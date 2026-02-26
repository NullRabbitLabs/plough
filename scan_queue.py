import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List

from config import Config
from scan_client import ScanClient, ScanClientError

logger = logging.getLogger(__name__)


@dataclass
class ScanRequest:
    pubkey: str
    network: str
    ips: List[str]
    metadata: dict
    queued_at: str
    reason: str


@dataclass
class ScanResult:
    status: str  # triggered | queued | skipped_cooldown | skipped_stake | skipped_no_ips | skipped_disabled
    ips: List[str] = field(default_factory=list)
    scan_ids: List[str] = field(default_factory=list)
    cdn_blocked_ips: List[str] = field(default_factory=list)
    queue_position: int = 0
    last_scan_at: str = ""


class ScanQueue:
    def __init__(self, config: Config, scan_client: ScanClient) -> None:
        self.config = config
        self.scan_client = scan_client
        self._state: dict = {"queued": [], "last_ferret_scan": {}}
        self._call_timestamps: List[datetime] = []

    def load(self) -> None:
        try:
            with open(self.config.scan_queue_path) as f:
                data = json.load(f)
            self._state = {
                "queued": data.get("queued", []),
                "last_ferret_scan": data.get("last_ferret_scan", {}),
            }
        except (FileNotFoundError, json.JSONDecodeError):
            self._state = {"queued": [], "last_ferret_scan": {}}

    def save(self) -> None:
        with open(self.config.scan_queue_path, "w") as f:
            json.dump(self._state, f, indent=2)

    def _within_rate_limit(self) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        return len(self._call_timestamps) < self.config.scan_rate_limit

    def _record_call(self) -> None:
        self._call_timestamps.append(datetime.now(timezone.utc))

    def _available_slots(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        return max(0, self.config.scan_rate_limit - len(self._call_timestamps))

    async def try_scan(
        self,
        pubkey: str,
        network: str,
        ips: List[str],
        stake: float,
        operator_name: str,
        metadata: dict,
    ) -> ScanResult:
        if not self.config.enable_auto_scan:
            return ScanResult(status="skipped_disabled")

        if not ips:
            return ScanResult(status="skipped_no_ips")

        if network == "solana" and stake < self.config.scan_min_stake_sol:
            return ScanResult(status="skipped_stake")
        if network == "sui" and stake < self.config.scan_min_stake_sui:
            return ScanResult(status="skipped_stake")

        last_scan = self._state["last_ferret_scan"].get(pubkey)
        if last_scan:
            last_dt = datetime.fromisoformat(last_scan)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - last_dt).total_seconds()
            if age < self.config.scan_cooldown:
                return ScanResult(status="skipped_cooldown", last_scan_at=last_scan)

        if not self._within_rate_limit():
            position = len(self._state["queued"]) + 1
            self._state["queued"].append({
                "pubkey": pubkey,
                "network": network,
                "ips": ips,
                "metadata": metadata,
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "reason": "rate_limited",
            })
            return ScanResult(status="queued", ips=ips, queue_position=position)

        return await self._do_submit(pubkey, network, ips, metadata)

    async def _do_submit(
        self, pubkey: str, network: str, ips: List[str], metadata: dict
    ) -> ScanResult:
        scan_ids = []
        cdn_blocked_ips = []
        submitted_ips = []

        for ip in ips:
            try:
                sub = await self.scan_client.submit(ip, metadata, protocol=network)
                self._record_call()
                submitted_ips.append(ip)
                if sub.cdn_blocked:
                    cdn_blocked_ips.append(ip)
                else:
                    scan_ids.append(sub.scan_id)
            except ScanClientError as e:
                logger.warning("Scan submission failed for %s/%s: %s", pubkey, ip, e)

        self._state["last_ferret_scan"][pubkey] = datetime.now(timezone.utc).isoformat()
        return ScanResult(
            status="triggered",
            ips=submitted_ips,
            scan_ids=scan_ids,
            cdn_blocked_ips=cdn_blocked_ips,
        )

    async def process_queue(self) -> None:
        if not self._state["queued"]:
            return

        available = self._available_slots()
        if available <= 0:
            return

        remaining = list(self._state["queued"])
        self._state["queued"] = []
        processed = 0

        for item in remaining:
            if processed >= available:
                self._state["queued"].append(item)
                continue
            pubkey = item["pubkey"]
            network = item["network"]
            ips = item["ips"]
            metadata = item.get("metadata", {})
            # How many IPs can we still submit?
            slots_left = available - processed
            batch_ips = ips[:slots_left]
            remainder_ips = ips[slots_left:]

            await self._do_submit(pubkey, network, batch_ips, metadata)
            processed += len(batch_ips)

            if remainder_ips:
                self._state["queued"].append({**item, "ips": remainder_ips})

import asyncio
import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from config import Config

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)

_CLUSTER_NODES_TTL = 60  # seconds


@dataclass
class ScanData:
    validator_pubkey: str
    network: str
    ip_addresses: List[str]
    findings: List[dict]
    scan_date: str


@dataclass
class EnrichedData:
    name: str = ""
    website: str = ""
    twitter: str = ""
    discord: str = ""
    telegram: Optional[str] = None
    email: Optional[str] = None
    keybase: str = ""
    ips: List[str] = field(default_factory=list)
    rdns: str = ""
    scan: Optional[ScanData] = None
    source: str = ""


class Enricher:
    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client
        self._known_operators: dict = {}
        self._stakewiz_cache: Dict[str, dict] = {}
        self._scan_index: Dict[str, ScanData] = {}
        self._cluster_nodes_cache: Dict[str, str] = {}  # identity -> ip (in-memory TTL)
        self._cluster_nodes_fetched_at: float = 0.0
        self._node_ip_cache: Dict[str, dict] = {}  # identity -> {ip, last_seen} (persistent)

    def load_known_operators(self) -> None:
        try:
            with open(self.config.operators_path) as f:
                data = json.load(f)
            self._known_operators = data.get("solana", data) if "solana" in data else data
        except (FileNotFoundError, json.JSONDecodeError):
            self._known_operators = {}

    def load_stakewiz_cache(self) -> None:
        try:
            with open(self.config.stakewiz_cache_path) as f:
                entries = json.load(f)
            self._stakewiz_cache = {e["vote_identity"]: e for e in entries if e.get("vote_identity")}
        except (FileNotFoundError, json.JSONDecodeError):
            self._stakewiz_cache = {}

    def load_node_ip_cache(self) -> None:
        try:
            with open(self.config.node_ip_cache_path) as f:
                self._node_ip_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._node_ip_cache = {}

    def _save_node_ip_cache(self) -> None:
        try:
            with open(self.config.node_ip_cache_path, "w") as f:
                json.dump(self._node_ip_cache, f)
        except Exception as e:
            logger.warning("Failed to save node IP cache: %s", e)

    def load_scan_index(self) -> None:
        try:
            with open(self.config.scanned_validators_path) as f:
                entries = json.load(f)
            self._scan_index = {}
            for e in entries:
                pubkey = e.get("validator_pubkey", "")
                if pubkey:
                    self._scan_index[pubkey] = ScanData(
                        validator_pubkey=pubkey,
                        network=e.get("network", ""),
                        ip_addresses=e.get("ip_addresses", []),
                        findings=e.get("findings", []),
                        scan_date=e.get("scan_date", ""),
                    )
        except (FileNotFoundError, json.JSONDecodeError):
            self._scan_index = {}

    async def enrich_solana(self, vote_account: str, identity: str) -> EnrichedData:
        data = EnrichedData()

        # Priority 1: known_operators["solana"]
        known = self._known_operators.get(vote_account)
        if known is not None:
            if isinstance(known, dict):
                data.name = known.get("name", "")
                data.website = known.get("website", "")
                data.twitter = known.get("twitter", "")
                data.discord = known.get("discord", "")
                data.telegram = known.get("telegram")
                data.email = known.get("email")
                data.keybase = known.get("keybase", "")
                data.ips = known.get("ips", [])
            else:
                data.name = str(known)
            data.source = "known_operators"
        else:
            # Priority 2: stakewiz cache
            cached = self._stakewiz_cache.get(vote_account)
            if cached is not None:
                data.name = cached.get("name", "")
                data.website = cached.get("website", "")
                data.keybase = cached.get("keybase", "")
                data.source = "stakewiz"

        # Priority 3: if no IP, fetch from cluster nodes
        if not data.ips:
            ip = await self._fetch_cluster_node_ip(identity)
            if ip:
                data.ips = [ip]

        # Priority 4: reverse DNS for first IP
        if data.ips and not data.rdns:
            data.rdns = await self._reverse_dns(data.ips[0])

        # Always attach scan data
        data.scan = self._scan_index.get(vote_account)

        return data

    async def _refresh_cluster_nodes(self) -> None:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getClusterNodes", "params": []}
        try:
            resp = await self.client.post(self.config.sol_rpc_url, json=payload, timeout=10)
            resp.raise_for_status()
            nodes = resp.json().get("result", [])
            self._cluster_nodes_cache = {
                node["pubkey"]: node["gossip"].split(":")[0]
                for node in nodes
                if node.get("gossip")
            }
            self._cluster_nodes_fetched_at = time.monotonic()
            now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            for identity, ip in self._cluster_nodes_cache.items():
                self._node_ip_cache[identity] = {"ip": ip, "last_seen": now}
            self._save_node_ip_cache()
        except Exception as e:
            logger.warning("getClusterNodes failed: %s", e)

    async def snapshot_cluster_nodes(self) -> None:
        """Force a fresh getClusterNodes fetch and persist results. Call each poll cycle."""
        self._cluster_nodes_fetched_at = 0.0
        await self._refresh_cluster_nodes()

    async def _fetch_cluster_node_ip(self, identity: str) -> str:
        if time.monotonic() - self._cluster_nodes_fetched_at > _CLUSTER_NODES_TTL:
            await self._refresh_cluster_nodes()
        ip = self._cluster_nodes_cache.get(identity, "")
        if not ip:
            ip = self._node_ip_cache.get(identity, {}).get("ip", "")
        return ip

    async def _reverse_dns(self, ip: str) -> str:
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(_executor, socket.gethostbyaddr, ip),
                timeout=2.0,
            )
            return result[0]
        except Exception:
            return ""

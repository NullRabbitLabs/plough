import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import httpx

from config import Config
from enrichment import Enricher, ScanData
from scan_queue import ScanQueue, ScanResult
from state import State

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000


@dataclass
class SolValidator:
    identity: str
    vote_account: str
    activated_stake_sol: float
    commission: int
    last_vote: int
    root_slot: int
    name: str = ""
    website: str = ""
    keybase: str = ""
    twitter: str = ""
    discord: str = ""
    ips: List[str] = field(default_factory=list)
    rdns: str = ""
    scan: Optional[ScanData] = None


class SolMonitor:
    def __init__(
        self,
        config: Config,
        state: State,
        alerter,
        client: httpx.AsyncClient,
        enricher: Optional[Enricher] = None,
        scan_queue: Optional[ScanQueue] = None,
    ) -> None:
        self.config = config
        self.state = state
        self.alerter = alerter
        self.client = client
        self.enricher = enricher
        self.scan_queue = scan_queue

    def parse_delinquent(self, data: dict) -> List[SolValidator]:
        validators = []
        for item in data.get("result", {}).get("delinquent", []):
            validators.append(
                SolValidator(
                    identity=item["nodePubkey"],
                    vote_account=item["votePubkey"],
                    activated_stake_sol=item["activatedStake"] / LAMPORTS_PER_SOL,
                    commission=item["commission"],
                    last_vote=item["lastVote"],
                    root_slot=item["rootSlot"],
                )
            )
        return validators

    def filter_by_stake(self, validators: List[SolValidator]) -> List[SolValidator]:
        return [v for v in validators if v.activated_stake_sol >= self.config.sol_stake_threshold_sol]

    def find_new_delinquent(self, validators: List[SolValidator], state: State) -> List[SolValidator]:
        previous = state.get_previous_delinquent()
        return [v for v in validators if v.vote_account not in previous]

    async def process_delinquent(self, validators: List[SolValidator], state: State) -> None:
        current = {v.vote_account for v in validators}
        previous = state.get_previous_delinquent()
        candidates = state.get_candidate_delinquent()

        # Validators newly delinquent this poll (not seen in previous poll)
        newly_delinquent = current - previous

        # Validators confirmed delinquent: were candidates last poll AND still delinquent
        confirmed = candidates & current

        to_alert = [v for v in validators if v.vote_account in confirmed]
        filtered = self.filter_by_stake(to_alert)
        if filtered:
            is_mass = len(filtered) >= self.config.sol_mass_event_threshold
            scan_results = await self._trigger_scans(filtered, is_mass)
            await self.alerter.alert_sol_delinquent(filtered, is_mass=is_mass, scan_results=scan_results)

        # Newly delinquent validators become candidates for next poll
        state.set_previous_delinquent(current)
        state.set_candidate_delinquent(newly_delinquent)
        state.save()

    async def _trigger_scans(self, validators: List[SolValidator], is_mass: bool) -> Dict[str, ScanResult]:
        if self.scan_queue is None:
            return {}
        results: Dict[str, ScanResult] = {}
        incident_time = datetime.now(timezone.utc).isoformat()

        if is_mass:
            sorted_validators = sorted(validators, key=lambda v: v.activated_stake_sol, reverse=True)
            top = sorted_validators[:5]
            rest = sorted_validators[5:]
        else:
            top = validators
            rest = []

        for v in top:
            metadata = {
                "source": "ferret",
                "trigger": "solana_delinquency",
                "validator_pubkey": v.vote_account,
                "validator_name": v.name,
                "network": "solana",
                "stake": str(int(v.activated_stake_sol)),
                "incident_time": incident_time,
                "event_type": "delinquent",
                "details": f"Commission {v.commission}%, last vote {v.last_vote}",
            }
            try:
                result = await self.scan_queue.try_scan(
                    v.vote_account, "solana", v.ips, v.activated_stake_sol, v.name, metadata
                )
            except Exception as e:
                logger.warning("Scan trigger failed for %s: %s", v.vote_account, e)
                continue
            results[v.vote_account] = result

        for v in rest:
            metadata = {
                "source": "ferret",
                "trigger": "solana_delinquency",
                "validator_pubkey": v.vote_account,
                "validator_name": v.name,
                "network": "solana",
                "stake": str(int(v.activated_stake_sol)),
                "incident_time": incident_time,
                "event_type": "delinquent",
                "details": f"Commission {v.commission}%, last vote {v.last_vote}",
            }
            # Force-queue rest by temporarily exhausting rate limit
            if self.scan_queue._within_rate_limit():
                # Append to queue state directly to avoid retrying the submit path
                self.scan_queue._state["queued"].append({
                    "pubkey": v.vote_account,
                    "network": "solana",
                    "ips": v.ips,
                    "metadata": metadata,
                    "queued_at": incident_time,
                    "reason": "mass_event_overflow",
                })
                results[v.vote_account] = ScanResult(
                    status="queued",
                    ips=v.ips,
                    queue_position=len(self.scan_queue._state["queued"]),
                )
            else:
                try:
                    result = await self.scan_queue.try_scan(
                        v.vote_account, "solana", v.ips, v.activated_stake_sol, v.name, metadata
                    )
                except Exception as e:
                    logger.warning("Scan trigger failed for %s: %s", v.vote_account, e)
                    continue
                results[v.vote_account] = result

        return results

    async def enrich_validators(self, validators: List[SolValidator]) -> None:
        if self.enricher is None:
            return
        for v in validators:
            try:
                data = await self.enricher.enrich_solana(v.vote_account, v.identity)
                v.name = data.name
                v.website = data.website
                v.keybase = data.keybase
                v.twitter = data.twitter
                v.discord = data.discord
                v.ips = data.ips
                v.rdns = data.rdns
                v.scan = data.scan
            except Exception as e:
                logger.warning("Enrichment failed for %s: %s", v.vote_account, e)

    async def fetch_vote_accounts(self) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getVoteAccounts",
            "params": [],
        }
        resp = await self.client.post(self.config.sol_rpc_url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def run(self) -> None:
        if not self.config.sol_rpc_url:
            logger.error(
                "SOL_RPC_URL is not set. getVoteAccounts is blocked by all free public RPCs. "
                "Get a free key at https://helius.dev and set SOL_RPC_URL=https://mainnet.helius-rpc.com/?api-key=YOUR_KEY"
            )
            return
        while True:
            try:
                if self.scan_queue is not None:
                    await self.scan_queue.process_queue()
                if self.enricher is not None:
                    await self.enricher.snapshot_cluster_nodes()
                data = await self.fetch_vote_accounts()
                validators = self.parse_delinquent(data)
                await self.enrich_validators(validators)
                await self.process_delinquent(validators, self.state)
            except Exception as e:
                logger.error("SOL monitor error: %s", e)
            await asyncio.sleep(self.config.poll_interval_sol)

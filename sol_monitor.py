import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set

import httpx

from config import Config
from state import State

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000
STAKEWIZ_URL = "https://api.stakewiz.com/validators"


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


class SolMonitor:
    def __init__(
        self,
        config: Config,
        state: State,
        alerter,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self.state = state
        self.alerter = alerter
        self.client = client

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
        new_delinquent = self.find_new_delinquent(validators, state)
        filtered = self.filter_by_stake(new_delinquent)

        if not filtered:
            return

        is_mass = len(filtered) >= self.config.sol_mass_event_threshold
        await self.alerter.alert_sol_delinquent(filtered, is_mass=is_mass)

        current_vote_accounts = {v.vote_account for v in validators}
        state.set_previous_delinquent(current_vote_accounts)
        state.save()

    async def enrich_validators(self, validators: List[SolValidator]) -> None:
        try:
            resp = await self.client.get(STAKEWIZ_URL, timeout=10)
            resp.raise_for_status()
            registry: Dict[str, dict] = {
                v["vote_account"]: v
                for v in resp.json()
                if v.get("vote_account")
            }
        except Exception as e:
            logger.warning("Could not fetch validator registry: %s", e)
            return
        for v in validators:
            info = registry.get(v.vote_account, {})
            v.name = info.get("name", "")
            v.website = info.get("www_url", "")
            v.keybase = info.get("keybase_id", "")

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
                data = await self.fetch_vote_accounts()
                validators = self.parse_delinquent(data)
                await self.enrich_validators(validators)
                await self.process_delinquent(validators, self.state)
            except Exception as e:
                logger.error("SOL monitor error: %s", e)
            await asyncio.sleep(self.config.poll_interval_sol)

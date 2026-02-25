import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Set

import httpx

from config import Config
from state import State

logger = logging.getLogger(__name__)


@dataclass
class SuiValidator:
    name: str
    sui_address: str
    stake_amount: int
    next_epoch_stake: int


class SuiMonitor:
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

    def parse_validators(self, data: dict) -> List[SuiValidator]:
        validators = []
        for item in data.get("result", {}).get("activeValidators", []):
            validators.append(
                SuiValidator(
                    name=item["name"],
                    sui_address=item["suiAddress"],
                    stake_amount=int(item["stakingPoolSuiBalance"]),
                    next_epoch_stake=int(item["nextEpochStake"]),
                )
            )
        return validators

    def _stake_drop_fraction(self, previous_stake: int, next_epoch_stake: int) -> float:
        if previous_stake == 0:
            return 0.0
        return (previous_stake - next_epoch_stake) / previous_stake

    async def process_validators(self, validators: List[SuiValidator], state: State) -> None:
        previous_addresses = state.get_previous_sui_addresses()
        previous_stakes = state.get_previous_sui_stakes()
        current_addresses = {v.sui_address for v in validators}

        # Alert for validators that dropped out of the active set
        dropped_addresses = previous_addresses - current_addresses
        for address in dropped_addresses:
            dropped = SuiValidator(
                name=f"Unknown ({address})",
                sui_address=address,
                stake_amount=previous_stakes.get(address, 0),
                next_epoch_stake=0,
            )
            await self.alerter.alert_sui_drop(dropped)

        # Alert for validators with significant stake drops
        for validator in validators:
            if validator.sui_address not in previous_stakes:
                continue
            prev_stake = previous_stakes[validator.sui_address]
            drop = self._stake_drop_fraction(prev_stake, validator.next_epoch_stake)
            if drop >= self.config.sui_stake_drop_threshold:
                await self.alerter.alert_sui_drop(validator)

        state.set_previous_sui_addresses(current_addresses)
        state.set_previous_sui_stakes({v.sui_address: v.stake_amount for v in validators})
        state.save()

    async def fetch_system_state(self) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "suix_getLatestSuiSystemState",
            "params": [],
        }
        resp = await self.client.post(self.config.sui_rpc_url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def run(self) -> None:
        while True:
            try:
                data = await self.fetch_system_state()
                validators = self.parse_validators(data)
                await self.process_validators(validators, self.state)
            except Exception as e:
                logger.error("SUI monitor error: %s", e)
            await asyncio.sleep(self.config.poll_interval_sui)

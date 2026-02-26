import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

from config import Config
from state import State

logger = logging.getLogger(__name__)


@dataclass
class CosmosValidator:
    operator_address: str
    moniker: str
    status: str  # BOND_STATUS_BONDED | BOND_STATUS_UNBONDING | BOND_STATUS_UNBONDED
    jailed: bool
    tokens: int  # stake in uatom


class CosmosMonitor:
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

    def parse_validator(self, data: dict) -> CosmosValidator:
        v = data["validator"]
        return CosmosValidator(
            operator_address=v["operator_address"],
            moniker=v["description"]["moniker"],
            status=v["status"],
            jailed=v.get("jailed", False),
            tokens=int(v.get("tokens", 0)),
        )

    async def fetch_validator(self, valoper: str) -> CosmosValidator:
        url = f"{self.config.cosmos_rest_url}/cosmos/staking/v1beta1/validators/{valoper}"
        resp = await self.client.get(url)
        resp.raise_for_status()
        return self.parse_validator(resp.json())

    async def poll(self) -> None:
        previous = self.state.get_previous_cosmos_status()
        updated: Dict[str, dict] = {}

        for valoper in self.config.cosmos_validators:
            try:
                validator = await self.fetch_validator(valoper)
            except Exception as e:
                logger.error("Cosmos: failed to fetch %s: %s", valoper, e)
                continue

            prev = previous.get(valoper)
            if prev is not None:
                if validator.jailed and not prev.get("jailed", False):
                    await self.alerter.alert_cosmos_jailed(validator)
                elif (
                    validator.status != "BOND_STATUS_BONDED"
                    and prev.get("status") == "BOND_STATUS_BONDED"
                ):
                    await self.alerter.alert_cosmos_inactive(validator)

            updated[valoper] = {"jailed": validator.jailed, "status": validator.status}

        self.state.set_previous_cosmos_status(updated)
        self.state.save()

    async def run(self) -> None:
        if not self.config.cosmos_validators:
            logger.info("COSMOS_VALIDATORS not set, Cosmos monitor disabled")
            return
        while True:
            try:
                await self.poll()
            except Exception as e:
                logger.error("Cosmos monitor error: %s", e)
            await asyncio.sleep(self.config.poll_interval_cosmos)

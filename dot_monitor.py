import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

from config import Config
from state import State

logger = logging.getLogger(__name__)


@dataclass
class DotValidator:
    stash: str
    display: str
    is_elected: bool
    commission: float
    bonded: int  # total bonded in Planck


@dataclass
class DotSlashEvent:
    stash: str
    amount: int  # Planck
    block_num: int
    event_index: str  # unique ID for dedup


class DotMonitor:
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

    def _subscan_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.dot_subscan_api_key:
            headers["X-API-Key"] = self.config.dot_subscan_api_key
        return headers

    def parse_validators(self, data: dict) -> List[DotValidator]:
        validators = []
        for item in data.get("data", {}).get("list", []) or []:
            display_info = item.get("stash_account_display", {})
            stash = display_info.get("address", "")
            display = display_info.get("display", stash)
            validators.append(
                DotValidator(
                    stash=stash,
                    display=display,
                    is_elected=bool(item.get("is_elected", False)),
                    commission=float(item.get("validator_prefs_value", 0)) / 1e7,
                    bonded=int(item.get("bonded_total", 0)),
                )
            )
        return validators

    def parse_slash_events(self, data: dict) -> List[DotSlashEvent]:
        events = []
        for item in data.get("data", {}).get("events", []) or []:
            params = item.get("params", [])
            stash = ""
            amount = 0
            for p in params:
                if p.get("type_name") == "T::AccountId" or p.get("name") == "stash":
                    stash = p.get("value", "")
                elif p.get("type_name") == "BalanceOf<T>" or p.get("name") == "amount":
                    amount = int(p.get("value", 0))
            event_index = item.get("event_index", "")
            block_num = item.get("block_num", 0)
            if stash:
                events.append(
                    DotSlashEvent(
                        stash=stash,
                        amount=amount,
                        block_num=block_num,
                        event_index=event_index,
                    )
                )
        return events

    async def fetch_validators(self) -> List[DotValidator]:
        url = f"{self.config.dot_subscan_url}/api/scan/staking/validators"
        payload = {
            "row": 100,
            "page": 0,
            "order": "desc",
            "order_field": "bonded_nominators_count",
        }
        resp = await self.client.post(url, json=payload, headers=self._subscan_headers())
        resp.raise_for_status()
        return self.parse_validators(resp.json())

    async def fetch_slash_events(self) -> List[DotSlashEvent]:
        url = f"{self.config.dot_subscan_url}/api/scan/events"
        payload = {"module": "staking", "call": "Slash", "row": 25, "page": 0}
        resp = await self.client.post(url, json=payload, headers=self._subscan_headers())
        resp.raise_for_status()
        return self.parse_slash_events(resp.json())

    async def poll_inactive(self, validators: List[DotValidator]) -> None:
        previous_active = set(self.state.get_previous_dot_active())
        configured = set(self.config.dot_validators)
        elected_stashes = {v.stash for v in validators if v.is_elected}
        validator_map = {v.stash: v for v in validators}

        for stash in configured:
            was_active = stash in previous_active
            is_active = stash in elected_stashes
            if was_active and not is_active:
                # Build a placeholder if the stash isn't in the current list
                v = validator_map.get(
                    stash,
                    DotValidator(stash=stash, display=stash, is_elected=False, commission=0.0, bonded=0),
                )
                await self.alerter.alert_dot_inactive(v)

        self.state.set_previous_dot_active(list(elected_stashes & configured))
        self.state.save()

    async def poll_slashing(self) -> None:
        events = await self.fetch_slash_events()
        configured = set(self.config.dot_validators)
        for event in events:
            if event.stash not in configured:
                continue
            event_key = f"dot_slash_{event.event_index}"
            if self.state.is_seen(event_key):
                continue
            # Build a placeholder validator
            v = DotValidator(
                stash=event.stash,
                display=event.stash,
                is_elected=False,
                commission=0.0,
                bonded=0,
            )
            await self.alerter.alert_dot_slashed(v, event)
            self.state.mark_seen(event_key)
            self.state.save()

    async def poll(self) -> None:
        validators = await self.fetch_validators()
        await self.poll_inactive(validators)
        await self.poll_slashing()

    async def run(self) -> None:
        if not self.config.dot_validators:
            logger.info("DOT_VALIDATORS not set, Polkadot monitor disabled")
            return
        while True:
            try:
                await self.poll()
            except Exception as e:
                logger.error("Polkadot monitor error: %s", e)
            await asyncio.sleep(self.config.poll_interval_dot)

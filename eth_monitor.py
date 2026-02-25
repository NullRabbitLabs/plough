import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import httpx

from config import Config
from state import State

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000


@dataclass
class EthSlashingEvent:
    validator_index: int
    slashed_by: int
    slash_type: str  # "attester" or "proposer"
    epoch: int
    slot: int
    operator_name: str

    @property
    def event_id(self) -> str:
        return f"eth_slash_{self.validator_index}_{self.slot}"


class EthMonitor:
    def __init__(
        self,
        config: Config,
        state: State,
        alerter,
        client: httpx.AsyncClient,
        operators_path: Optional[str] = None,
    ) -> None:
        self.config = config
        self.state = state
        self.alerter = alerter
        self.client = client
        self._operators_path = operators_path or config.operators_path
        self._known_operators = self._load_operators()

    def _load_operators(self) -> dict:
        import json
        try:
            with open(self._operators_path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def resolve_operator(self, validator_index: int) -> str:
        key = str(validator_index)
        if key in self._known_operators:
            return self._known_operators[key]
        return f"Validator #{validator_index}"

    def parse_beaconcha_slashings(self, data: dict) -> List[EthSlashingEvent]:
        events = []
        for item in data.get("data", []):
            events.append(
                EthSlashingEvent(
                    validator_index=item["validatorindex"],
                    slashed_by=item.get("slashedby", 0),
                    slash_type=item.get("slashtype", "unknown"),
                    epoch=item["epoch"],
                    slot=item["slot"],
                    operator_name=self.resolve_operator(item["validatorindex"]),
                )
            )
        return events

    def parse_attester_slashings(self, data: dict) -> List[EthSlashingEvent]:
        events = []
        for item in data.get("data", []):
            att1 = item["attestation_1"]
            indices = att1["data"]
            slot = int(att1["data"]["slot"])
            # The first attesting index is the slashed validator
            raw_indices = item["attestation_1"]["attesting_indices"]
            validator_index = int(raw_indices[0])
            events.append(
                EthSlashingEvent(
                    validator_index=validator_index,
                    slashed_by=0,
                    slash_type="attester",
                    epoch=0,
                    slot=slot,
                    operator_name=self.resolve_operator(validator_index),
                )
            )
        return events

    def parse_proposer_slashings(self, data: dict) -> List[EthSlashingEvent]:
        events = []
        for item in data.get("data", []):
            header = item["signed_header_1"]["message"]
            slot = int(header["slot"])
            validator_index = int(header["proposer_index"])
            events.append(
                EthSlashingEvent(
                    validator_index=validator_index,
                    slashed_by=0,
                    slash_type="proposer",
                    epoch=0,
                    slot=slot,
                    operator_name=self.resolve_operator(validator_index),
                )
            )
        return events

    async def fetch_slashings(self) -> List[EthSlashingEvent]:
        try:
            return await self.fetch_fallback_slashings()
        except Exception as e:
            logger.error("ETH beacon pool fetch failed: %s", e)
            return []

    async def fetch_fallback_slashings(self) -> List[EthSlashingEvent]:
        base = self.config.eth_beacon_node_url
        events: List[EthSlashingEvent] = []
        for path, parser in [
            ("/eth/v1/beacon/pool/attester_slashings", self.parse_attester_slashings),
            ("/eth/v1/beacon/pool/proposer_slashings", self.parse_proposer_slashings),
        ]:
            try:
                resp = await self.client.get(f"{base}{path}")
                resp.raise_for_status()
                events.extend(parser(resp.json()))
            except Exception as e:
                logger.error("ETH fetch failed for %s: %s", path, e)
        return events

    async def process_events(self, events: List[EthSlashingEvent]) -> None:
        for event in events:
            if self.state.is_seen(event.event_id):
                logger.debug("Skipping already-seen event %s", event.event_id)
                continue
            await self.alerter.alert_eth_slashing(event)
            self.state.mark_seen(event.event_id)
            self.state.save()

    async def run(self) -> None:
        while True:
            try:
                events = await self.fetch_slashings()
                await self.process_events(events)
            except Exception as e:
                logger.error("ETH monitor error: %s", e)
            await asyncio.sleep(self.config.poll_interval_eth)

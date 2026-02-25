import json
import time
from typing import Dict, List, Set


class State:
    def __init__(self, path: str) -> None:
        self._path = path
        self._data: Dict = {
            "seen_events": [],
            "alert_times": {},
            "previous_delinquent": [],
            "previous_sui_addresses": [],
            "previous_sui_stakes": {},
        }

    def load(self) -> None:
        try:
            with open(self._path) as f:
                loaded = json.load(f)
            self._data["seen_events"] = loaded.get("seen_events", [])
            self._data["alert_times"] = loaded.get("alert_times", {})
            self._data["previous_delinquent"] = loaded.get("previous_delinquent", [])
            self._data["previous_sui_addresses"] = loaded.get("previous_sui_addresses", [])
            self._data["previous_sui_stakes"] = loaded.get("previous_sui_stakes", {})
        except FileNotFoundError:
            pass

    def save(self) -> None:
        with open(self._path, "w") as f:
            json.dump(self._data, f)

    def is_seen(self, event_id: str) -> bool:
        return event_id in self._data["seen_events"]

    def mark_seen(self, event_id: str) -> None:
        if event_id not in self._data["seen_events"]:
            self._data["seen_events"].append(event_id)

    def is_on_cooldown(self, validator_id: str, cooldown_seconds: int) -> bool:
        last = self._data["alert_times"].get(validator_id)
        if last is None:
            return False
        return (time.time() - last) < cooldown_seconds

    def record_alert(self, validator_id: str) -> None:
        self._data["alert_times"][validator_id] = time.time()

    def get_previous_delinquent(self) -> Set[str]:
        return set(self._data["previous_delinquent"])

    def set_previous_delinquent(self, vote_accounts: Set[str]) -> None:
        self._data["previous_delinquent"] = list(vote_accounts)

    def get_previous_sui_addresses(self) -> Set[str]:
        return set(self._data["previous_sui_addresses"])

    def set_previous_sui_addresses(self, addresses: Set[str]) -> None:
        self._data["previous_sui_addresses"] = list(addresses)

    def get_previous_sui_stakes(self) -> Dict[str, int]:
        return dict(self._data["previous_sui_stakes"])

    def set_previous_sui_stakes(self, stakes: Dict[str, int]) -> None:
        self._data["previous_sui_stakes"] = dict(stakes)

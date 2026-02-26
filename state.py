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
            "candidate_delinquent": [],
            "last_eth_slot": 0,
            "previous_sui_addresses": [],
            "previous_sui_stakes": {},
            "previous_cosmos_status": {},
            "previous_dot_active": [],
            "last_dot_slash_id": "",
        }

    def load(self) -> None:
        try:
            with open(self._path) as f:
                loaded = json.load(f)
            self._data["seen_events"] = loaded.get("seen_events", [])
            self._data["alert_times"] = loaded.get("alert_times", {})
            self._data["previous_delinquent"] = loaded.get("previous_delinquent", [])
            self._data["candidate_delinquent"] = loaded.get("candidate_delinquent", [])
            self._data["last_eth_slot"] = loaded.get("last_eth_slot", 0)
            self._data["previous_sui_addresses"] = loaded.get("previous_sui_addresses", [])
            self._data["previous_sui_stakes"] = loaded.get("previous_sui_stakes", {})
            self._data["previous_cosmos_status"] = loaded.get("previous_cosmos_status", {})
            self._data["previous_dot_active"] = loaded.get("previous_dot_active", [])
            self._data["last_dot_slash_id"] = loaded.get("last_dot_slash_id", "")
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

    def get_candidate_delinquent(self) -> Set[str]:
        return set(self._data.get("candidate_delinquent", []))

    def set_candidate_delinquent(self, vote_accounts: Set[str]) -> None:
        self._data["candidate_delinquent"] = list(vote_accounts)

    def get_last_eth_slot(self) -> int:
        return self._data.get("last_eth_slot", 0)

    def set_last_eth_slot(self, slot: int) -> None:
        self._data["last_eth_slot"] = slot

    def get_previous_cosmos_status(self) -> Dict:
        return dict(self._data.get("previous_cosmos_status", {}))

    def set_previous_cosmos_status(self, status: Dict) -> None:
        self._data["previous_cosmos_status"] = dict(status)

    def get_previous_dot_active(self) -> List:
        return list(self._data.get("previous_dot_active", []))

    def set_previous_dot_active(self, stashes: List) -> None:
        self._data["previous_dot_active"] = list(stashes)

    def get_last_dot_slash_id(self) -> str:
        return self._data.get("last_dot_slash_id", "")

    def set_last_dot_slash_id(self, event_id: str) -> None:
        self._data["last_dot_slash_id"] = event_id

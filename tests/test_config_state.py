import json
import time
import pytest
from config import Config
from state import State


class TestConfig:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.delenv("POLL_INTERVAL_ETH", raising=False)
        monkeypatch.delenv("POLL_INTERVAL_SOL", raising=False)
        monkeypatch.delenv("POLL_INTERVAL_SUI", raising=False)
        monkeypatch.delenv("ETH_BEACON_API_KEY", raising=False)
        cfg = Config.from_env()
        assert cfg.telegram_bot_token == ""
        assert cfg.telegram_chat_id == ""
        assert cfg.poll_interval_eth == 60
        assert cfg.poll_interval_sol == 30
        assert cfg.poll_interval_sui == 60
        assert cfg.eth_beacon_api_key == ""
        assert cfg.sol_rpc_url == ""
        assert cfg.eth_beacon_node_url == "https://ethereum-beacon-api.publicnode.com"

    def test_eth_beacon_api_key(self, monkeypatch):
        monkeypatch.setenv("ETH_BEACON_API_KEY", "mykey123")
        cfg = Config.from_env()
        assert cfg.eth_beacon_api_key == "mykey123"

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "my-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123456")
        monkeypatch.setenv("POLL_INTERVAL_ETH", "120")
        monkeypatch.setenv("SOL_STAKE_THRESHOLD_SOL", "500")
        cfg = Config.from_env()
        assert cfg.telegram_bot_token == "my-token"
        assert cfg.telegram_chat_id == "-100123456"
        assert cfg.poll_interval_eth == 120
        assert cfg.sol_stake_threshold_sol == 500

    def test_quiet_hours_defaults_to_none(self, monkeypatch):
        monkeypatch.delenv("QUIET_HOURS_START", raising=False)
        monkeypatch.delenv("QUIET_HOURS_END", raising=False)
        cfg = Config.from_env()
        assert cfg.quiet_hours_start is None
        assert cfg.quiet_hours_end is None

    def test_quiet_hours_set(self, monkeypatch):
        monkeypatch.setenv("QUIET_HOURS_START", "22")
        monkeypatch.setenv("QUIET_HOURS_END", "7")
        cfg = Config.from_env()
        assert cfg.quiet_hours_start == 22
        assert cfg.quiet_hours_end == 7


class TestState:
    def test_is_seen_returns_false_for_new_event(self, tmp_state_path):
        s = State(tmp_state_path)
        assert s.is_seen("evt_001") is False

    def test_mark_seen_and_is_seen(self, tmp_state_path):
        s = State(tmp_state_path)
        s.mark_seen("evt_001")
        assert s.is_seen("evt_001") is True

    def test_is_seen_false_for_different_event(self, tmp_state_path):
        s = State(tmp_state_path)
        s.mark_seen("evt_001")
        assert s.is_seen("evt_002") is False

    def test_cooldown_not_active_initially(self, tmp_state_path):
        s = State(tmp_state_path)
        assert s.is_on_cooldown("validator_abc", 3600) is False

    def test_cooldown_active_after_record(self, tmp_state_path):
        s = State(tmp_state_path)
        s.record_alert("validator_abc")
        assert s.is_on_cooldown("validator_abc", 3600) is True

    def test_cooldown_expired(self, tmp_state_path):
        s = State(tmp_state_path)
        s.record_alert("validator_abc")
        # manually set the time to the past
        s._data["alert_times"]["validator_abc"] = time.time() - 7200
        assert s.is_on_cooldown("validator_abc", 3600) is False

    def test_persist_and_reload(self, tmp_state_path):
        s = State(tmp_state_path)
        s.mark_seen("evt_persist")
        s.record_alert("val_persist")
        s.save()

        s2 = State(tmp_state_path)
        s2.load()
        assert s2.is_seen("evt_persist") is True
        assert s2.is_on_cooldown("val_persist", 3600) is True

    def test_load_creates_empty_state_if_no_file(self, tmp_state_path):
        s = State(tmp_state_path)
        s.load()  # file doesn't exist yet
        assert s.is_seen("anything") is False

    def test_save_creates_valid_json(self, tmp_state_path):
        s = State(tmp_state_path)
        s.mark_seen("e1")
        s.record_alert("v1")
        s.save()
        with open(tmp_state_path) as f:
            data = json.load(f)
        assert "e1" in data["seen_events"]
        assert "v1" in data["alert_times"]

    def test_previous_delinquent_roundtrip(self, tmp_state_path):
        s = State(tmp_state_path)
        s.set_previous_delinquent({"voteA", "voteB"})
        s.save()
        s2 = State(tmp_state_path)
        s2.load()
        assert s2.get_previous_delinquent() == {"voteA", "voteB"}

    def test_previous_sui_addresses_roundtrip(self, tmp_state_path):
        s = State(tmp_state_path)
        s.set_previous_sui_addresses({"0xaaa", "0xbbb"})
        s.save()
        s2 = State(tmp_state_path)
        s2.load()
        assert s2.get_previous_sui_addresses() == {"0xaaa", "0xbbb"}

    def test_previous_sui_stakes_roundtrip(self, tmp_state_path):
        s = State(tmp_state_path)
        s.set_previous_sui_stakes({"0xaaa": 1000, "0xbbb": 2000})
        s.save()
        s2 = State(tmp_state_path)
        s2.load()
        assert s2.get_previous_sui_stakes() == {"0xaaa": 1000, "0xbbb": 2000}

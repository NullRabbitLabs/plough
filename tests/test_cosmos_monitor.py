import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from config import Config
from state import State
from alerter import Alerter
from cosmos_monitor import CosmosMonitor, CosmosValidator

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setenv("COSMOS_VALIDATORS", "cosmosvaloper1abc123,cosmosvaloper1def456")
    monkeypatch.setenv("COSMOS_REST_URL", "https://api.cosmos.network")
    monkeypatch.delenv("QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("QUIET_HOURS_END", raising=False)
    return Config.from_env()


@pytest.fixture
def config_no_validators(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setenv("COSMOS_VALIDATORS", "")
    monkeypatch.delenv("QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("QUIET_HOURS_END", raising=False)
    return Config.from_env()


@pytest.fixture
def state(tmp_state_path):
    return State(tmp_state_path)


@pytest.fixture
def mock_alerter():
    alerter = MagicMock()
    alerter.alert_cosmos_jailed = AsyncMock()
    alerter.alert_cosmos_inactive = AsyncMock()
    return alerter


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def monitor(config, state, mock_alerter, mock_client):
    return CosmosMonitor(config, state, mock_alerter, mock_client)


@pytest.fixture
def bonded_response():
    return {
        "validator": {
            "operator_address": "cosmosvaloper1abc123",
            "description": {"moniker": "MyCosmosNode"},
            "status": "BOND_STATUS_BONDED",
            "jailed": False,
            "tokens": "1000000000000",
        }
    }


@pytest.fixture
def jailed_response():
    return {
        "validator": {
            "operator_address": "cosmosvaloper1abc123",
            "description": {"moniker": "MyCosmosNode"},
            "status": "BOND_STATUS_BONDED",
            "jailed": True,
            "tokens": "1000000000000",
        }
    }


@pytest.fixture
def unbonding_response():
    return {
        "validator": {
            "operator_address": "cosmosvaloper1abc123",
            "description": {"moniker": "MyCosmosNode"},
            "status": "BOND_STATUS_UNBONDING",
            "jailed": False,
            "tokens": "1000000000000",
        }
    }


class TestParseValidator:
    def test_parse_bonded_validator(self, monitor, bonded_response):
        v = monitor.parse_validator(bonded_response)
        assert v.operator_address == "cosmosvaloper1abc123"
        assert v.moniker == "MyCosmosNode"
        assert v.status == "BOND_STATUS_BONDED"
        assert v.jailed is False
        assert v.tokens == 1000000000000

    def test_parse_jailed_validator(self, monitor, jailed_response):
        v = monitor.parse_validator(jailed_response)
        assert v.jailed is True

    def test_parse_unbonding_validator(self, monitor, unbonding_response):
        v = monitor.parse_validator(unbonding_response)
        assert v.status == "BOND_STATUS_UNBONDING"
        assert v.jailed is False

    def test_parse_fixture_file(self, monitor):
        import json
        with open(FIXTURES_DIR / "cosmos_validator.json") as f:
            data = json.load(f)
        v = monitor.parse_validator(data)
        assert v.operator_address == "cosmosvaloper1abc123"
        assert v.moniker == "MyCosmosNode"
        assert v.status == "BOND_STATUS_BONDED"


class TestPollLogic:
    @pytest.mark.asyncio
    async def test_no_alert_on_first_poll(self, monitor, state, mock_alerter, bonded_response):
        mock_resp = MagicMock()
        mock_resp.json.return_value = bonded_response
        mock_resp.raise_for_status = MagicMock()
        monitor.client.get = AsyncMock(return_value=mock_resp)

        await monitor.poll()

        mock_alerter.alert_cosmos_jailed.assert_not_called()
        mock_alerter.alert_cosmos_inactive.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_on_jailing_transition(self, monitor, state, mock_alerter, jailed_response):
        # Set previous state as not jailed
        state.set_previous_cosmos_status({
            "cosmosvaloper1abc123": {"jailed": False, "status": "BOND_STATUS_BONDED"},
            "cosmosvaloper1def456": {"jailed": False, "status": "BOND_STATUS_BONDED"},
        })

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        def make_response(response_data):
            r = MagicMock()
            r.json.return_value = response_data
            r.raise_for_status = MagicMock()
            return r

        jailed_resp = make_response(jailed_response)
        bonded_resp = make_response({
            "validator": {
                "operator_address": "cosmosvaloper1def456",
                "description": {"moniker": "OtherNode"},
                "status": "BOND_STATUS_BONDED",
                "jailed": False,
                "tokens": "500000000000",
            }
        })

        monitor.client.get = AsyncMock(side_effect=[jailed_resp, bonded_resp])

        await monitor.poll()

        mock_alerter.alert_cosmos_jailed.assert_called_once()
        mock_alerter.alert_cosmos_inactive.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_on_unbonding_transition(self, monitor, state, mock_alerter, unbonding_response):
        state.set_previous_cosmos_status({
            "cosmosvaloper1abc123": {"jailed": False, "status": "BOND_STATUS_BONDED"},
            "cosmosvaloper1def456": {"jailed": False, "status": "BOND_STATUS_BONDED"},
        })

        def make_response(response_data):
            r = MagicMock()
            r.json.return_value = response_data
            r.raise_for_status = MagicMock()
            return r

        unbonding_resp = make_response(unbonding_response)
        bonded_resp = make_response({
            "validator": {
                "operator_address": "cosmosvaloper1def456",
                "description": {"moniker": "OtherNode"},
                "status": "BOND_STATUS_BONDED",
                "jailed": False,
                "tokens": "500000000000",
            }
        })

        monitor.client.get = AsyncMock(side_effect=[unbonding_resp, bonded_resp])

        await monitor.poll()

        mock_alerter.alert_cosmos_inactive.assert_called_once()
        mock_alerter.alert_cosmos_jailed.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_alert_when_already_jailed(self, monitor, state, mock_alerter, jailed_response):
        # Previous state is already jailed — no new alert
        state.set_previous_cosmos_status({
            "cosmosvaloper1abc123": {"jailed": True, "status": "BOND_STATUS_BONDED"},
            "cosmosvaloper1def456": {"jailed": False, "status": "BOND_STATUS_BONDED"},
        })

        def make_response(data):
            r = MagicMock()
            r.json.return_value = data
            r.raise_for_status = MagicMock()
            return r

        monitor.client.get = AsyncMock(side_effect=[
            make_response(jailed_response),
            make_response({
                "validator": {
                    "operator_address": "cosmosvaloper1def456",
                    "description": {"moniker": "OtherNode"},
                    "status": "BOND_STATUS_BONDED",
                    "jailed": False,
                    "tokens": "500000000000",
                }
            }),
        ])

        await monitor.poll()

        mock_alerter.alert_cosmos_jailed.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_updated_after_poll(self, monitor, state, mock_alerter, bonded_response):
        def make_response(data):
            r = MagicMock()
            r.json.return_value = data
            r.raise_for_status = MagicMock()
            return r

        monitor.client.get = AsyncMock(side_effect=[
            make_response(bonded_response),
            make_response({
                "validator": {
                    "operator_address": "cosmosvaloper1def456",
                    "description": {"moniker": "OtherNode"},
                    "status": "BOND_STATUS_BONDED",
                    "jailed": False,
                    "tokens": "500000000000",
                }
            }),
        ])

        await monitor.poll()

        updated = state.get_previous_cosmos_status()
        assert "cosmosvaloper1abc123" in updated
        assert updated["cosmosvaloper1abc123"]["jailed"] is False
        assert updated["cosmosvaloper1abc123"]["status"] == "BOND_STATUS_BONDED"

    @pytest.mark.asyncio
    async def test_fetch_error_skips_validator(self, monitor, state, mock_alerter):
        state.set_previous_cosmos_status({
            "cosmosvaloper1abc123": {"jailed": False, "status": "BOND_STATUS_BONDED"},
            "cosmosvaloper1def456": {"jailed": False, "status": "BOND_STATUS_BONDED"},
        })
        monitor.client.get = AsyncMock(side_effect=Exception("network error"))

        # Should not raise
        await monitor.poll()
        mock_alerter.alert_cosmos_jailed.assert_not_called()


class TestRunDisabled:
    @pytest.mark.asyncio
    async def test_run_exits_when_no_validators(self, config_no_validators, state, mock_alerter, mock_client):
        monitor = CosmosMonitor(config_no_validators, state, mock_alerter, mock_client)
        # run() should return immediately when no validators configured
        await monitor.run()
        mock_alerter.alert_cosmos_jailed.assert_not_called()
        mock_alerter.alert_cosmos_inactive.assert_not_called()

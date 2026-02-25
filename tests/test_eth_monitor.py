import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from state import State
from alerter import Alerter
from eth_monitor import EthMonitor, EthSlashingEvent


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setenv("ETH_BEACON_API_URL", "https://beaconcha.in")
    monkeypatch.setenv("ETH_BEACON_NODE_URL", "http://localhost:5052")
    return Config.from_env()


@pytest.fixture
def state(tmp_state_path):
    s = State(tmp_state_path)
    return s


@pytest.fixture
def alerter(config, state):
    return Alerter(config, state)


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def monitor(config, state, alerter, mock_client, tmp_path):
    operators_path = str(tmp_path / "known_operators.json")
    with open(operators_path, "w") as f:
        json.dump({}, f)
    return EthMonitor(config, state, alerter, mock_client, operators_path=operators_path)


class TestParseBeachachaIn:
    def test_parse_beaconcha_slashings(self, monitor, eth_beaconcha_fixture):
        events = monitor.parse_beaconcha_slashings(eth_beaconcha_fixture)
        assert len(events) == 2
        assert events[0].validator_index == 12345
        assert events[0].slash_type == "attester"
        assert events[0].epoch == 200000
        assert events[0].slot == 6400000
        assert events[1].validator_index == 99999
        assert events[1].slash_type == "proposer"

    def test_event_id_format(self, monitor, eth_beaconcha_fixture):
        events = monitor.parse_beaconcha_slashings(eth_beaconcha_fixture)
        assert events[0].event_id == "eth_slash_12345_6400000"
        assert events[1].event_id == "eth_slash_99999_6400032"


class TestParseFallback:
    def test_parse_attester_slashings(self, monitor, eth_attester_slashings_fixture):
        events = monitor.parse_attester_slashings(eth_attester_slashings_fixture)
        assert len(events) == 1
        assert events[0].validator_index == 55555
        assert events[0].slash_type == "attester"
        assert events[0].slot == 6500000

    def test_parse_proposer_slashings(self, monitor, eth_proposer_slashings_fixture):
        events = monitor.parse_proposer_slashings(eth_proposer_slashings_fixture)
        assert len(events) == 1
        assert events[0].validator_index == 77777
        assert events[0].slash_type == "proposer"
        assert events[0].slot == 6600000


class TestOperatorResolution:
    def test_known_operator_from_json(self, config, state, alerter, mock_client, tmp_path):
        operators_path = str(tmp_path / "known_operators.json")
        with open(operators_path, "w") as f:
            json.dump({"12345": "Lido Finance"}, f)
        monitor = EthMonitor(config, state, alerter, mock_client, operators_path=operators_path)
        name = monitor.resolve_operator(12345)
        assert name == "Lido Finance"

    def test_unknown_operator_falls_back_to_index(self, monitor):
        name = monitor.resolve_operator(99999)
        assert "99999" in name


class TestAlreadySeen:
    @pytest.mark.asyncio
    async def test_already_seen_event_not_alerted(self, monitor, state):
        state.mark_seen("eth_slash_12345_6400000")
        alert_mock = AsyncMock()
        with patch.object(monitor.alerter, "alert_eth_slashing", alert_mock):
            event = EthSlashingEvent(12345, 67890, "attester", 200000, 6400000, "Op")
            await monitor.process_events([event])
            alert_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_event_is_alerted(self, monitor):
        alert_mock = AsyncMock()
        with patch.object(monitor.alerter, "alert_eth_slashing", alert_mock):
            event = EthSlashingEvent(12345, 67890, "attester", 200000, 6400000, "Op")
            await monitor.process_events([event])
            alert_mock.assert_called_once_with(event)


class TestFallback:
    @pytest.mark.asyncio
    async def test_beacon_pool_is_primary(self, monitor, mock_client):
        # fetch_slashings now calls fetch_fallback_slashings directly (beacon pool is primary)
        with patch.object(monitor, "fetch_fallback_slashings", new_callable=AsyncMock) as mock_fb:
            mock_fb.return_value = []
            await monitor.fetch_slashings()
            mock_fb.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_slashings_returns_empty_on_error(self, monitor):
        with patch.object(monitor, "fetch_fallback_slashings", new_callable=AsyncMock) as mock_fb:
            mock_fb.side_effect = Exception("network error")
            result = await monitor.fetch_slashings()
            assert result == []

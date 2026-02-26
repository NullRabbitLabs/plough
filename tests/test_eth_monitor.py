import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from config import Config
from state import State
from alerter import Alerter
from eth_monitor import EthMonitor, EthSlashingEvent

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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

    def test_nested_format_ethereum_key_used(self, config, state, alerter, mock_client, tmp_path):
        operators_path = str(tmp_path / "known_operators.json")
        with open(operators_path, "w") as f:
            json.dump({"solana": {}, "ethereum": {"12345": "Lido Finance"}, "sui": {}}, f)
        monitor = EthMonitor(config, state, alerter, mock_client, operators_path=operators_path)
        assert monitor.resolve_operator(12345) == "Lido Finance"

    def test_nested_format_dict_value_reads_name(self, config, state, alerter, mock_client, tmp_path):
        operators_path = str(tmp_path / "known_operators.json")
        with open(operators_path, "w") as f:
            json.dump({"solana": {}, "ethereum": {"12345": {"name": "Lido Finance", "website": "https://lido.fi"}}, "sui": {}}, f)
        monitor = EthMonitor(config, state, alerter, mock_client, operators_path=operators_path)
        assert monitor.resolve_operator(12345) == "Lido Finance"

    def test_flat_format_still_works(self, config, state, alerter, mock_client, tmp_path):
        operators_path = str(tmp_path / "known_operators.json")
        with open(operators_path, "w") as f:
            json.dump({"12345": "Flashbots"}, f)
        monitor = EthMonitor(config, state, alerter, mock_client, operators_path=operators_path)
        assert monitor.resolve_operator(12345) == "Flashbots"


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
    async def test_beacon_pool_is_called(self, monitor, mock_client):
        with patch.object(monitor, "fetch_fallback_slashings", new_callable=AsyncMock) as mock_fb:
            with patch.object(monitor, "fetch_block_range_slashings", new_callable=AsyncMock) as mock_br:
                mock_fb.return_value = []
                mock_br.return_value = []
                await monitor.fetch_slashings()
                mock_fb.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_slashings_returns_empty_on_error(self, monitor):
        with patch.object(monitor, "fetch_fallback_slashings", new_callable=AsyncMock) as mock_fb:
            with patch.object(monitor, "fetch_block_range_slashings", new_callable=AsyncMock) as mock_br:
                mock_fb.side_effect = Exception("network error")
                mock_br.return_value = []
                result = await monitor.fetch_slashings()
                assert result == []


class TestAttesterIntersection:
    def test_intersection_yields_multiple_events(self, monitor):
        data = {
            "data": [{
                "attestation_1": {
                    "attesting_indices": ["100", "200", "300"],
                    "data": {
                        "slot": "5000000", "index": "0", "beacon_block_root": "0x",
                        "source": {"epoch": "0", "root": "0x"},
                        "target": {"epoch": "0", "root": "0x"},
                    },
                },
                "attestation_2": {
                    "attesting_indices": ["200", "300"],
                    "data": {
                        "slot": "5000001", "index": "0", "beacon_block_root": "0x",
                        "source": {"epoch": "0", "root": "0x"},
                        "target": {"epoch": "0", "root": "0x"},
                    },
                },
            }]
        }
        events = monitor.parse_attester_slashings(data)
        slashed = {e.validator_index for e in events}
        assert slashed == {200, 300}

    def test_only_intersection_validator_slashed(self, monitor, eth_attester_slashings_fixture):
        # att1=[55555, 66666], att2=[55555] → intersection={55555} only
        events = monitor.parse_attester_slashings(eth_attester_slashings_fixture)
        assert len(events) == 1
        assert events[0].validator_index == 55555
        assert not any(e.validator_index == 66666 for e in events)


class TestBlockScanning:
    @pytest.mark.asyncio
    async def test_fetch_finalized_slot(self, monitor, mock_client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "data": {"header": {"message": {"slot": "7000000"}}}
        }
        mock_client.get = AsyncMock(return_value=resp)
        slot = await monitor.fetch_finalized_slot()
        assert slot == 7000000

    @pytest.mark.asyncio
    async def test_fetch_block_slashings(self, monitor, mock_client, eth_block_with_slashings_fixture):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = eth_block_with_slashings_fixture
        mock_client.get = AsyncMock(return_value=resp)
        events = await monitor.fetch_block_slashings(7000001)
        assert len(events) == 2
        attester = next(e for e in events if e.slash_type == "attester")
        proposer = next(e for e in events if e.slash_type == "proposer")
        assert attester.validator_index == 11111
        assert proposer.validator_index == 33333

    @pytest.mark.asyncio
    async def test_fetch_block_slashings_empty_block(self, monitor, mock_client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "data": {
                "message": {
                    "slot": "7000001",
                    "body": {"attester_slashings": [], "proposer_slashings": []},
                }
            }
        }
        mock_client.get = AsyncMock(return_value=resp)
        events = await monitor.fetch_block_slashings(7000001)
        assert events == []

    @pytest.mark.asyncio
    async def test_fetch_block_slashings_error_returns_empty(self, monitor, mock_client):
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        events = await monitor.fetch_block_slashings(7000001)
        assert events == []


class TestSlotTracking:
    @pytest.mark.asyncio
    async def test_initialises_slot_on_first_run(self, monitor, state, mock_client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": {"header": {"message": {"slot": "7000000"}}}}
        mock_client.get = AsyncMock(return_value=resp)
        events = await monitor.fetch_block_range_slashings()
        assert events == []
        assert state.get_last_eth_slot() == 7000000

    @pytest.mark.asyncio
    async def test_scans_new_slots_since_last(self, monitor, state, mock_client):
        state.set_last_eth_slot(6999998)

        finalized_resp = MagicMock()
        finalized_resp.raise_for_status = MagicMock()
        finalized_resp.json.return_value = {
            "data": {"header": {"message": {"slot": "7000000"}}}
        }
        empty_block = {
            "data": {
                "message": {
                    "slot": "0",
                    "body": {"attester_slashings": [], "proposer_slashings": []},
                }
            }
        }
        block_resp = MagicMock()
        block_resp.raise_for_status = MagicMock()
        block_resp.json.return_value = empty_block

        mock_client.get = AsyncMock(side_effect=[finalized_resp, block_resp, block_resp])
        await monitor.fetch_block_range_slashings()
        assert state.get_last_eth_slot() == 7000000

    @pytest.mark.asyncio
    async def test_caps_at_max_slots_per_poll(self, monitor, state, mock_client, monkeypatch):
        monkeypatch.setattr(monitor.config, "eth_max_slots_per_poll", 2)
        state.set_last_eth_slot(7000000)

        finalized_resp = MagicMock()
        finalized_resp.raise_for_status = MagicMock()
        finalized_resp.json.return_value = {
            "data": {"header": {"message": {"slot": "7000100"}}}
        }
        block_resp = MagicMock()
        block_resp.raise_for_status = MagicMock()
        block_resp.json.return_value = {
            "data": {
                "message": {
                    "slot": "0",
                    "body": {"attester_slashings": [], "proposer_slashings": []},
                }
            }
        }
        mock_client.get = AsyncMock(side_effect=[finalized_resp, block_resp, block_resp])
        await monitor.fetch_block_range_slashings()
        assert state.get_last_eth_slot() == 7000002

    @pytest.mark.asyncio
    async def test_no_new_slots(self, monitor, state, mock_client):
        state.set_last_eth_slot(7000000)

        finalized_resp = MagicMock()
        finalized_resp.raise_for_status = MagicMock()
        finalized_resp.json.return_value = {
            "data": {"header": {"message": {"slot": "7000000"}}}
        }
        mock_client.get = AsyncMock(return_value=finalized_resp)
        events = await monitor.fetch_block_range_slashings()
        assert events == []
        assert state.get_last_eth_slot() == 7000000

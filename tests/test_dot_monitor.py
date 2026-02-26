import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from config import Config
from state import State
from dot_monitor import DotMonitor, DotValidator, DotSlashEvent

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setenv("DOT_VALIDATORS", "1abc123def456,1ghi789jkl012")
    monkeypatch.setenv("DOT_SUBSCAN_URL", "https://polkadot.api.subscan.io")
    monkeypatch.delenv("DOT_SUBSCAN_API_KEY", raising=False)
    monkeypatch.delenv("QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("QUIET_HOURS_END", raising=False)
    return Config.from_env()


@pytest.fixture
def config_no_validators(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setenv("DOT_VALIDATORS", "")
    monkeypatch.delenv("QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("QUIET_HOURS_END", raising=False)
    return Config.from_env()


@pytest.fixture
def state(tmp_state_path):
    return State(tmp_state_path)


@pytest.fixture
def mock_alerter():
    alerter = MagicMock()
    alerter.alert_dot_inactive = AsyncMock()
    alerter.alert_dot_slashed = AsyncMock()
    return alerter


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def monitor(config, state, mock_alerter, mock_client):
    return DotMonitor(config, state, mock_alerter, mock_client)


@pytest.fixture
def validators_fixture():
    with open(FIXTURES_DIR / "dot_validators.json") as f:
        return json.load(f)


@pytest.fixture
def slash_events_fixture():
    with open(FIXTURES_DIR / "dot_slash_events.json") as f:
        return json.load(f)


class TestParseValidators:
    def test_parse_validator_list(self, monitor, validators_fixture):
        validators = monitor.parse_validators(validators_fixture)
        assert len(validators) == 3

    def test_parse_elected_status(self, monitor, validators_fixture):
        validators = monitor.parse_validators(validators_fixture)
        elected = [v for v in validators if v.is_elected]
        assert len(elected) == 2

    def test_parse_non_elected_status(self, monitor, validators_fixture):
        validators = monitor.parse_validators(validators_fixture)
        non_elected = [v for v in validators if not v.is_elected]
        assert len(non_elected) == 1
        assert non_elected[0].stash == "1mno345pqr678"

    def test_parse_validator_fields(self, monitor, validators_fixture):
        validators = monitor.parse_validators(validators_fixture)
        v = validators[0]
        assert v.stash == "1abc123def456"
        assert v.display == "MyDotNode"
        assert v.bonded == 1000000000000

    def test_parse_empty_list(self, monitor):
        data = {"data": {"list": []}}
        validators = monitor.parse_validators(data)
        assert validators == []

    def test_parse_null_list(self, monitor):
        data = {"data": {"list": None}}
        validators = monitor.parse_validators(data)
        assert validators == []


class TestParseSlashEvents:
    def test_parse_slash_events(self, monitor, slash_events_fixture):
        events = monitor.parse_slash_events(slash_events_fixture)
        assert len(events) == 2

    def test_parse_slash_event_fields(self, monitor, slash_events_fixture):
        events = monitor.parse_slash_events(slash_events_fixture)
        e = events[0]
        assert e.stash == "1abc123def456"
        assert e.amount == 500000000000
        assert e.block_num == 12345678
        assert e.event_index == "12345678-2"

    def test_parse_slash_event_second_stash(self, monitor, slash_events_fixture):
        events = monitor.parse_slash_events(slash_events_fixture)
        assert events[1].stash == "1zzz999yyy888"


class TestPollInactive:
    @pytest.mark.asyncio
    async def test_no_alert_on_first_poll(self, monitor, state, mock_alerter, validators_fixture):
        validators = monitor.parse_validators(validators_fixture)
        await monitor.poll_inactive(validators)
        mock_alerter.alert_dot_inactive.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_when_elected_validator_drops_out(self, monitor, state, mock_alerter):
        # Both were active previously
        state.set_previous_dot_active(["1abc123def456", "1ghi789jkl012"])

        # Now only one is elected
        validators = [
            DotValidator("1abc123def456", "MyDotNode", is_elected=True, commission=5.0, bonded=1000000000000),
            DotValidator("1ghi789jkl012", "AnotherNode", is_elected=False, commission=1.0, bonded=500000000000),
        ]
        await monitor.poll_inactive(validators)

        mock_alerter.alert_dot_inactive.assert_called_once()
        call_arg = mock_alerter.alert_dot_inactive.call_args[0][0]
        assert call_arg.stash == "1ghi789jkl012"

    @pytest.mark.asyncio
    async def test_no_alert_when_already_inactive(self, monitor, state, mock_alerter):
        # Neither was previously active
        state.set_previous_dot_active(["1abc123def456"])

        # Only 1abc is still elected; 1ghi was never active so no transition
        validators = [
            DotValidator("1abc123def456", "MyDotNode", is_elected=True, commission=5.0, bonded=1000000000000),
            DotValidator("1ghi789jkl012", "AnotherNode", is_elected=False, commission=1.0, bonded=500000000000),
        ]
        await monitor.poll_inactive(validators)

        mock_alerter.alert_dot_inactive.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_updated_after_poll(self, monitor, state, mock_alerter, validators_fixture):
        validators = monitor.parse_validators(validators_fixture)
        await monitor.poll_inactive(validators)

        active = state.get_previous_dot_active()
        # Only the configured validators that are elected should be tracked
        assert "1abc123def456" in active
        assert "1ghi789jkl012" in active
        # The non-configured elected validator is not tracked
        assert "1mno345pqr678" not in active


class TestPollSlashing:
    @pytest.mark.asyncio
    async def test_alert_on_configured_slash(self, monitor, state, mock_alerter, slash_events_fixture):
        mock_resp = MagicMock()
        mock_resp.json.return_value = slash_events_fixture
        mock_resp.raise_for_status = MagicMock()
        monitor.client.post = AsyncMock(return_value=mock_resp)

        await monitor.poll_slashing()

        # Only "1abc123def456" is in config; "1zzz999yyy888" is not
        mock_alerter.alert_dot_slashed.assert_called_once()
        call_args = mock_alerter.alert_dot_slashed.call_args[0]
        assert call_args[1].stash == "1abc123def456"

    @pytest.mark.asyncio
    async def test_slash_deduped_on_second_poll(self, monitor, state, mock_alerter, slash_events_fixture):
        state.mark_seen("dot_slash_12345678-2")

        mock_resp = MagicMock()
        mock_resp.json.return_value = slash_events_fixture
        mock_resp.raise_for_status = MagicMock()
        monitor.client.post = AsyncMock(return_value=mock_resp)

        await monitor.poll_slashing()

        mock_alerter.alert_dot_slashed.assert_not_called()

    @pytest.mark.asyncio
    async def test_slash_event_marked_seen_after_alert(self, monitor, state, mock_alerter, slash_events_fixture):
        mock_resp = MagicMock()
        mock_resp.json.return_value = slash_events_fixture
        mock_resp.raise_for_status = MagicMock()
        monitor.client.post = AsyncMock(return_value=mock_resp)

        await monitor.poll_slashing()

        assert state.is_seen("dot_slash_12345678-2")


class TestRunDisabled:
    @pytest.mark.asyncio
    async def test_run_exits_when_no_validators(self, config_no_validators, state, mock_alerter, mock_client):
        monitor = DotMonitor(config_no_validators, state, mock_alerter, mock_client)
        await monitor.run()
        mock_alerter.alert_dot_inactive.assert_not_called()
        mock_alerter.alert_dot_slashed.assert_not_called()

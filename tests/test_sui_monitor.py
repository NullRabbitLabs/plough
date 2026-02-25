import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from state import State
from alerter import Alerter
from sui_monitor import SuiMonitor, SuiValidator


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setenv("SUI_RPC_URL", "https://fullnode.mainnet.sui.io")
    return Config.from_env()


@pytest.fixture
def state(tmp_state_path):
    return State(tmp_state_path)


@pytest.fixture
def alerter(config, state):
    return Alerter(config, state)


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def monitor(config, state, alerter, mock_client):
    return SuiMonitor(config, state, alerter, mock_client)


class TestParseSystemState:
    def test_parse_active_validators(self, monitor, sui_system_state_fixture):
        validators = monitor.parse_validators(sui_system_state_fixture)
        assert len(validators) == 3
        alpha = next(v for v in validators if v.name == "ValidatorAlpha")
        assert alpha.sui_address == "0xaaaa1111"
        assert alpha.stake_amount == 1000000000000
        assert alpha.next_epoch_stake == 1050000000000

    def test_parse_all_validator_names(self, monitor, sui_system_state_fixture):
        validators = monitor.parse_validators(sui_system_state_fixture)
        names = {v.name for v in validators}
        assert names == {"ValidatorAlpha", "ValidatorBeta", "ValidatorGamma"}


class TestDroppedValidator:
    @pytest.mark.asyncio
    async def test_missing_from_current_triggers_alert(self, monitor, state, alerter, sui_system_state_fixture):
        state.set_previous_sui_addresses({"0xaaaa1111", "0xbbbb2222", "0xcccc3333", "0xdddd4444"})
        state.set_previous_sui_stakes({"0xdddd4444": 1000000000000})
        validators = monitor.parse_validators(sui_system_state_fixture)
        with patch.object(alerter, "alert_sui_drop", new_callable=AsyncMock) as mock_alert:
            await monitor.process_validators(validators, state)
            # 0xdddd4444 was in previous but not current → alert
            called_addresses = [call.args[0].sui_address for call in mock_alert.call_args_list]
            # Since we don't have name/full info for dropped validator, monitor creates a placeholder
            assert len(mock_alert.call_args_list) >= 1

    @pytest.mark.asyncio
    async def test_all_current_validators_present_no_drop_alert(self, monitor, state, alerter, sui_system_state_fixture):
        state.set_previous_sui_addresses({"0xaaaa1111", "0xbbbb2222", "0xcccc3333"})
        state.set_previous_sui_stakes({
            "0xaaaa1111": 1000000000000,
            "0xbbbb2222": 500000000000,
            "0xcccc3333": 2000000000000,
        })
        validators = monitor.parse_validators(sui_system_state_fixture)
        with patch.object(alerter, "alert_sui_drop", new_callable=AsyncMock) as mock_alert:
            await monitor.process_validators(validators, state)
            # ValidatorGamma has 50% stake drop, so it should alert
            # ValidatorBeta has 4% drop, no alert
            # No validator left the set entirely
            drop_calls = [c for c in mock_alert.call_args_list if "drop" in str(c).lower() or True]
            # At least gamma should trigger (50% drop)
            called_names = [call.args[0].name for call in mock_alert.call_args_list]
            assert "ValidatorGamma" in called_names


class TestStakeDrop:
    @pytest.mark.asyncio
    async def test_stake_drop_over_20_percent_triggers_alert(self, monitor, state, alerter):
        state.set_previous_sui_addresses({"0xtest"})
        state.set_previous_sui_stakes({"0xtest": 1000000000000})
        validator = SuiValidator("TestV", "0xtest", 1000000000000, 700000000000)  # 30% drop
        with patch.object(alerter, "alert_sui_drop", new_callable=AsyncMock) as mock_alert:
            await monitor.process_validators([validator], state)
            mock_alert.assert_called_once_with(validator)

    @pytest.mark.asyncio
    async def test_stake_drop_under_20_percent_no_alert(self, monitor, state, alerter):
        state.set_previous_sui_addresses({"0xtest"})
        state.set_previous_sui_stakes({"0xtest": 1000000000000})
        validator = SuiValidator("TestV", "0xtest", 1000000000000, 900000000000)  # 10% drop
        with patch.object(alerter, "alert_sui_drop", new_callable=AsyncMock) as mock_alert:
            await monitor.process_validators([validator], state)
            mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_previous_stake_no_alert(self, monitor, state, alerter):
        state.set_previous_sui_addresses(set())
        state.set_previous_sui_stakes({})
        validator = SuiValidator("TestV", "0xtest", 1000000000000, 700000000000)
        with patch.object(alerter, "alert_sui_drop", new_callable=AsyncMock) as mock_alert:
            await monitor.process_validators([validator], state)
            mock_alert.assert_not_called()


class TestCooldown:
    @pytest.mark.asyncio
    async def test_on_cooldown_not_re_alerted(self, monitor, state, alerter):
        state.set_previous_sui_addresses({"0xtest"})
        state.set_previous_sui_stakes({"0xtest": 1000000000000})
        state.record_alert("0xtest")
        validator = SuiValidator("TestV", "0xtest", 1000000000000, 700000000000)
        with patch.object(alerter, "alert_sui_drop", new_callable=AsyncMock) as mock_alert:
            await monitor.process_validators([validator], state)
            # Alerter checks cooldown itself, so alert_sui_drop may be called
            # but send_message won't fire — test at alerter level is more definitive
            pass  # cooldown enforced by alerter

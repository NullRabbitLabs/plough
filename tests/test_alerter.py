import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from state import State
from alerter import Alerter
from eth_monitor import EthSlashingEvent
from sol_monitor import SolValidator
from sui_monitor import SuiValidator


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.delenv("QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("QUIET_HOURS_END", raising=False)
    return Config.from_env()


@pytest.fixture
def state(tmp_state_path):
    return State(tmp_state_path)


@pytest.fixture
def alerter(config, state):
    return Alerter(config, state)


@pytest.fixture
def eth_event():
    return EthSlashingEvent(
        validator_index=12345,
        slashed_by=67890,
        slash_type="attester",
        epoch=200000,
        slot=6400000,
        operator_name="Validator Operator X",
    )


@pytest.fixture
def sol_validator():
    return SolValidator(
        identity="ValidatorIdent11111111111111111111111111111",
        vote_account="ValidatorVote111111111111111111111111111111",
        activated_stake_sol=5000.0,
        commission=5,
        last_vote=249900000,
        root_slot=249800000,
    )


@pytest.fixture
def sui_validator():
    return SuiValidator(
        name="ValidatorAlpha",
        sui_address="0xaaaa1111",
        stake_amount=1000000000000,
        next_epoch_stake=500000000000,
    )


class TestAlertDedup:
    @pytest.mark.asyncio
    async def test_same_event_id_not_sent_twice(self, alerter, state):
        state.mark_seen("eth_slash_12345_6400000")
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_eth_slashing(
                EthSlashingEvent(12345, 67890, "attester", 200000, 6400000, "Op X")
            )
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_event_id_is_sent(self, alerter):
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_eth_slashing(
                EthSlashingEvent(12345, 67890, "attester", 200000, 6400000, "Op X")
            )
            mock_send.assert_called_once()


class TestCooldown:
    @pytest.mark.asyncio
    async def test_sol_validator_on_cooldown_not_alerted(self, alerter, state, sol_validator):
        state.record_alert(sol_validator.vote_account)
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_sol_delinquent([sol_validator], is_mass=False)
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sol_validator_off_cooldown_is_alerted(self, alerter, state, sol_validator):
        state.record_alert(sol_validator.vote_account)
        # expire the cooldown manually
        state._data["alert_times"][sol_validator.vote_account] = time.time() - 99999
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_sol_delinquent([sol_validator], is_mass=False)
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_sui_validator_on_cooldown_not_alerted(self, alerter, state, sui_validator):
        state.record_alert(sui_validator.sui_address)
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_sui_drop(sui_validator)
            mock_send.assert_not_called()


class TestMassEvent:
    @pytest.mark.asyncio
    async def test_mass_event_sends_single_message(self, alerter):
        validators = [
            SolValidator(
                identity=f"Ident{i}",
                vote_account=f"Vote{i}",
                activated_stake_sol=5000.0,
                commission=5,
                last_vote=100,
                root_slot=90,
            )
            for i in range(6)
        ]
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_sol_delinquent(validators, is_mass=True)
            assert mock_send.call_count == 1

    @pytest.mark.asyncio
    async def test_non_mass_event_sends_per_validator(self, alerter):
        validators = [
            SolValidator(
                identity=f"Ident{i}",
                vote_account=f"Vote{i}",
                activated_stake_sol=5000.0,
                commission=5,
                last_vote=100,
                root_slot=90,
            )
            for i in range(3)
        ]
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_sol_delinquent(validators, is_mass=False)
            assert mock_send.call_count == 3


class TestMessageFormat:
    def test_eth_format_contains_key_fields(self, alerter, eth_event):
        msg = alerter.format_eth_slashing(eth_event)
        assert "12345" in msg
        assert "attester" in msg.lower()
        assert "200000" in msg or "6400000" in msg

    def test_eth_format_contains_operator_name(self, alerter, eth_event):
        msg = alerter.format_eth_slashing(eth_event)
        assert "Validator Operator X" in msg

    def test_sol_format_individual(self, alerter, sol_validator):
        msg = alerter.format_sol_delinquent([sol_validator], is_mass=False)
        assert sol_validator.vote_account in msg or sol_validator.identity in msg
        assert "5000" in msg or "delinquent" in msg.lower()

    def test_sol_format_mass(self, alerter):
        validators = [
            SolValidator(f"Id{i}", f"Vote{i}", 1000.0, 5, 100, 90) for i in range(6)
        ]
        msg = alerter.format_sol_delinquent(validators, is_mass=True)
        assert "6" in msg

    def test_sui_format_contains_key_fields(self, alerter, sui_validator):
        msg = alerter.format_sui_drop(sui_validator)
        assert "ValidatorAlpha" in msg
        assert "0xaaaa1111" in msg


class TestSlack:
    @pytest.mark.asyncio
    async def test_slack_send_called_when_webhook_configured(self, state, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        cfg = Config.from_env()
        a = Alerter(cfg, state)
        with patch.object(a, "_send_slack", new_callable=AsyncMock) as mock_slack, \
             patch.object(a, "_send_telegram", new_callable=AsyncMock) as mock_tg:
            await a.send_message("hello")
            mock_slack.assert_called_once_with("hello")
            mock_tg.assert_not_called()

    @pytest.mark.asyncio
    async def test_telegram_send_called_when_token_configured(self, state, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1")
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        cfg = Config.from_env()
        a = Alerter(cfg, state)
        with patch.object(a, "_send_slack", new_callable=AsyncMock) as mock_slack, \
             patch.object(a, "_send_telegram", new_callable=AsyncMock) as mock_tg:
            await a.send_message("hello")
            mock_tg.assert_called_once_with("hello")
            mock_slack.assert_not_called()

    @pytest.mark.asyncio
    async def test_both_channels_fire_when_both_configured(self, state, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1")
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        cfg = Config.from_env()
        a = Alerter(cfg, state)
        with patch.object(a, "_send_slack", new_callable=AsyncMock) as mock_slack, \
             patch.object(a, "_send_telegram", new_callable=AsyncMock) as mock_tg:
            await a.send_message("hello")
            mock_tg.assert_called_once_with("hello")
            mock_slack.assert_called_once_with("hello")

    def test_html_to_mrkdwn_bold(self, alerter):
        result = alerter._html_to_mrkdwn("<b>ETH Validator Slashed</b>")
        assert result == "*ETH Validator Slashed*"

    def test_html_to_mrkdwn_code(self, alerter):
        result = alerter._html_to_mrkdwn("<code>0xabc</code>")
        assert result == "`0xabc`"

    def test_html_to_mrkdwn_strips_unknown_tags(self, alerter):
        result = alerter._html_to_mrkdwn("<i>italic</i>")
        assert result == "italic"

    def test_html_to_mrkdwn_plain_text_unchanged(self, alerter):
        result = alerter._html_to_mrkdwn("no tags here")
        assert result == "no tags here"


class TestQuietHours:
    @pytest.mark.asyncio
    async def test_quiet_hours_suppresses_alert(self, state, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
        monkeypatch.setenv("QUIET_HOURS_START", "0")
        monkeypatch.setenv("QUIET_HOURS_END", "23")
        cfg = Config.from_env()
        a = Alerter(cfg, state)
        with patch.object(a, "send_message", new_callable=AsyncMock) as mock_send:
            await a.alert_eth_slashing(
                EthSlashingEvent(1, 2, "attester", 1, 1, "Op")
            )
            mock_send.assert_not_called()

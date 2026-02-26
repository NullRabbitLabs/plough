import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from state import State
from alerter import Alerter
from enrichment import ScanData
from eth_monitor import EthSlashingEvent
from sol_monitor import SolValidator
from sui_monitor import SuiValidator
from cosmos_monitor import CosmosValidator
from dot_monitor import DotValidator, DotSlashEvent


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


@pytest.fixture
def cosmos_validator_bonded():
    return CosmosValidator(
        operator_address="cosmosvaloper1abc123",
        moniker="MyCosmosNode",
        status="BOND_STATUS_BONDED",
        jailed=False,
        tokens=1000000000,
    )


@pytest.fixture
def cosmos_validator_jailed():
    return CosmosValidator(
        operator_address="cosmosvaloper1abc123",
        moniker="MyCosmosNode",
        status="BOND_STATUS_BONDED",
        jailed=True,
        tokens=1000000000,
    )


@pytest.fixture
def dot_validator():
    return DotValidator(
        stash="1abc123def456",
        display="MyDotNode",
        is_elected=True,
        commission=5.0,
        bonded=1000000000000,
    )


@pytest.fixture
def dot_slash_event():
    return DotSlashEvent(
        stash="1abc123def456",
        amount=500000000000,
        block_num=12345678,
        event_index="12345678-2",
    )


class TestCosmosAlerts:
    @pytest.mark.asyncio
    async def test_alert_cosmos_jailed_sends_message(self, alerter, cosmos_validator_jailed):
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_cosmos_jailed(cosmos_validator_jailed)
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_cosmos_jailed_on_cooldown_skipped(self, alerter, state, cosmos_validator_jailed):
        state.record_alert(cosmos_validator_jailed.operator_address)
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_cosmos_jailed(cosmos_validator_jailed)
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_cosmos_inactive_sends_message(self, alerter, cosmos_validator_bonded):
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_cosmos_inactive(cosmos_validator_bonded)
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_cosmos_inactive_on_cooldown_skipped(self, alerter, state, cosmos_validator_bonded):
        state.record_alert(cosmos_validator_bonded.operator_address)
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_cosmos_inactive(cosmos_validator_bonded)
            mock_send.assert_not_called()

    def test_format_cosmos_jailed_contains_key_fields(self, alerter, cosmos_validator_jailed):
        msg = alerter.format_cosmos_jailed(cosmos_validator_jailed)
        assert "MyCosmosNode" in msg
        assert "cosmosvaloper1abc123" in msg
        assert "jailed" in msg.lower() or "Jailed" in msg

    def test_format_cosmos_inactive_contains_key_fields(self, alerter, cosmos_validator_bonded):
        msg = alerter.format_cosmos_inactive(cosmos_validator_bonded)
        assert "MyCosmosNode" in msg
        assert "cosmosvaloper1abc123" in msg


class TestDotAlerts:
    @pytest.mark.asyncio
    async def test_alert_dot_inactive_sends_message(self, alerter, dot_validator):
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_dot_inactive(dot_validator)
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_dot_inactive_on_cooldown_skipped(self, alerter, state, dot_validator):
        state.record_alert(dot_validator.stash)
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_dot_inactive(dot_validator)
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_dot_slashed_sends_message(self, alerter, dot_validator, dot_slash_event):
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_dot_slashed(dot_validator, dot_slash_event)
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_dot_slashed_deduped_by_event_index(self, alerter, state, dot_validator, dot_slash_event):
        state.mark_seen(f"dot_slash_{dot_slash_event.event_index}")
        with patch.object(alerter, "send_message", new_callable=AsyncMock) as mock_send:
            await alerter.alert_dot_slashed(dot_validator, dot_slash_event)
            mock_send.assert_not_called()

    def test_format_dot_inactive_contains_key_fields(self, alerter, dot_validator):
        msg = alerter.format_dot_inactive(dot_validator)
        assert "MyDotNode" in msg
        assert "1abc123def456" in msg

    def test_format_dot_slashed_contains_key_fields(self, alerter, dot_validator, dot_slash_event):
        msg = alerter.format_dot_slashed(dot_validator, dot_slash_event)
        assert "MyDotNode" in msg
        assert "12345678-2" in msg or "slashed" in msg.lower() or "Slash" in msg


class TestSolFormatEnhanced:
    def _make_validator(self, **kwargs):
        defaults = dict(
            identity="Ident1", vote_account="VoteAccount111111111111111111111111111111",
            activated_stake_sol=5000.0, commission=5, last_vote=100, root_slot=90,
        )
        defaults.update(kwargs)
        return SolValidator(**defaults)

    def test_scan_section_present_when_scan_data_exists(self, alerter):
        scan = ScanData("VoteABC", "solana", ["1.2.3.4"], [{"service": "SSH", "port": 22, "severity": "high"}], "2024-06-01")
        v = self._make_validator(scan=scan)
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "SCAN DATA AVAILABLE" in msg
        assert "1.2.3.4" in msg
        assert "2024-06-01" in msg

    def test_scan_section_absent_when_no_scan(self, alerter):
        v = self._make_validator()
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "NOT IN SCAN DB" in msg

    def test_exposed_services_listed(self, alerter):
        scan = ScanData("VoteABC", "solana", ["1.2.3.4"], [
            {"service": "SSH", "port": 22, "severity": "high"},
            {"service": "RPC", "port": 8899, "severity": "low"},
        ], "2024-06-01")
        v = self._make_validator(scan=scan)
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "SSH:22" in msg
        assert "RPC:8899" in msg

    def test_critical_findings_count(self, alerter):
        scan = ScanData("VoteABC", "solana", ["1.2.3.4"], [
            {"service": "SSH", "port": 22, "severity": "critical"},
            {"service": "RDP", "port": 3389, "severity": "critical"},
            {"service": "RPC", "port": 8899, "severity": "low"},
        ], "2024-06-01")
        v = self._make_validator(scan=scan)
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "Critical findings: 2" in msg

    def test_contact_section_present_when_website(self, alerter):
        v = self._make_validator(name="Acme", website="https://acme.io")
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "CONTACT" in msg
        assert "https://acme.io" in msg

    def test_contact_section_absent_when_no_info(self, alerter):
        v = self._make_validator()
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "NO CONTACT INFO" in msg

    def test_twitter_in_contact(self, alerter):
        v = self._make_validator(name="Acme", twitter="acme_validator")
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "acme_validator" in msg

    def test_discord_in_contact(self, alerter):
        v = self._make_validator(name="Acme", discord="acme-discord")
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "acme-discord" in msg

    def test_explorer_links_for_unknown_only(self, alerter):
        unknown = self._make_validator()
        known = self._make_validator(name="Acme Corp")
        msg_unknown = alerter.format_sol_delinquent([unknown], is_mass=False)
        msg_known = alerter.format_sol_delinquent([known], is_mass=False)
        assert "validators.app" in msg_unknown
        assert "solana.fm" in msg_unknown
        assert "validators.app" not in msg_known
        assert "solana.fm" not in msg_known

    def test_action_line_for_known_operator(self, alerter):
        v = self._make_validator(name="Acme Corp")
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "Send disclosure" in msg

    def test_action_line_for_unknown_operator(self, alerter):
        v = self._make_validator()
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "Identify operator" in msg

    def test_name_in_output_when_present(self, alerter):
        v = self._make_validator(name="Chorus One")
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        assert "Name: Chorus One" in msg

    def test_mass_format_unchanged(self, alerter):
        validators = [SolValidator(f"Id{i}", f"Vote{i}", 1000.0, 5, 100, 90) for i in range(6)]
        msg = alerter.format_sol_delinquent(validators, is_mass=True)
        assert "Mass Delinquency" in msg
        assert "6" in msg


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

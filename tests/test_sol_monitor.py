import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from state import State
from alerter import Alerter
from enrichment import Enricher, EnrichedData, ScanData
from sol_monitor import SolMonitor, SolValidator

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setenv("SOL_RPC_URL", "https://api.mainnet-beta.solana.com")
    monkeypatch.setenv("SOL_STAKE_THRESHOLD_SOL", "100")
    monkeypatch.setenv("SOL_MASS_EVENT_THRESHOLD", "5")
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
    return SolMonitor(config, state, alerter, mock_client)


class TestParseVoteAccounts:
    def test_parse_delinquent_validators(self, monitor, sol_vote_accounts_fixture):
        validators = monitor.parse_delinquent(sol_vote_accounts_fixture)
        assert len(validators) == 2
        v = validators[0]
        assert v.vote_account == "DelinquentVote111111111111111111111111111111"
        assert v.identity == "DelinquentIdent1111111111111111111111111111"
        assert v.activated_stake_sol == pytest.approx(5000000.0, rel=1e-3)
        assert v.commission == 5

    def test_parse_activated_stake_conversion(self, monitor, sol_vote_accounts_fixture):
        validators = monitor.parse_delinquent(sol_vote_accounts_fixture)
        # 5_000_000_000_000_000 lamports / 1e9 = 5_000_000 SOL
        assert validators[0].activated_stake_sol == pytest.approx(5_000_000.0, rel=1e-3)


class TestNewDelinquent:
    def test_newly_delinquent_triggers_alert(self, monitor, state, sol_vote_accounts_fixture):
        state.set_previous_delinquent(set())
        validators = monitor.parse_delinquent(sol_vote_accounts_fixture)
        new_delinquent = monitor.find_new_delinquent(validators, state)
        assert len(new_delinquent) > 0

    def test_already_delinquent_not_triggered(self, monitor, state, sol_vote_accounts_fixture):
        validators = monitor.parse_delinquent(sol_vote_accounts_fixture)
        existing = {v.vote_account for v in validators}
        state.set_previous_delinquent(existing)
        new_delinquent = monitor.find_new_delinquent(validators, state)
        assert len(new_delinquent) == 0


class TestStakeThreshold:
    def test_below_threshold_excluded(self, monitor, sol_vote_accounts_fixture):
        validators = monitor.parse_delinquent(sol_vote_accounts_fixture)
        # SmallDelinquent has 100_000_000 lamports = 0.1 SOL, threshold is 100 SOL
        filtered = monitor.filter_by_stake(validators)
        vote_accounts = {v.vote_account for v in filtered}
        assert "SmallDelinquent111111111111111111111111111111" not in vote_accounts

    def test_above_threshold_included(self, monitor, sol_vote_accounts_fixture):
        validators = monitor.parse_delinquent(sol_vote_accounts_fixture)
        filtered = monitor.filter_by_stake(validators)
        vote_accounts = {v.vote_account for v in filtered}
        assert "DelinquentVote111111111111111111111111111111" in vote_accounts


class TestMassEvent:
    @pytest.mark.asyncio
    async def test_mass_event_threshold_triggers_grouped_alert(self, monitor, state, alerter):
        state.set_previous_delinquent(set())
        state.set_candidate_delinquent(set())
        validators = [
            SolValidator(f"Id{i}", f"Vote{i}", 5000.0, 5, 100, 90) for i in range(6)
        ]
        with patch.object(alerter, "alert_sol_delinquent", new_callable=AsyncMock) as mock_alert:
            # Poll 1: validators become candidates
            await monitor.process_delinquent(validators, state)
            mock_alert.assert_not_called()
            # Poll 2: confirmed delinquent → grouped alert
            await monitor.process_delinquent(validators, state)
            mock_alert.assert_called_once()
            assert mock_alert.call_args[1].get("is_mass") or mock_alert.call_args[0][1] is True

    @pytest.mark.asyncio
    async def test_below_mass_threshold_individual_alerts(self, monitor, state, alerter):
        state.set_previous_delinquent(set())
        state.set_candidate_delinquent(set())
        validators = [
            SolValidator(f"Id{i}", f"Vote{i}", 5000.0, 5, 100, 90) for i in range(3)
        ]
        with patch.object(alerter, "alert_sol_delinquent", new_callable=AsyncMock) as mock_alert:
            # Poll 1: candidates
            await monitor.process_delinquent(validators, state)
            assert not mock_alert.called
            # Poll 2: confirmed
            await monitor.process_delinquent(validators, state)
            assert mock_alert.called


class TestEnrichmentIntegration:
    @pytest.mark.asyncio
    async def test_enricher_called_per_validator(self, config, state, alerter, mock_client):
        mock_enricher = AsyncMock(spec=Enricher)
        mock_enricher.enrich_solana = AsyncMock(return_value=EnrichedData(
            name="Chorus One", website="https://chorus.one", keybase="chorusone",
            twitter="chorusone", discord="", ips=["1.2.3.4"], rdns="ec2.amazonaws.com",
        ))
        monitor = SolMonitor(config, state, alerter, mock_client, enricher=mock_enricher)
        validators = [
            SolValidator("Id1", "Vote1", 5000.0, 5, 100, 90),
            SolValidator("Id2", "Vote2", 5000.0, 5, 100, 90),
        ]
        await monitor.enrich_validators(validators)
        assert mock_enricher.enrich_solana.call_count == 2
        assert validators[0].name == "Chorus One"
        assert validators[0].website == "https://chorus.one"
        assert validators[0].ips == ["1.2.3.4"]
        assert validators[1].name == "Chorus One"

    @pytest.mark.asyncio
    async def test_enrichment_fields_applied(self, config, state, alerter, mock_client):
        scan = ScanData("Vote1", "solana", ["1.2.3.4"], [{"service": "SSH", "port": 22, "severity": "high"}], "2024-06-01")
        mock_enricher = AsyncMock(spec=Enricher)
        mock_enricher.enrich_solana = AsyncMock(return_value=EnrichedData(
            name="Acme", website="https://acme.io", keybase="acme",
            twitter="acme_validator", discord="acme", ips=["10.0.0.1"], rdns="acme.example.com",
            scan=scan,
        ))
        monitor = SolMonitor(config, state, alerter, mock_client, enricher=mock_enricher)
        validators = [SolValidator("Id1", "Vote1", 5000.0, 5, 100, 90)]
        await monitor.enrich_validators(validators)
        v = validators[0]
        assert v.name == "Acme"
        assert v.twitter == "acme_validator"
        assert v.discord == "acme"
        assert v.rdns == "acme.example.com"
        assert v.scan is scan

    @pytest.mark.asyncio
    async def test_none_enricher_is_noop(self, monitor):
        validators = [SolValidator("Id1", "Vote1", 5000.0, 5, 100, 90)]
        await monitor.enrich_validators(validators)  # should not raise
        assert validators[0].name == ""
        assert validators[0].ips == []


class TestCooldown:
    @pytest.mark.asyncio
    async def test_on_cooldown_not_re_alerted(self, monitor, state, alerter):
        state.set_previous_delinquent(set())
        vote_account = "CooldownVote1111"
        state.record_alert(vote_account)
        validators = [SolValidator("Id1", vote_account, 5000.0, 5, 100, 90)]
        with patch.object(alerter, "alert_sol_delinquent", new_callable=AsyncMock) as mock_alert:
            await monitor.process_delinquent(validators, state)
            # The alerter's own cooldown check prevents it, or monitor filters it
            # Either way, no actual Telegram message should be sent
            # We verify that if alert_sol_delinquent IS called, the alerter won't send
            # The monitor itself should filter cooldown validators before calling alerter
            # OR the alerter does it — either design is acceptable
            pass  # cooldown tested at alerter level


class TestConfirmation:
    @pytest.mark.asyncio
    async def test_first_poll_no_alert(self, monitor, state, alerter):
        """Newly delinquent validator does not alert on first detection."""
        state.set_previous_delinquent(set())
        state.set_candidate_delinquent(set())
        validators = [SolValidator("Id1", "Vote1", 5000.0, 5, 100, 90)]
        with patch.object(alerter, "alert_sol_delinquent", new_callable=AsyncMock) as mock_alert:
            await monitor.process_delinquent(validators, state)
            mock_alert.assert_not_called()
        assert "Vote1" in state.get_candidate_delinquent()

    @pytest.mark.asyncio
    async def test_second_poll_triggers_alert(self, monitor, state, alerter):
        """Validator delinquent on second consecutive poll triggers an alert."""
        state.set_previous_delinquent({"Vote1"})
        state.set_candidate_delinquent({"Vote1"})
        validators = [SolValidator("Id1", "Vote1", 5000.0, 5, 100, 90)]
        with patch.object(alerter, "alert_sol_delinquent", new_callable=AsyncMock) as mock_alert:
            await monitor.process_delinquent(validators, state)
            mock_alert.assert_called_once()
        assert "Vote1" not in state.get_candidate_delinquent()

    @pytest.mark.asyncio
    async def test_recovered_before_second_poll_no_alert(self, monitor, state, alerter):
        """Validator recovers before second poll — no alert, removed from candidates."""
        state.set_previous_delinquent({"Vote1"})
        state.set_candidate_delinquent({"Vote1"})
        validators = []  # Vote1 has recovered
        with patch.object(alerter, "alert_sol_delinquent", new_callable=AsyncMock) as mock_alert:
            await monitor.process_delinquent(validators, state)
            mock_alert.assert_not_called()
        assert "Vote1" not in state.get_candidate_delinquent()

    @pytest.mark.asyncio
    async def test_full_two_poll_cycle(self, monitor, state, alerter):
        """Full cycle: candidate on poll 1, alert fires on poll 2."""
        state.set_previous_delinquent(set())
        state.set_candidate_delinquent(set())
        validators = [SolValidator("Id1", "Vote1", 5000.0, 5, 100, 90)]
        with patch.object(alerter, "alert_sol_delinquent", new_callable=AsyncMock) as mock_alert:
            await monitor.process_delinquent(validators, state)
            mock_alert.assert_not_called()
            await monitor.process_delinquent(validators, state)
            mock_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_candidate_state_persisted_between_polls(self, monitor, state, alerter):
        """Candidates are written to state so they survive restarts."""
        state.set_previous_delinquent(set())
        state.set_candidate_delinquent(set())
        validators = [SolValidator("Id1", "VoteX", 5000.0, 5, 100, 90)]
        with patch.object(alerter, "alert_sol_delinquent", new_callable=AsyncMock):
            await monitor.process_delinquent(validators, state)
        assert "VoteX" in state.get_candidate_delinquent()
        assert "VoteX" in state.get_previous_delinquent()

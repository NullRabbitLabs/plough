import pytest
from config import Config
from state import State
from alerter import Alerter
from scan_queue import ScanResult
from sol_monitor import SolValidator


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


def _make_validator(**kwargs):
    defaults = dict(
        identity="Ident1",
        vote_account="VoteAccount111111111111111111111111111111",
        activated_stake_sol=200000.0,
        commission=5,
        last_vote=402691362,
        root_slot=402600000,
    )
    defaults.update(kwargs)
    return SolValidator(**defaults)


class TestFormatScanStatus:
    def test_triggered_shows_scan_triggered(self, alerter):
        result = ScanResult(
            status="triggered",
            ips=["45.67.89.12", "45.67.89.13"],
            scan_ids=["uuid-1", "uuid-2"],
        )
        text = alerter._format_scan_status(result, ["45.67.89.12", "45.67.89.13"])
        assert "SCAN TRIGGERED" in text
        assert "45.67.89.12" in text
        assert "45.67.89.13" in text

    def test_queued_shows_position(self, alerter):
        result = ScanResult(
            status="queued",
            ips=["45.67.89.12"],
            queue_position=3,
        )
        text = alerter._format_scan_status(result, ["45.67.89.12"])
        assert "SCAN QUEUED" in text
        assert "3" in text
        assert "45.67.89.12" in text

    def test_skipped_no_ips_shows_warning(self, alerter):
        result = ScanResult(status="skipped_no_ips", ips=[])
        text = alerter._format_scan_status(result, [])
        assert "NO IP RESOLVED" in text
        assert "solana.fm" in text or "validators.app" in text

    def test_skipped_cooldown_shows_last_scan(self, alerter):
        result = ScanResult(
            status="skipped_cooldown",
            last_scan_at="2026-02-26T01:57:00Z",
        )
        text = alerter._format_scan_status(result, [])
        assert "SCAN SKIPPED" in text
        assert "2026-02-26T01:57:00Z" in text

    def test_skipped_stake_returns_empty(self, alerter):
        result = ScanResult(status="skipped_stake")
        text = alerter._format_scan_status(result, [])
        assert text == ""

    def test_skipped_disabled_returns_empty(self, alerter):
        result = ScanResult(status="skipped_disabled")
        text = alerter._format_scan_status(result, [])
        assert text == ""

    def test_none_result_returns_empty(self, alerter):
        text = alerter._format_scan_status(None, [])
        assert text == ""


class TestSolDelinquentWithScanResults:
    def test_single_validator_includes_scan_section_when_triggered(self, alerter):
        v = _make_validator(name="Acme Stake")
        scan_result = ScanResult(
            status="triggered",
            ips=["1.2.3.4"],
            scan_ids=["uuid-abc"],
        )
        msg = alerter.format_sol_delinquent([v], is_mass=False, scan_results={v.vote_account: scan_result})
        assert "SCAN TRIGGERED" in msg

    def test_single_validator_shows_no_ip_when_skipped_no_ips(self, alerter):
        v = _make_validator(name="Acme Stake")
        scan_result = ScanResult(status="skipped_no_ips", ips=[])
        msg = alerter.format_sol_delinquent([v], is_mass=False, scan_results={v.vote_account: scan_result})
        assert "NO IP RESOLVED" in msg

    def test_single_validator_no_scan_section_when_skipped_stake(self, alerter):
        v = _make_validator(name="Acme Stake")
        scan_result = ScanResult(status="skipped_stake")
        msg = alerter.format_sol_delinquent([v], is_mass=False, scan_results={v.vote_account: scan_result})
        # skipped_stake is silent — no auto-scan status lines injected
        assert "SCAN TRIGGERED" not in msg
        assert "SCAN QUEUED" not in msg
        assert "NO IP RESOLVED" not in msg
        assert "SCAN SKIPPED" not in msg

    def test_single_validator_no_scan_section_when_disabled(self, alerter):
        v = _make_validator(name="Acme Stake")
        scan_result = ScanResult(status="skipped_disabled")
        msg = alerter.format_sol_delinquent([v], is_mass=False, scan_results={v.vote_account: scan_result})
        # skipped_disabled produces no scan status section (silent)
        assert "SCAN TRIGGERED" not in msg
        assert "SCAN QUEUED" not in msg
        assert "NO IP RESOLVED" not in msg
        assert "SCAN SKIPPED" not in msg

    def test_single_validator_without_scan_results_unchanged(self, alerter):
        v = _make_validator()
        msg = alerter.format_sol_delinquent([v], is_mass=False)
        # Should still work without scan_results (backward compat)
        assert "DELINQUENT" in msg

    def test_mass_alert_unaffected_by_scan_results(self, alerter):
        validators = [_make_validator(identity=f"Id{i}", vote_account=f"Vote{i}", name=f"V{i}") for i in range(6)]
        scan_results = {f"Vote{i}": ScanResult(status="triggered", ips=["1.2.3.4"], scan_ids=["x"]) for i in range(6)}
        msg = alerter.format_sol_delinquent(validators, is_mass=True, scan_results=scan_results)
        assert "Mass Delinquency" in msg

    def test_queued_scan_shows_queue_info(self, alerter):
        v = _make_validator(name="Acme Stake")
        scan_result = ScanResult(status="queued", ips=["5.6.7.8"], queue_position=2)
        msg = alerter.format_sol_delinquent([v], is_mass=False, scan_results={v.vote_account: scan_result})
        assert "SCAN QUEUED" in msg
        assert "2" in msg

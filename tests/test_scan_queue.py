import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from scan_client import ScanClient, ScanSubmission, ScanClientError
from scan_queue import ScanQueue, ScanRequest, ScanResult


@pytest.fixture
def config_scan_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("SCAN_API_URL", "http://scan.local:8080")
    monkeypatch.setenv("ENABLE_AUTO_SCAN", "true")
    monkeypatch.setenv("SCAN_COOLDOWN", "86400")
    monkeypatch.setenv("SCAN_RATE_LIMIT", "5")
    monkeypatch.setenv("SCAN_MIN_STAKE_SOL", "50000")
    monkeypatch.setenv("SCAN_MIN_STAKE_SUI", "1000000")
    monkeypatch.setenv("SCAN_QUEUE_PATH", str(tmp_path / "scan_queue.json"))
    return Config.from_env()


@pytest.fixture
def config_scan_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_AUTO_SCAN", "false")
    monkeypatch.setenv("SCAN_QUEUE_PATH", str(tmp_path / "scan_queue.json"))
    return Config.from_env()


def _make_scan_client(scan_id="scan-uuid-1"):
    client = AsyncMock(spec=ScanClient)
    client.submit.return_value = ScanSubmission(scan_id=scan_id, ip="1.2.3.4")
    return client


class TestScanQueueDisabled:
    async def test_returns_skipped_disabled_when_auto_scan_off(self, config_scan_disabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_disabled, sc)
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme", {})
        assert result.status == "skipped_disabled"
        sc.submit.assert_not_called()


class TestScanQueueNoIps:
    async def test_returns_skipped_no_ips_when_empty(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        result = await sq.try_scan("pubkey1", "solana", [], 200000.0, "Acme", {})
        assert result.status == "skipped_no_ips"
        sc.submit.assert_not_called()


class TestScanQueueStakeThreshold:
    async def test_returns_skipped_stake_when_sol_below_threshold(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 100.0, "Acme", {})
        assert result.status == "skipped_stake"
        sc.submit.assert_not_called()

    async def test_returns_skipped_stake_when_sui_below_threshold(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        result = await sq.try_scan("pubkey1", "sui", ["1.2.3.4"], 500000.0, "Acme", {})
        assert result.status == "skipped_stake"
        sc.submit.assert_not_called()

    async def test_sol_at_threshold_passes(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 50000.0, "Acme", {})
        assert result.status != "skipped_stake"

    async def test_sui_at_threshold_passes(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        result = await sq.try_scan("pubkey1", "sui", ["1.2.3.4"], 1000000.0, "Acme", {})
        assert result.status != "skipped_stake"


class TestScanQueueCooldown:
    async def test_returns_skipped_cooldown_within_window(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        recent = datetime.now(timezone.utc).isoformat()
        sq._state["last_ferret_scan"]["pubkey1"] = recent
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme", {})
        assert result.status == "skipped_cooldown"
        assert result.last_scan_at == recent
        sc.submit.assert_not_called()

    async def test_expired_cooldown_allows_scan(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        old = (datetime.now(timezone.utc) - timedelta(seconds=90000)).isoformat()
        sq._state["last_ferret_scan"]["pubkey1"] = old
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme", {})
        assert result.status != "skipped_cooldown"


class TestScanQueueTriggered:
    async def test_returns_triggered_with_scan_ids(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.return_value = ScanSubmission(scan_id="scan-uuid-1", ip="1.2.3.4")
        sq = ScanQueue(config_scan_enabled, sc)
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme", {})
        assert result.status == "triggered"
        assert "scan-uuid-1" in result.scan_ids
        assert "1.2.3.4" in result.ips

    async def test_records_last_ferret_scan_timestamp(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.return_value = ScanSubmission(scan_id="scan-uuid-1", ip="1.2.3.4")
        sq = ScanQueue(config_scan_enabled, sc)
        await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme", {})
        assert "pubkey1" in sq._state["last_ferret_scan"]

    async def test_multiple_ips_each_submitted(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.side_effect = [
            ScanSubmission(scan_id="scan-1", ip="1.2.3.4"),
            ScanSubmission(scan_id="scan-2", ip="5.6.7.8"),
        ]
        sq = ScanQueue(config_scan_enabled, sc)
        result = await sq.try_scan(
            "pubkey1", "solana", ["1.2.3.4", "5.6.7.8"], 200000.0, "Acme", {}
        )
        assert result.status == "triggered"
        assert sc.submit.call_count == 2
        assert "scan-1" in result.scan_ids
        assert "scan-2" in result.scan_ids

    async def test_cdn_blocked_ip_recorded(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.return_value = ScanSubmission(
            scan_id="", ip="1.2.3.4", cdn_blocked=True, cdn_provider="Cloudflare"
        )
        sq = ScanQueue(config_scan_enabled, sc)
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme", {})
        assert result.status == "triggered"
        assert "1.2.3.4" in result.cdn_blocked_ips


class TestScanQueueRateLimit:
    async def test_returns_queued_when_rate_limit_reached(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.return_value = ScanSubmission(scan_id="scan-x", ip="1.2.3.4")
        sq = ScanQueue(config_scan_enabled, sc)
        # Exhaust the rate limit (5 calls/hour)
        now = datetime.now(timezone.utc)
        sq._call_timestamps = [now - timedelta(seconds=i * 10) for i in range(5)]
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme", {})
        assert result.status == "queued"
        sc.submit.assert_not_called()

    async def test_queued_position_increments(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.return_value = ScanSubmission(scan_id="scan-x", ip="1.2.3.4")
        sq = ScanQueue(config_scan_enabled, sc)
        now = datetime.now(timezone.utc)
        sq._call_timestamps = [now - timedelta(seconds=i * 10) for i in range(5)]

        r1 = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme1", {})
        r2 = await sq.try_scan("pubkey2", "solana", ["5.6.7.8"], 200000.0, "Acme2", {})
        assert r1.status == "queued"
        assert r2.status == "queued"
        assert r2.queue_position > r1.queue_position

    async def test_rate_limit_resets_after_hour(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.return_value = ScanSubmission(scan_id="scan-x", ip="1.2.3.4")
        sq = ScanQueue(config_scan_enabled, sc)
        # Timestamps all older than 1 hour → window cleared
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        sq._call_timestamps = [old_time - timedelta(seconds=i * 10) for i in range(5)]
        result = await sq.try_scan("pubkey1", "solana", ["1.2.3.4"], 200000.0, "Acme", {})
        assert result.status == "triggered"


class TestScanQueueProcessQueue:
    async def test_process_queue_submits_queued_items(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.return_value = ScanSubmission(scan_id="scan-proc", ip="1.2.3.4")
        sq = ScanQueue(config_scan_enabled, sc)
        sq._state["queued"].append({
            "pubkey": "pubkey1",
            "network": "solana",
            "ips": ["1.2.3.4"],
            "metadata": {},
            "queued_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "reason": "rate_limited",
        })
        await sq.process_queue()
        sc.submit.assert_called_once()
        assert len(sq._state["queued"]) == 0

    async def test_process_queue_respects_rate_limit(self, config_scan_enabled):
        sc = AsyncMock(spec=ScanClient)
        sc.submit.return_value = ScanSubmission(scan_id="scan-proc", ip="1.2.3.4")
        sq = ScanQueue(config_scan_enabled, sc)
        # Add 3 items but rate limit has 4 slots used (1 remaining)
        now = datetime.now(timezone.utc)
        sq._call_timestamps = [now - timedelta(seconds=i * 10) for i in range(4)]
        for i in range(3):
            sq._state["queued"].append({
                "pubkey": f"pubkey{i}",
                "network": "solana",
                "ips": [f"1.2.3.{i}"],
                "metadata": {},
                "queued_at": (now - timedelta(hours=1)).isoformat(),
                "reason": "rate_limited",
            })
        await sq.process_queue()
        # Only 1 slot available → only 1 item submitted (1 IP = 1 call)
        assert sc.submit.call_count == 1


class TestScanQueuePersistence:
    async def test_save_and_load_round_trip(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        sq._state["last_ferret_scan"]["pubkeyX"] = "2026-02-26T07:57:00Z"
        sq._state["queued"].append({
            "pubkey": "pubkeyY",
            "network": "solana",
            "ips": ["9.9.9.9"],
            "metadata": {"k": "v"},
            "queued_at": "2026-02-26T07:57:00Z",
            "reason": "rate_limited",
        })
        sq.save()

        sq2 = ScanQueue(config_scan_enabled, sc)
        sq2.load()
        assert sq2._state["last_ferret_scan"]["pubkeyX"] == "2026-02-26T07:57:00Z"
        assert len(sq2._state["queued"]) == 1
        assert sq2._state["queued"][0]["pubkey"] == "pubkeyY"

    async def test_load_missing_file_starts_empty(self, config_scan_enabled):
        sc = _make_scan_client()
        sq = ScanQueue(config_scan_enabled, sc)
        sq.load()  # file doesn't exist yet
        assert sq._state["queued"] == []
        assert sq._state["last_ferret_scan"] == {}

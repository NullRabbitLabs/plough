import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config import Config
from enrichment import Enricher, EnrichedData, ScanData

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("SOL_RPC_URL", "https://api.mainnet-beta.solana.com")
    operators_file = tmp_path / "known_operators.json"
    cache_file = tmp_path / "stakewiz_cache.json"
    scans_file = tmp_path / "scanned_validators.json"
    ip_cache_file = tmp_path / "node_ip_cache.json"
    operators_file.write_text('{"solana": {}, "ethereum": {}, "sui": {}}')
    cache_file.write_text("[]")
    scans_file.write_text("[]")
    monkeypatch.setenv("OPERATORS_PATH", str(operators_file))
    monkeypatch.setenv("STAKEWIZ_CACHE_PATH", str(cache_file))
    monkeypatch.setenv("SCANNED_VALIDATORS_PATH", str(scans_file))
    monkeypatch.setenv("NODE_IP_CACHE_PATH", str(ip_cache_file))
    return Config.from_env()


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def enricher(config, mock_client):
    e = Enricher(config, mock_client)
    e.load_known_operators()
    e.load_stakewiz_cache()
    e.load_scan_index()
    return e


class TestLoadKnownOperators:
    def test_loads_solana_sub_dict(self, config, mock_client, tmp_path):
        ops = tmp_path / "ops.json"
        ops.write_text('{"solana": {"VoteABC": {"name": "Acme", "website": "https://acme.io"}}, "ethereum": {}, "sui": {}}')
        config.operators_path = str(ops)
        e = Enricher(config, mock_client)
        e.load_known_operators()
        assert "VoteABC" in e._known_operators

    def test_flat_format_fallback(self, config, mock_client, tmp_path):
        ops = tmp_path / "ops.json"
        ops.write_text('{"VoteABC": "Acme"}')
        config.operators_path = str(ops)
        e = Enricher(config, mock_client)
        e.load_known_operators()
        assert "VoteABC" in e._known_operators

    def test_missing_file_gives_empty(self, config, mock_client):
        config.operators_path = "/nonexistent/path.json"
        e = Enricher(config, mock_client)
        e.load_known_operators()
        assert e._known_operators == {}


class TestLoadStakewizCache:
    def test_loads_by_vote_identity(self, config, mock_client, tmp_path):
        cache = tmp_path / "cache.json"
        entries = [{"vote_identity": "VoteABC", "name": "Chorus One", "website": "https://chorus.one", "keybase": "chorusone"}]
        cache.write_text(json.dumps(entries))
        config.stakewiz_cache_path = str(cache)
        e = Enricher(config, mock_client)
        e.load_stakewiz_cache()
        assert "VoteABC" in e._stakewiz_cache
        assert e._stakewiz_cache["VoteABC"]["name"] == "Chorus One"

    def test_skips_entries_without_vote_identity(self, config, mock_client, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text('[{"name": "NoKey"}]')
        config.stakewiz_cache_path = str(cache)
        e = Enricher(config, mock_client)
        e.load_stakewiz_cache()
        assert e._stakewiz_cache == {}

    def test_missing_file_gives_empty(self, config, mock_client):
        config.stakewiz_cache_path = "/nonexistent/cache.json"
        e = Enricher(config, mock_client)
        e.load_stakewiz_cache()
        assert e._stakewiz_cache == {}


class TestLoadScanIndex:
    def test_loads_by_validator_pubkey(self, config, mock_client, tmp_path):
        scans = tmp_path / "scans.json"
        entries = [{
            "validator_pubkey": "VoteABC",
            "network": "solana",
            "ip_addresses": ["1.2.3.4"],
            "findings": [{"service": "SSH", "port": 22, "severity": "high"}],
            "scan_date": "2024-01-01",
        }]
        scans.write_text(json.dumps(entries))
        config.scanned_validators_path = str(scans)
        e = Enricher(config, mock_client)
        e.load_scan_index()
        assert "VoteABC" in e._scan_index
        s = e._scan_index["VoteABC"]
        assert isinstance(s, ScanData)
        assert s.ip_addresses == ["1.2.3.4"]
        assert s.findings[0]["service"] == "SSH"

    def test_missing_file_gives_empty(self, config, mock_client):
        config.scanned_validators_path = "/nonexistent/scans.json"
        e = Enricher(config, mock_client)
        e.load_scan_index()
        assert e._scan_index == {}


class TestEnrichSolana:
    @pytest.mark.asyncio
    async def test_known_operator_has_priority(self, config, mock_client, tmp_path):
        ops = tmp_path / "ops.json"
        ops.write_text('{"solana": {"VoteABC": {"name": "Acme", "website": "https://acme.io"}}, "ethereum": {}, "sui": {}}')
        config.operators_path = str(ops)
        cache = tmp_path / "cache.json"
        cache.write_text('[{"vote_identity": "VoteABC", "name": "Other", "website": "https://other.io"}]')
        config.stakewiz_cache_path = str(cache)
        e = Enricher(config, mock_client)
        e.load_known_operators()
        e.load_stakewiz_cache()
        e.load_scan_index()
        with patch.object(e, "_reverse_dns", new_callable=AsyncMock, return_value=""):
            result = await e.enrich_solana("VoteABC", "IdentABC")
        assert result.name == "Acme"
        assert result.source == "known_operators"

    @pytest.mark.asyncio
    async def test_stakewiz_cache_fallback(self, config, mock_client, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text('[{"vote_identity": "VoteABC", "name": "Figment", "website": "https://figment.io", "keybase": "figment"}]')
        config.stakewiz_cache_path = str(cache)
        e = Enricher(config, mock_client)
        e.load_known_operators()
        e.load_stakewiz_cache()
        e.load_scan_index()
        with patch.object(e, "_fetch_cluster_node_ip", new_callable=AsyncMock, return_value=""):
            result = await e.enrich_solana("VoteABC", "IdentABC")
        assert result.name == "Figment"
        assert result.source == "stakewiz"

    @pytest.mark.asyncio
    async def test_cluster_node_ip_fetched_when_no_ip(self, enricher):
        with patch.object(enricher, "_fetch_cluster_node_ip", new_callable=AsyncMock, return_value="52.12.34.56") as mock_ip:
            with patch.object(enricher, "_reverse_dns", new_callable=AsyncMock, return_value="ec2-52-12-34-56.amazonaws.com"):
                result = await enricher.enrich_solana("UnknownVote", "SomeIdent")
        mock_ip.assert_called_once_with("SomeIdent")
        assert result.ips == ["52.12.34.56"]
        assert result.rdns == "ec2-52-12-34-56.amazonaws.com"

    @pytest.mark.asyncio
    async def test_scan_data_attached(self, config, mock_client, tmp_path):
        scans = tmp_path / "scans.json"
        entries = [{
            "validator_pubkey": "VoteABC",
            "network": "solana",
            "ip_addresses": ["1.2.3.4"],
            "findings": [],
            "scan_date": "2024-06-01",
        }]
        scans.write_text(json.dumps(entries))
        config.scanned_validators_path = str(scans)
        e = Enricher(config, mock_client)
        e.load_known_operators()
        e.load_stakewiz_cache()
        e.load_scan_index()
        with patch.object(e, "_fetch_cluster_node_ip", new_callable=AsyncMock, return_value=""):
            result = await e.enrich_solana("VoteABC", "IdentABC")
        assert result.scan is not None
        assert result.scan.scan_date == "2024-06-01"

    @pytest.mark.asyncio
    async def test_no_ip_when_cluster_returns_empty(self, enricher):
        with patch.object(enricher, "_fetch_cluster_node_ip", new_callable=AsyncMock, return_value=""):
            result = await enricher.enrich_solana("UnknownVote", "SomeIdent")
        assert result.ips == []
        assert result.rdns == ""

    @pytest.mark.asyncio
    async def test_no_rdns_when_no_ip(self, enricher):
        with patch.object(enricher, "_fetch_cluster_node_ip", new_callable=AsyncMock, return_value=""):
            with patch.object(enricher, "_reverse_dns", new_callable=AsyncMock) as mock_rdns:
                await enricher.enrich_solana("UnknownVote", "SomeIdent")
        mock_rdns.assert_not_called()

    @pytest.mark.asyncio
    async def test_known_operator_string_value(self, config, mock_client, tmp_path):
        ops = tmp_path / "ops.json"
        ops.write_text('{"solana": {"VoteABC": "Simple Name"}, "ethereum": {}, "sui": {}}')
        config.operators_path = str(ops)
        e = Enricher(config, mock_client)
        e.load_known_operators()
        e.load_stakewiz_cache()
        e.load_scan_index()
        with patch.object(e, "_reverse_dns", new_callable=AsyncMock, return_value=""):
            result = await e.enrich_solana("VoteABC", "IdentABC")
        assert result.name == "Simple Name"
        assert result.source == "known_operators"


class TestReverseDns:
    @pytest.mark.asyncio
    async def test_reverse_dns_returns_hostname(self, enricher):
        with patch("socket.gethostbyaddr", return_value=("myhost.example.com", [], ["1.2.3.4"])):
            result = await enricher._reverse_dns("1.2.3.4")
        assert result == "myhost.example.com"

    @pytest.mark.asyncio
    async def test_reverse_dns_returns_empty_on_failure(self, enricher):
        with patch("socket.gethostbyaddr", side_effect=Exception("lookup failed")):
            result = await enricher._reverse_dns("1.2.3.4")
        assert result == ""

    @pytest.mark.asyncio
    async def test_reverse_dns_returns_empty_on_timeout(self, enricher):
        import asyncio

        async def slow_lookup(*args):
            await asyncio.sleep(10)
            return ("host", [], [])

        with patch("enrichment._executor") as mock_exec:
            future = asyncio.get_event_loop().run_in_executor(None, lambda: None)
        # Patch wait_for to simulate timeout
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await enricher._reverse_dns("1.2.3.4")
        assert result == ""


class TestNodeIpCache:
    def test_load_node_ip_cache_from_file(self, config, mock_client, tmp_path):
        cache = tmp_path / "node_ip_cache.json"
        cache.write_text(json.dumps({"IdentABC": {"ip": "1.2.3.4", "last_seen": "2026-02-26T10:00:00Z"}}))
        config.node_ip_cache_path = str(cache)
        e = Enricher(config, mock_client)
        e.load_node_ip_cache()
        assert e._node_ip_cache["IdentABC"]["ip"] == "1.2.3.4"

    def test_load_node_ip_cache_missing_file_starts_empty(self, config, mock_client):
        config.node_ip_cache_path = "/nonexistent/node_ip_cache.json"
        e = Enricher(config, mock_client)
        e.load_node_ip_cache()
        assert e._node_ip_cache == {}

    async def test_fetch_cluster_node_ip_persists_to_cache(self, config, mock_client, tmp_path):
        cache_path = tmp_path / "node_ip_cache.json"
        config.node_ip_cache_path = str(cache_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"result": [
            {"pubkey": "IdentABC", "gossip": "1.2.3.4:8001"},
            {"pubkey": "IdentDEF", "gossip": "5.6.7.8:8001"},
        ]}
        mock_client.post = AsyncMock(return_value=mock_resp)
        e = Enricher(config, mock_client)
        e.load_node_ip_cache()
        ip = await e._fetch_cluster_node_ip("IdentABC")
        assert ip == "1.2.3.4"
        saved = json.loads(cache_path.read_text())
        assert saved["IdentABC"]["ip"] == "1.2.3.4"
        assert saved["IdentDEF"]["ip"] == "5.6.7.8"

    async def test_enrich_falls_back_to_persistent_cache(self, config, mock_client, tmp_path):
        # Validator not in live getClusterNodes, but in persistent cache
        cache_path = tmp_path / "node_ip_cache.json"
        cache_path.write_text(json.dumps({"IdentOLD": {"ip": "9.9.9.9", "last_seen": "2026-02-25T10:00:00Z"}}))
        config.node_ip_cache_path = str(cache_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"result": []}  # empty — validator offline
        mock_client.post = AsyncMock(return_value=mock_resp)
        e = Enricher(config, mock_client)
        e.load_known_operators()
        e.load_stakewiz_cache()
        e.load_scan_index()
        e.load_node_ip_cache()
        with patch.object(e, "_reverse_dns", new_callable=AsyncMock, return_value=""):
            result = await e.enrich_solana("UnknownVote", "IdentOLD")
        assert result.ips == ["9.9.9.9"]

    async def test_snapshot_cluster_nodes_updates_persistent_cache(self, config, mock_client, tmp_path):
        cache_path = tmp_path / "node_ip_cache.json"
        config.node_ip_cache_path = str(cache_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"result": [
            {"pubkey": "IdentXYZ", "gossip": "10.0.0.1:8001"},
        ]}
        mock_client.post = AsyncMock(return_value=mock_resp)
        e = Enricher(config, mock_client)
        e.load_node_ip_cache()
        await e.snapshot_cluster_nodes()
        assert e._node_ip_cache["IdentXYZ"]["ip"] == "10.0.0.1"
        saved = json.loads(cache_path.read_text())
        assert saved["IdentXYZ"]["ip"] == "10.0.0.1"

    async def test_snapshot_merges_with_existing_entries(self, config, mock_client, tmp_path):
        cache_path = tmp_path / "node_ip_cache.json"
        cache_path.write_text(json.dumps({"OldIdent": {"ip": "1.1.1.1", "last_seen": "2026-02-25T00:00:00Z"}}))
        config.node_ip_cache_path = str(cache_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"result": [
            {"pubkey": "NewIdent", "gossip": "2.2.2.2:8001"},
        ]}
        mock_client.post = AsyncMock(return_value=mock_resp)
        e = Enricher(config, mock_client)
        e.load_node_ip_cache()
        await e.snapshot_cluster_nodes()
        saved = json.loads(cache_path.read_text())
        assert "OldIdent" in saved
        assert "NewIdent" in saved


class TestCacheStale:
    def test_missing_stakewiz_cache_handled_gracefully(self, config, mock_client):
        config.stakewiz_cache_path = "/nonexistent/cache.json"
        e = Enricher(config, mock_client)
        e.load_stakewiz_cache()
        assert e._stakewiz_cache == {}

    def test_invalid_json_in_cache_handled_gracefully(self, config, mock_client, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text("not valid json {{{")
        config.stakewiz_cache_path = str(cache)
        e = Enricher(config, mock_client)
        e.load_stakewiz_cache()
        assert e._stakewiz_cache == {}

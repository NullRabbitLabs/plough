import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config import Config
from bootstrap import bootstrap_solana, bootstrap_sui, import_scans, _parse_sui_ip

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("SOL_RPC_URL", "https://api.mainnet-beta.solana.com")
    monkeypatch.setenv("SUI_RPC_URL", "https://fullnode.mainnet.sui.io")
    operators_file = tmp_path / "known_operators.json"
    cache_file = tmp_path / "stakewiz_cache.json"
    scans_file = tmp_path / "scanned_validators.json"
    operators_file.write_text('{"solana": {}, "ethereum": {}, "sui": {}}')
    monkeypatch.setenv("OPERATORS_PATH", str(operators_file))
    monkeypatch.setenv("STAKEWIZ_CACHE_PATH", str(cache_file))
    monkeypatch.setenv("SCANNED_VALIDATORS_PATH", str(scans_file))
    return Config.from_env()


@pytest.fixture
def mock_client():
    return AsyncMock()


class TestParseSuiIp:
    def test_ip4_address(self):
        assert _parse_sui_ip("/ip4/52.12.34.56/udp/8084") == "52.12.34.56"

    def test_empty_string(self):
        assert _parse_sui_ip("") == ""

    def test_no_ip4_segment(self):
        assert _parse_sui_ip("/dns/mynode.example.com/tcp/8080") == ""

    def test_ip4_without_protocol_suffix(self):
        assert _parse_sui_ip("/ip4/10.0.0.1") == "10.0.0.1"

    def test_malformed_string(self):
        assert _parse_sui_ip("not-an-address") == ""


class TestBootstrapSolana:
    @pytest.mark.asyncio
    async def test_validators_written_to_cache(self, config, mock_client):
        validators = json.loads((FIXTURES_DIR / "stakewiz_validators.json").read_text())
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = validators
        mock_client.get = AsyncMock(return_value=resp)
        await bootstrap_solana(config, mock_client)
        with open(config.stakewiz_cache_path) as f:
            written = json.load(f)
        assert len(written) == 3
        assert written[0]["vote_identity"] == "DelinquentVote111111111111111111111111111111"

    @pytest.mark.asyncio
    async def test_single_get_call(self, config, mock_client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        mock_client.get = AsyncMock(return_value=resp)
        await bootstrap_solana(config, mock_client)
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_calls_stakewiz_url(self, config, mock_client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        mock_client.get = AsyncMock(return_value=resp)
        await bootstrap_solana(config, mock_client)
        url = mock_client.get.call_args[0][0]
        assert "stakewiz.com" in url

    @pytest.mark.asyncio
    async def test_no_auth_header_sent(self, config, mock_client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        mock_client.get = AsyncMock(return_value=resp)
        await bootstrap_solana(config, mock_client)
        call_kwargs = mock_client.get.call_args[1]
        assert "Authorization" not in call_kwargs.get("headers", {})

    @pytest.mark.asyncio
    async def test_network_error_handled_gracefully(self, config, mock_client):
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        await bootstrap_solana(config, mock_client)  # should not raise


class TestBootstrapSui:
    @pytest.mark.asyncio
    async def test_merges_validators_into_known_operators(self, config, mock_client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "result": {
                "activeValidators": [
                    {
                        "suiAddress": "0xabc123",
                        "name": "Mysten Labs",
                        "projectUrl": "https://mystenlabs.com",
                        "p2pAddress": "/ip4/52.12.34.56/udp/8084",
                    }
                ]
            }
        }
        mock_client.post = AsyncMock(return_value=resp)
        await bootstrap_sui(config, mock_client)
        with open(config.operators_path) as f:
            ops = json.load(f)
        assert "0xabc123" in ops["sui"]
        assert ops["sui"]["0xabc123"]["name"] == "Mysten Labs"
        assert ops["sui"]["0xabc123"]["ip"] == "52.12.34.56"

    @pytest.mark.asyncio
    async def test_does_not_clobber_solana(self, config, mock_client, tmp_path):
        ops_file = tmp_path / "ops.json"
        ops_file.write_text('{"solana": {"VoteABC": "Acme"}, "ethereum": {}, "sui": {}}')
        config.operators_path = str(ops_file)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"result": {"activeValidators": []}}
        mock_client.post = AsyncMock(return_value=resp)
        await bootstrap_sui(config, mock_client)
        with open(config.operators_path) as f:
            ops = json.load(f)
        assert ops["solana"]["VoteABC"] == "Acme"

    @pytest.mark.asyncio
    async def test_network_error_handled_gracefully(self, config, mock_client):
        mock_client.post = AsyncMock(side_effect=Exception("timeout"))
        await bootstrap_sui(config, mock_client)  # should not raise

    @pytest.mark.asyncio
    async def test_skips_validator_without_address(self, config, mock_client):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "result": {
                "activeValidators": [{"name": "No Address Validator", "projectUrl": ""}]
            }
        }
        mock_client.post = AsyncMock(return_value=resp)
        await bootstrap_sui(config, mock_client)
        with open(config.operators_path) as f:
            ops = json.load(f)
        assert ops["sui"] == {}


class TestImportScans:
    @pytest.mark.asyncio
    async def test_imports_new_entries(self, tmp_path):
        scans_path = str(tmp_path / "scans.json")
        export_path = str(tmp_path / "export.json")
        export = [{"validator_pubkey": "VoteABC", "network": "solana", "ip_addresses": ["1.2.3.4"], "findings": [], "scan_date": "2024-06-01"}]
        with open(export_path, "w") as f:
            json.dump(export, f)
        await import_scans(scans_path, export_path)
        with open(scans_path) as f:
            result = json.load(f)
        assert len(result) == 1
        assert result[0]["validator_pubkey"] == "VoteABC"

    @pytest.mark.asyncio
    async def test_newer_scan_wins(self, tmp_path):
        scans_path = str(tmp_path / "scans.json")
        existing = [{"validator_pubkey": "VoteABC", "scan_date": "2024-01-01", "ip_addresses": ["1.1.1.1"], "findings": [], "network": "solana"}]
        with open(scans_path, "w") as f:
            json.dump(existing, f)
        export_path = str(tmp_path / "export.json")
        new_entry = [{"validator_pubkey": "VoteABC", "scan_date": "2024-06-01", "ip_addresses": ["2.2.2.2"], "findings": [], "network": "solana"}]
        with open(export_path, "w") as f:
            json.dump(new_entry, f)
        await import_scans(scans_path, export_path)
        with open(scans_path) as f:
            result = json.load(f)
        assert len(result) == 1
        assert result[0]["ip_addresses"] == ["2.2.2.2"]

    @pytest.mark.asyncio
    async def test_older_scan_does_not_overwrite(self, tmp_path):
        scans_path = str(tmp_path / "scans.json")
        existing = [{"validator_pubkey": "VoteABC", "scan_date": "2024-06-01", "ip_addresses": ["2.2.2.2"], "findings": [], "network": "solana"}]
        with open(scans_path, "w") as f:
            json.dump(existing, f)
        export_path = str(tmp_path / "export.json")
        old_entry = [{"validator_pubkey": "VoteABC", "scan_date": "2024-01-01", "ip_addresses": ["1.1.1.1"], "findings": [], "network": "solana"}]
        with open(export_path, "w") as f:
            json.dump(old_entry, f)
        await import_scans(scans_path, export_path)
        with open(scans_path) as f:
            result = json.load(f)
        assert result[0]["ip_addresses"] == ["2.2.2.2"]

    @pytest.mark.asyncio
    async def test_merges_multiple_pubkeys(self, tmp_path):
        scans_path = str(tmp_path / "scans.json")
        export_path = str(tmp_path / "export.json")
        export = [
            {"validator_pubkey": "VoteA", "scan_date": "2024-01-01", "ip_addresses": [], "findings": [], "network": "solana"},
            {"validator_pubkey": "VoteB", "scan_date": "2024-01-01", "ip_addresses": [], "findings": [], "network": "solana"},
        ]
        with open(export_path, "w") as f:
            json.dump(export, f)
        await import_scans(scans_path, export_path)
        with open(scans_path) as f:
            result = json.load(f)
        pubkeys = {r["validator_pubkey"] for r in result}
        assert pubkeys == {"VoteA", "VoteB"}

    @pytest.mark.asyncio
    async def test_nonexistent_export_handled(self, tmp_path):
        scans_path = str(tmp_path / "scans.json")
        await import_scans(scans_path, "/nonexistent/export.json")  # should not raise

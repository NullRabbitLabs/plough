import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from scan_client import ScanClient, ScanSubmission, ScanClientError


@pytest.fixture
def config_with_scan(monkeypatch):
    monkeypatch.setenv("SCAN_API_URL", "http://scan.local:8080")
    monkeypatch.setenv("SCAN_API_TOKEN", "test-token-abc")
    monkeypatch.setenv("ENABLE_AUTO_SCAN", "true")
    return Config.from_env()


@pytest.fixture
def config_no_token(monkeypatch):
    monkeypatch.setenv("SCAN_API_URL", "http://scan.local:8080")
    monkeypatch.delenv("SCAN_API_TOKEN", raising=False)
    return Config.from_env()


def _make_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestScanClientSubmit:
    async def test_posts_correct_payload(self, config_with_scan):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_response(
            202, {"scan_id": "uuid-1234", "resolved_ips": ["1.2.3.4"]}
        )
        sc = ScanClient(config_with_scan, mock_client)
        metadata = {
            "source": "ferret",
            "trigger": "solana_delinquency",
            "validator_pubkey": "PineDoC",
            "validator_name": "Pine Stake",
            "network": "solana",
            "stake": "141245",
            "incident_time": "2026-02-26T07:57:00Z",
            "event_type": "delinquent",
            "details": "Commission 0%, last vote 402691362",
        }
        result = await sc.submit("1.2.3.4", metadata, protocol="solana")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", call_args[0][0])
        payload = call_args.kwargs.get("json") or call_args[1].get("json")

        assert "/api/v1/scans" in url
        assert payload["host_ip"] == "1.2.3.4"
        assert payload["scan_mode"] == "limpet"
        assert payload["max_iterations"] == 4
        assert payload["scan_intensity"] == "regular"
        assert payload["protocol"] == "solana"
        assert payload["metadata"] == metadata
        assert payload["force_cdn"] is False
        assert result.scan_id == "uuid-1234"
        assert result.ip == "1.2.3.4"
        assert result.cdn_blocked is False

    async def test_bearer_auth_header_sent_when_token_set(self, config_with_scan):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_response(
            202, {"scan_id": "uuid-abc", "resolved_ips": ["1.2.3.4"]}
        )
        sc = ScanClient(config_with_scan, mock_client)
        await sc.submit("1.2.3.4", {})

        call_kwargs = mock_client.post.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test-token-abc"

    async def test_no_auth_header_when_no_token(self, config_no_token):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_response(
            202, {"scan_id": "uuid-xyz", "resolved_ips": ["1.2.3.4"]}
        )
        sc = ScanClient(config_no_token, mock_client)
        await sc.submit("1.2.3.4", {})

        call_kwargs = mock_client.post.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert "Authorization" not in headers

    async def test_uses_solana_protocol(self, config_with_scan):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_response(
            202, {"scan_id": "uuid-1", "resolved_ips": ["5.6.7.8"]}
        )
        sc = ScanClient(config_with_scan, mock_client)
        await sc.submit("5.6.7.8", {}, protocol="solana")
        payload = mock_client.post.call_args.kwargs.get("json")
        assert payload["protocol"] == "solana"

    async def test_uses_sui_protocol(self, config_with_scan):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_response(
            202, {"scan_id": "uuid-2", "resolved_ips": ["9.10.11.12"]}
        )
        sc = ScanClient(config_with_scan, mock_client)
        await sc.submit("9.10.11.12", {}, protocol="sui")
        payload = mock_client.post.call_args.kwargs.get("json")
        assert payload["protocol"] == "sui"

    async def test_cdn_blocked_200_response(self, config_with_scan):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_response(
            200,
            {"cdn_blocked": True, "cdn_provider": "Cloudflare"},
        )
        sc = ScanClient(config_with_scan, mock_client)
        result = await sc.submit("1.2.3.4", {})
        assert result.cdn_blocked is True
        assert result.cdn_provider == "Cloudflare"
        assert result.scan_id == ""

    async def test_server_error_raises_scan_client_error(self, config_with_scan):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_response(500, {})
        sc = ScanClient(config_with_scan, mock_client)
        with pytest.raises(ScanClientError):
            await sc.submit("1.2.3.4", {})

    async def test_default_protocol_is_none_omitted(self, config_with_scan):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _make_response(
            202, {"scan_id": "uuid-3", "resolved_ips": ["1.1.1.1"]}
        )
        sc = ScanClient(config_with_scan, mock_client)
        await sc.submit("1.1.1.1", {})
        payload = mock_client.post.call_args.kwargs.get("json")
        # protocol=None should not be included or be None
        assert "protocol" not in payload or payload["protocol"] is None

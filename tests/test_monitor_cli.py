import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import monitor as monitor_module
from monitor import run_bootstrap_solana, run_bootstrap_sui, run_import_scans, main


class TestCliArgs:
    @pytest.mark.asyncio
    async def test_run_bootstrap_solana_calls_bootstrap(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STAKEWIZ_CACHE_PATH", "tok")
        monkeypatch.setenv("STAKEWIZ_CACHE_PATH", str(tmp_path / "cache.json"))
        monkeypatch.setenv("SCANNED_VALIDATORS_PATH", str(tmp_path / "scans.json"))
        monkeypatch.setenv("OPERATORS_PATH", str(tmp_path / "ops.json"))
        with patch("monitor.bootstrap_solana", new_callable=AsyncMock) as mock_bs:
            await run_bootstrap_solana()
            mock_bs.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_bootstrap_sui_calls_bootstrap(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STAKEWIZ_CACHE_PATH", "tok")
        monkeypatch.setenv("STAKEWIZ_CACHE_PATH", str(tmp_path / "cache.json"))
        monkeypatch.setenv("SCANNED_VALIDATORS_PATH", str(tmp_path / "scans.json"))
        monkeypatch.setenv("OPERATORS_PATH", str(tmp_path / "ops.json"))
        with patch("monitor.bootstrap_sui", new_callable=AsyncMock) as mock_bs:
            await run_bootstrap_sui()
            mock_bs.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_import_scans_calls_import(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SCANNED_VALIDATORS_PATH", str(tmp_path / "scans.json"))
        monkeypatch.setenv("STAKEWIZ_CACHE_PATH", str(tmp_path / "cache.json"))
        monkeypatch.setenv("OPERATORS_PATH", str(tmp_path / "ops.json"))
        with patch("monitor.import_scans", new_callable=AsyncMock) as mock_import:
            await run_import_scans("/path/to/export.json")
            mock_import.assert_called_once_with(str(tmp_path / "scans.json"), "/path/to/export.json")

    @pytest.mark.asyncio
    async def test_main_wires_enricher_into_sol_monitor(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STAKEWIZ_CACHE_PATH", "")
        monkeypatch.setenv("STAKEWIZ_CACHE_PATH", str(tmp_path / "cache.json"))
        monkeypatch.setenv("SCANNED_VALIDATORS_PATH", str(tmp_path / "scans.json"))
        monkeypatch.setenv("OPERATORS_PATH", str(tmp_path / "ops.json"))
        monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.json"))
        monkeypatch.setenv("SOL_RPC_URL", "https://api.mainnet-beta.solana.com")

        captured = {}

        original_sol = monitor_module.SolMonitor

        def capturing_sol(*args, **kwargs):
            captured["enricher"] = kwargs.get("enricher")
            sol = MagicMock()
            sol.run = AsyncMock(side_effect=Exception("stop"))
            return sol

        with patch("monitor.SolMonitor", side_effect=capturing_sol), \
             patch("monitor.EthMonitor") as mock_eth, \
             patch("monitor.SuiMonitor") as mock_sui, \
             patch("monitor.CosmosMonitor") as mock_cosmos, \
             patch("monitor.DotMonitor") as mock_dot, \
             patch("monitor.asyncio.gather", new_callable=AsyncMock):
            await main()

        assert "enricher" in captured
        from enrichment import Enricher
        assert isinstance(captured["enricher"], Enricher)

    @pytest.mark.asyncio
    async def test_run_bootstrap_solana_passes_config_and_client(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STAKEWIZ_CACHE_PATH", str(tmp_path / "cache.json"))
        monkeypatch.setenv("SCANNED_VALIDATORS_PATH", str(tmp_path / "scans.json"))
        monkeypatch.setenv("OPERATORS_PATH", str(tmp_path / "ops.json"))

        with patch("monitor.bootstrap_solana", new_callable=AsyncMock) as mock_bs:
            await run_bootstrap_solana()
            call_args = mock_bs.call_args
            config_arg = call_args[0][0]
            assert config_arg.stakewiz_cache_path == str(tmp_path / "cache.json")

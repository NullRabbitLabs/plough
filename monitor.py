import argparse
import asyncio
import json
import logging

import httpx

from alerter import Alerter
from bootstrap import bootstrap_solana, bootstrap_sui, import_scans
from config import Config
from cosmos_monitor import CosmosMonitor
from dot_monitor import DotMonitor
from enrichment import Enricher
from eth_monitor import EthMonitor
from scan_client import ScanClient
from scan_queue import ScanQueue
from sol_monitor import SolMonitor
from state import State
from sui_monitor import SuiMonitor

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def run_bootstrap_solana() -> None:
    config = Config.from_env()
    async with httpx.AsyncClient(timeout=30) as client:
        await bootstrap_solana(config, client)


async def run_bootstrap_sui() -> None:
    config = Config.from_env()
    async with httpx.AsyncClient(timeout=30) as client:
        await bootstrap_sui(config, client)


async def run_enrich(vote_account: str, identity: str) -> None:
    config = Config.from_env()
    async with httpx.AsyncClient(timeout=15) as client:
        if not identity and config.sol_rpc_url:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getVoteAccounts", "params": []}
            resp = await client.post(config.sol_rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json().get("result", {})
            for v in data.get("current", []) + data.get("delinquent", []):
                if v.get("votePubkey") == vote_account:
                    identity = v.get("nodePubkey", "")
                    break

        enricher = Enricher(config, client)
        enricher.load_known_operators()
        enricher.load_stakewiz_cache()
        enricher.load_scan_index()
        enricher.load_node_ip_cache()
        result = await enricher.enrich_solana(vote_account, identity)
        print(json.dumps(result.__dict__, default=str, indent=2))


async def run_scan(vote_account: str) -> None:
    config = Config.from_env()
    async with httpx.AsyncClient(timeout=30) as client:
        # Resolve identity and enrich
        identity = ""
        if config.sol_rpc_url:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getVoteAccounts", "params": []}
            resp = await client.post(config.sol_rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json().get("result", {})
            for v in data.get("current", []) + data.get("delinquent", []):
                if v.get("votePubkey") == vote_account:
                    identity = v.get("nodePubkey", "")
                    break

        enricher = Enricher(config, client)
        enricher.load_known_operators()
        enricher.load_stakewiz_cache()
        enricher.load_scan_index()
        enricher.load_node_ip_cache()
        enriched = await enricher.enrich_solana(vote_account, identity)

        scan_client = ScanClient(config, client)
        scan_queue = ScanQueue(config, scan_client)
        scan_queue.load()

        metadata = {
            "source": "ferret",
            "trigger": "manual",
            "validator_pubkey": vote_account,
            "validator_name": enriched.name,
            "network": "solana",
            "incident_time": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "event_type": "manual_scan",
        }
        result = await scan_queue.try_scan(
            vote_account, "solana", enriched.ips, float("inf"), enriched.name, metadata
        )
        scan_queue.save()
        print(json.dumps({
            "vote_account": vote_account,
            "name": enriched.name,
            "ips": enriched.ips,
            "scan_result": result.__dict__,
        }, indent=2))


async def run_import_scans(path: str) -> None:
    config = Config.from_env()
    await import_scans(config.scanned_validators_path, path)


async def main() -> None:
    config = Config.from_env()
    state = State(config.state_path)
    state.load()

    async with httpx.AsyncClient(timeout=30) as client:
        alerter = Alerter(config, state)
        enricher = Enricher(config, client)
        enricher.load_known_operators()
        enricher.load_stakewiz_cache()
        enricher.load_scan_index()
        enricher.load_node_ip_cache()
        scan_client = ScanClient(config, client)
        scan_queue = ScanQueue(config, scan_client)
        scan_queue.load()
        eth = EthMonitor(config, state, alerter, client)
        sol = SolMonitor(config, state, alerter, client, enricher=enricher, scan_queue=scan_queue)
        sui = SuiMonitor(config, state, alerter, client)
        cosmos = CosmosMonitor(config, state, alerter, client)
        dot = DotMonitor(config, state, alerter, client)

        logger.info("Starting validator incident monitor")
        await asyncio.gather(eth.run(), sol.run(), sui.run(), cosmos.run(), dot.run())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validator incident monitor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--bootstrap-solana", action="store_true", help="Bootstrap validators.app cache")
    group.add_argument("--bootstrap-sui", action="store_true", help="Bootstrap Sui operator data")
    group.add_argument("--import-scans", metavar="PATH", help="Import scan results from PATH")
    group.add_argument("--enrich", metavar="VOTE_ACCOUNT", help="Print enriched data for a Solana validator")
    group.add_argument("--scan", metavar="VOTE_ACCOUNT", help="Manually trigger a scan for a Solana validator")
    args = parser.parse_args()

    try:
        if args.bootstrap_solana:
            asyncio.run(run_bootstrap_solana())
        elif args.bootstrap_sui:
            asyncio.run(run_bootstrap_sui())
        elif args.import_scans:
            asyncio.run(run_import_scans(args.import_scans))
        elif args.enrich:
            asyncio.run(run_enrich(args.enrich, ""))
        elif args.scan:
            asyncio.run(run_scan(args.scan))
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Monitor stopped by user")

import json
import logging
import os
from typing import Any, Dict, List

import httpx

from config import Config

logger = logging.getLogger(__name__)

STAKEWIZ_VALIDATORS_URL = "https://api.stakewiz.com/validators"


def _parse_sui_ip(address_str: str) -> str:
    """Extract IP from multiaddr-style string like '/ip4/52.12.34.56/udp/8084'."""
    if not address_str:
        return ""
    parts = address_str.split("/")
    try:
        idx = parts.index("ip4")
        return parts[idx + 1]
    except (ValueError, IndexError):
        return ""


async def bootstrap_solana(config: Config, client: httpx.AsyncClient) -> None:
    """Fetch all validators from Stakewiz and write to stakewiz_cache.json."""
    try:
        resp = await client.get(STAKEWIZ_VALIDATORS_URL, timeout=30)
        resp.raise_for_status()
        validators = resp.json()
    except Exception as e:
        logger.error("Stakewiz fetch failed: %s", e)
        return

    with open(config.stakewiz_cache_path, "w") as f:
        json.dump(validators, f)
    logger.info("Written %d validators to %s", len(validators), config.stakewiz_cache_path)


async def bootstrap_sui(config: Config, client: httpx.AsyncClient) -> None:
    """Fetch Sui system state and merge validator metadata into known_operators.json."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "suix_getLatestSuiSystemState",
        "params": [],
    }
    try:
        resp = await client.post(config.sui_rpc_url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Sui system state fetch failed: %s", e)
        return

    validators = data.get("result", {}).get("activeValidators", [])

    # Load existing known_operators.json
    try:
        with open(config.operators_path) as f:
            operators = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        operators = {}

    if "sui" not in operators:
        operators["sui"] = {}
    if "solana" not in operators:
        operators["solana"] = {}
    if "ethereum" not in operators:
        operators["ethereum"] = {}

    for v in validators:
        address = v.get("suiAddress", "")
        if not address:
            continue
        ip = _parse_sui_ip(v.get("p2pAddress", ""))
        operators["sui"][address] = {
            "name": v.get("name", ""),
            "project_url": v.get("projectUrl", ""),
            "ip": ip,
        }

    with open(config.operators_path, "w") as f:
        json.dump(operators, f, indent=2)
    logger.info("Merged %d Sui validators into %s", len(validators), config.operators_path)


async def import_scans(scanned_validators_path: str, scan_export_path: str) -> None:
    """Merge a scan export JSON array into scanned_validators.json."""
    # Load existing
    try:
        with open(scanned_validators_path) as f:
            existing: List[dict] = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []

    existing_index: Dict[str, dict] = {e["validator_pubkey"]: e for e in existing if e.get("validator_pubkey")}

    # Load new export
    try:
        with open(scan_export_path) as f:
            new_entries: List[dict] = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Could not read scan export: %s", e)
        return

    for entry in new_entries:
        pubkey = entry.get("validator_pubkey", "")
        if not pubkey:
            continue
        existing_entry = existing_index.get(pubkey)
        if existing_entry is None:
            existing_index[pubkey] = entry
        else:
            # Keep newer by ISO date string comparison
            if entry.get("scan_date", "") > existing_entry.get("scan_date", ""):
                existing_index[pubkey] = entry

    merged = list(existing_index.values())
    with open(scanned_validators_path, "w") as f:
        json.dump(merged, f, indent=2)
    logger.info("Scan index now has %d entries at %s", len(merged), scanned_validators_path)

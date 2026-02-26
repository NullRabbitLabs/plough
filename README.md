# plough

Multi-chain validator incident monitor. Watches Ethereum, Solana, Sui, Cosmos, and Polkadot validators for slashings, delinquency, and stake anomalies. Sends alerts to Telegram and/or Slack.

When a Solana validator goes delinquent, plough enriches the alert with operator identity, scan history, and IP data — and can automatically trigger a security scan via a configurable scan API.

## Features

- **Ethereum** — attester/proposer slashing detection via beaconcha.in + beacon node fallback
- **Solana** — delinquency monitoring with two-poll confirmation, mass event detection, enriched alerts
- **Sui** — stake drop alerts (configurable threshold)
- **Cosmos** — jailed and inactive validator alerts
- **Polkadot** — not-elected and slashing alerts via Subscan

**Enrichment (Solana)**
- Operator metadata from `known_operators.json` and [Stakewiz](https://stakewiz.com) cache
- IP resolution via `getClusterNodes` with persistent cache (last-known IPs survive validator going offline)
- Previous scan history (imported via `--import-scans`)

**Auto-scanning**
- Automatically submits scan jobs to a configurable scan API on delinquency incidents
- Rate limiting, per-validator cooldown, stake threshold filtering, and persistent scan queue

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env
set -a && source .env && set +a
.venv/bin/python monitor.py
```

## Keeping it running

**tmux:**
```bash
tmux new -s plough
set -a && source .env && set +a && .venv/bin/python monitor.py
# Ctrl-B D to detach
```

**Docker:**
```bash
docker compose up -d
```

## Bootstrap

Populate the Stakewiz validator cache (run on first start and periodically):

```bash
.venv/bin/python monitor.py --bootstrap-solana
```

## CLI tools

```bash
# Print enriched data for a Solana validator
.venv/bin/python monitor.py --enrich <VOTE_ACCOUNT>

# Manually trigger a scan for a Solana validator
ENABLE_AUTO_SCAN=true .venv/bin/python monitor.py --scan <VOTE_ACCOUNT>

# Bootstrap Sui operator data into known_operators.json
.venv/bin/python monitor.py --bootstrap-sui

# Import scan results from a scan export
.venv/bin/python monitor.py --import-scans /path/to/export.json
```

## Operator mapping

Create `known_operators.json` to map validator pubkeys/indices to human-readable names and metadata:

```json
{
  "solana": {
    "VotePubkey...": {
      "name": "Chorus One",
      "website": "https://chorus.one",
      "twitter": "chorusone",
      "discord": "chorusone",
      "ips": ["1.2.3.4"]
    }
  },
  "ethereum": {
    "12345": "Lido Finance"
  },
  "sui": {}
}
```

## Environment variables

### Alerting

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | | Target chat/channel ID |
| `SLACK_WEBHOOK_URL` | | Slack incoming webhook URL |
| `QUIET_HOURS_START` | off | Hour (0-23) to start suppressing alerts (UTC) |
| `QUIET_HOURS_END` | off | Hour (0-23) to stop suppressing alerts (UTC) |

### Ethereum

| Variable | Default | Description |
|---|---|---|
| `ETH_BEACON_API_URL` | `https://beaconcha.in` | beaconcha.in base URL |
| `ETH_BEACON_API_KEY` | | beaconcha.in API key (optional) |
| `ETH_BEACON_NODE_URL` | `https://ethereum-beacon-api.publicnode.com` | Beacon node fallback |
| `ETH_MAX_SLOTS_PER_POLL` | `32` | Finalized slots to scan per cycle |
| `POLL_INTERVAL_ETH` | `60` | Poll interval (seconds) |
| `ETH_COOLDOWN_SECONDS` | `3600` | Per-validator alert cooldown |

### Solana

| Variable | Default | Description |
|---|---|---|
| `SOL_RPC_URL` | | Solana RPC endpoint — `getVoteAccounts` requires a private RPC (e.g. [Helius](https://helius.dev)) |
| `SOL_STAKE_THRESHOLD_SOL` | `100` | Minimum stake (SOL) to alert on |
| `SOL_MASS_EVENT_THRESHOLD` | `5` | Number of simultaneous delinquencies for a mass event summary |
| `POLL_INTERVAL_SOL` | `30` | Poll interval (seconds) |
| `SOL_COOLDOWN_SECONDS` | `3600` | Per-validator alert cooldown |
| `STAKEWIZ_CACHE_PATH` | `stakewiz_cache.json` | Path for Stakewiz validator cache |
| `NODE_IP_CACHE_PATH` | `node_ip_cache.json` | Path for persistent node IP cache |

### Sui

| Variable | Default | Description |
|---|---|---|
| `SUI_RPC_URL` | `https://fullnode.mainnet.sui.io` | Sui RPC endpoint |
| `SUI_STAKE_DROP_THRESHOLD` | `0.20` | Stake drop fraction to alert on (0.20 = 20%) |
| `POLL_INTERVAL_SUI` | `60` | Poll interval (seconds) |
| `SUI_COOLDOWN_SECONDS` | `3600` | Per-validator alert cooldown |

### Cosmos

| Variable | Default | Description |
|---|---|---|
| `COSMOS_REST_URL` | `https://api.cosmos.network` | Cosmos REST endpoint |
| `COSMOS_VALIDATORS` | | Comma-separated operator addresses to monitor |
| `POLL_INTERVAL_COSMOS` | `60` | Poll interval (seconds) |
| `COSMOS_COOLDOWN_SECONDS` | `3600` | Per-validator alert cooldown |

### Polkadot

| Variable | Default | Description |
|---|---|---|
| `DOT_SUBSCAN_URL` | `https://polkadot.api.subscan.io` | Subscan API base URL |
| `DOT_SUBSCAN_API_KEY` | | Subscan API key |
| `DOT_VALIDATORS` | | Comma-separated stash addresses to monitor |
| `POLL_INTERVAL_DOT` | `300` | Poll interval (seconds) |
| `DOT_COOLDOWN_SECONDS` | `3600` | Per-validator alert cooldown |

### Scan API integration (optional)

| Variable | Default | Description |
|---|---|---|
| `ENABLE_AUTO_SCAN` | `false` | Enable automatic scan submission on incidents |
| `SCAN_API_URL` | | Scan API base URL |
| `SCAN_API_TOKEN` | | Bearer token for scan API |
| `SCAN_COOLDOWN` | `86400` | Seconds between scans for the same validator |
| `SCAN_RATE_LIMIT` | `5` | Max scan submissions per hour |
| `SCAN_MIN_STAKE_SOL` | `50000` | Minimum stake (SOL) to auto-scan |
| `SCAN_MIN_STAKE_SUI` | `1000000` | Minimum stake (SUI) to auto-scan |
| `SCAN_QUEUE_PATH` | `scan_queue.json` | Path for persistent scan queue |

### Storage

| Variable | Default | Description |
|---|---|---|
| `STATE_PATH` | `state.json` | Alert dedup and cooldown state |
| `OPERATORS_PATH` | `known_operators.json` | Known operator mappings |
| `SCANNED_VALIDATORS_PATH` | `scanned_validators.json` | Scan history index |

## Tests

```bash
.venv/bin/pytest tests/ -v
```

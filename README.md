# Validator Incident Monitor

Monitors Ethereum, Solana, and Sui validator networks for security incidents. Sends Telegram and/or Slack alerts on slashings, delinquency, and stake drops.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env` file:

```bash
TELEGRAM_BOT_TOKEN=your-token
TELEGRAM_CHAT_ID=-100123456
# and/or:
# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Run:

```bash
set -a && source .env && set +a
.venv/bin/python monitor.py
```

State is persisted in `state.json` (configurable via `STATE_PATH`).

## Keeping it running

**tmux:**
```bash
tmux new -s monitor
set -a && source .env && set +a && .venv/bin/python monitor.py
# Ctrl-B D to detach
```

**screen:**
```bash
screen -S monitor
set -a && source .env && set +a && .venv/bin/python monitor.py
# Ctrl-A D to detach
```

## Docker (optional)

```bash
docker compose up -d
```

State is persisted in `./data/state.json`.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | _(required)_ | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | _(required)_ | Target chat/channel ID |
| `ETH_BEACON_API_URL` | `https://beaconcha.in` | beaconcha.in base URL |
| `ETH_BEACON_NODE_URL` | `http://localhost:5052` | Beacon node (fallback) |
| `ETH_COOLDOWN_SECONDS` | `3600` | Per-validator alert cooldown |
| `POLL_INTERVAL_ETH` | `60` | ETH poll interval (seconds) |
| `SOL_RPC_URL` | `https://api.mainnet-beta.solana.com` | Solana RPC endpoint |
| `SOL_STAKE_THRESHOLD_SOL` | `100` | Min stake (SOL) to alert on |
| `SOL_MASS_EVENT_THRESHOLD` | `5` | # validators for mass alert |
| `SOL_COOLDOWN_SECONDS` | `3600` | Per-validator alert cooldown |
| `POLL_INTERVAL_SOL` | `30` | SOL poll interval (seconds) |
| `SUI_RPC_URL` | `https://fullnode.mainnet.sui.io` | Sui RPC endpoint |
| `SUI_STAKE_DROP_THRESHOLD` | `0.20` | Stake drop fraction to alert |
| `SUI_COOLDOWN_SECONDS` | `3600` | Per-validator alert cooldown |
| `POLL_INTERVAL_SUI` | `60` | Sui poll interval (seconds) |
| `STATE_PATH` | `state.json` | Path for persisted state |
| `OPERATORS_PATH` | `known_operators.json` | Known operator name mappings |
| `QUIET_HOURS_START` | _(off)_ | Hour (0-23) to start suppressing alerts |
| `QUIET_HOURS_END` | _(off)_ | Hour (0-23) to stop suppressing alerts |

## Operator mapping

Edit `known_operators.json` to map validator indices/pubkeys to human-readable names:

```json
{
  "12345": "Lido Finance",
  "67890": "Coinbase Cloud"
}
```

## Running tests

```bash
.venv/bin/pytest tests/ -v
```

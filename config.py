import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    slack_webhook_url: str

    poll_interval_eth: int
    poll_interval_sol: int
    poll_interval_sui: int

    eth_beacon_api_url: str
    eth_beacon_api_key: str
    eth_beacon_node_url: str
    eth_cooldown_seconds: int

    sol_rpc_url: str
    sol_stake_threshold_sol: float
    sol_mass_event_threshold: int
    sol_cooldown_seconds: int

    sui_rpc_url: str
    sui_stake_drop_threshold: float
    sui_cooldown_seconds: int

    state_path: str
    operators_path: str

    quiet_hours_start: Optional[int]
    quiet_hours_end: Optional[int]

    @classmethod
    def from_env(cls) -> "Config":
        qhs = os.environ.get("QUIET_HOURS_START")
        qhe = os.environ.get("QUIET_HOURS_END")
        return cls(
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
            poll_interval_eth=int(os.environ.get("POLL_INTERVAL_ETH", "60")),
            poll_interval_sol=int(os.environ.get("POLL_INTERVAL_SOL", "30")),
            poll_interval_sui=int(os.environ.get("POLL_INTERVAL_SUI", "60")),
            eth_beacon_api_url=os.environ.get("ETH_BEACON_API_URL", "https://beaconcha.in"),
            eth_beacon_api_key=os.environ.get("ETH_BEACON_API_KEY", ""),
            eth_beacon_node_url=os.environ.get("ETH_BEACON_NODE_URL", "https://ethereum-beacon-api.publicnode.com"),
            eth_cooldown_seconds=int(os.environ.get("ETH_COOLDOWN_SECONDS", "3600")),
            sol_rpc_url=os.environ.get("SOL_RPC_URL", ""),
            sol_stake_threshold_sol=float(os.environ.get("SOL_STAKE_THRESHOLD_SOL", "100")),
            sol_mass_event_threshold=int(os.environ.get("SOL_MASS_EVENT_THRESHOLD", "5")),
            sol_cooldown_seconds=int(os.environ.get("SOL_COOLDOWN_SECONDS", "3600")),
            sui_rpc_url=os.environ.get("SUI_RPC_URL", "https://fullnode.mainnet.sui.io"),
            sui_stake_drop_threshold=float(os.environ.get("SUI_STAKE_DROP_THRESHOLD", "0.20")),
            sui_cooldown_seconds=int(os.environ.get("SUI_COOLDOWN_SECONDS", "3600")),
            state_path=os.environ.get("STATE_PATH", "state.json"),
            operators_path=os.environ.get("OPERATORS_PATH", "known_operators.json"),
            quiet_hours_start=int(qhs) if qhs is not None else None,
            quiet_hours_end=int(qhe) if qhe is not None else None,
        )

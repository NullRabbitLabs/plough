import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    slack_webhook_url: str

    poll_interval_eth: int
    poll_interval_sol: int
    poll_interval_sui: int
    poll_interval_cosmos: int
    poll_interval_dot: int

    eth_beacon_api_url: str
    eth_beacon_api_key: str
    eth_beacon_node_url: str
    eth_cooldown_seconds: int
    eth_max_slots_per_poll: int

    sol_rpc_url: str
    sol_stake_threshold_sol: float
    sol_mass_event_threshold: int
    sol_cooldown_seconds: int

    sui_rpc_url: str
    sui_stake_drop_threshold: float
    sui_cooldown_seconds: int

    cosmos_rest_url: str
    cosmos_validators: List[str]
    cosmos_cooldown_seconds: int

    dot_subscan_url: str
    dot_subscan_api_key: str
    dot_validators: List[str]
    dot_cooldown_seconds: int

    state_path: str
    operators_path: str

    stakewiz_cache_path: str
    node_ip_cache_path: str
    scanned_validators_path: str

    scan_api_url: str
    scan_api_token: str
    enable_auto_scan: bool
    scan_cooldown: int
    scan_rate_limit: int
    scan_min_stake_sol: float
    scan_min_stake_sui: int
    scan_queue_path: str

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
            poll_interval_cosmos=int(os.environ.get("POLL_INTERVAL_COSMOS", "60")),
            poll_interval_dot=int(os.environ.get("POLL_INTERVAL_DOT", "300")),
            eth_beacon_api_url=os.environ.get("ETH_BEACON_API_URL", "https://beaconcha.in"),
            eth_beacon_api_key=os.environ.get("ETH_BEACON_API_KEY", ""),
            eth_beacon_node_url=os.environ.get("ETH_BEACON_NODE_URL", "https://ethereum-beacon-api.publicnode.com"),
            eth_cooldown_seconds=int(os.environ.get("ETH_COOLDOWN_SECONDS", "3600")),
            eth_max_slots_per_poll=int(os.environ.get("ETH_MAX_SLOTS_PER_POLL", "32")),
            sol_rpc_url=os.environ.get("SOL_RPC_URL", ""),
            sol_stake_threshold_sol=float(os.environ.get("SOL_STAKE_THRESHOLD_SOL", "100")),
            sol_mass_event_threshold=int(os.environ.get("SOL_MASS_EVENT_THRESHOLD", "5")),
            sol_cooldown_seconds=int(os.environ.get("SOL_COOLDOWN_SECONDS", "3600")),
            sui_rpc_url=os.environ.get("SUI_RPC_URL", "https://fullnode.mainnet.sui.io"),
            sui_stake_drop_threshold=float(os.environ.get("SUI_STAKE_DROP_THRESHOLD", "0.20")),
            sui_cooldown_seconds=int(os.environ.get("SUI_COOLDOWN_SECONDS", "3600")),
            cosmos_rest_url=os.environ.get("COSMOS_REST_URL", "https://api.cosmos.network"),
            cosmos_validators=[
                v.strip()
                for v in os.environ.get("COSMOS_VALIDATORS", "").split(",")
                if v.strip()
            ],
            cosmos_cooldown_seconds=int(os.environ.get("COSMOS_COOLDOWN_SECONDS", "3600")),
            dot_subscan_url=os.environ.get("DOT_SUBSCAN_URL", "https://polkadot.api.subscan.io"),
            dot_subscan_api_key=os.environ.get("DOT_SUBSCAN_API_KEY", ""),
            dot_validators=[
                v.strip()
                for v in os.environ.get("DOT_VALIDATORS", "").split(",")
                if v.strip()
            ],
            dot_cooldown_seconds=int(os.environ.get("DOT_COOLDOWN_SECONDS", "3600")),
            state_path=os.environ.get("STATE_PATH", "state.json"),
            operators_path=os.environ.get("OPERATORS_PATH", "known_operators.json"),
            stakewiz_cache_path=os.environ.get("STAKEWIZ_CACHE_PATH", "stakewiz_cache.json"),
            node_ip_cache_path=os.environ.get("NODE_IP_CACHE_PATH", "node_ip_cache.json"),
            scanned_validators_path=os.environ.get("SCANNED_VALIDATORS_PATH", "scanned_validators.json"),
            scan_api_url=os.environ.get("SCAN_API_URL", ""),
            scan_api_token=os.environ.get("SCAN_API_TOKEN", ""),
            enable_auto_scan=os.environ.get("ENABLE_AUTO_SCAN", "false").lower() == "true",
            scan_cooldown=int(os.environ.get("SCAN_COOLDOWN", "86400")),
            scan_rate_limit=int(os.environ.get("SCAN_RATE_LIMIT", "5")),
            scan_min_stake_sol=float(os.environ.get("SCAN_MIN_STAKE_SOL", "50000")),
            scan_min_stake_sui=int(os.environ.get("SCAN_MIN_STAKE_SUI", "1000000")),
            scan_queue_path=os.environ.get("SCAN_QUEUE_PATH", "scan_queue.json"),
            quiet_hours_start=int(qhs) if qhs is not None else None,
            quiet_hours_end=int(qhe) if qhe is not None else None,
        )

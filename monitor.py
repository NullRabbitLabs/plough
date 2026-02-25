import asyncio
import logging

import httpx

from alerter import Alerter
from config import Config
from eth_monitor import EthMonitor
from sol_monitor import SolMonitor
from state import State
from sui_monitor import SuiMonitor

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = Config.from_env()
    state = State(config.state_path)
    state.load()

    async with httpx.AsyncClient(timeout=30) as client:
        alerter = Alerter(config, state)
        eth = EthMonitor(config, state, alerter, client)
        sol = SolMonitor(config, state, alerter, client)
        sui = SuiMonitor(config, state, alerter, client)

        logger.info("Starting validator incident monitor")
        await asyncio.gather(eth.run(), sol.run(), sui.run())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Monitor stopped by user")

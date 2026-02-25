import logging
import re
from datetime import datetime
from typing import List

import httpx

from config import Config
from state import State

logger = logging.getLogger(__name__)


class Alerter:
    def __init__(self, config: Config, state: State) -> None:
        self.config = config
        self.state = state

    def _is_quiet_hours(self) -> bool:
        start = self.config.quiet_hours_start
        end = self.config.quiet_hours_end
        if start is None or end is None:
            return False
        hour = datetime.now().hour
        if start <= end:
            return start <= hour < end
        # Wraps midnight: e.g. start=22, end=7
        return hour >= start or hour < end

    def _html_to_mrkdwn(self, text: str) -> str:
        text = re.sub(r"<b>(.*?)</b>", r"*\1*", text, flags=re.DOTALL)
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", text)
        return text

    async def _send_telegram(self, text: str) -> None:
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            resp.raise_for_status()

    async def _send_slack(self, text: str) -> None:
        payload = {"text": self._html_to_mrkdwn(text)}
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.config.slack_webhook_url, json=payload, timeout=10)
            resp.raise_for_status()

    async def send_message(self, text: str) -> None:
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            await self._send_telegram(text)
        if self.config.slack_webhook_url:
            await self._send_slack(text)

    def format_eth_slashing(self, event) -> str:
        return (
            f"🚨 <b>ETH Validator Slashed</b>\n"
            f"Validator: #{event.validator_index} — {event.operator_name}\n"
            f"Type: {event.slash_type}\n"
            f"Epoch: {event.epoch} | Slot: {event.slot}\n"
            f"Slashed by: #{event.slashed_by}"
        )

    def format_sol_delinquent(self, validators: list, is_mass: bool) -> str:
        if is_mass:
            lines = []
            for v in validators[:10]:
                label = v.name if v.name else v.vote_account
                lines.append(f"• {label} ({v.activated_stake_sol:,.0f} SOL)")
            return (
                f"⚠️ <b>Solana Mass Delinquency Event</b>\n"
                f"{len(validators)} validators went delinquent simultaneously.\n"
                + "\n".join(lines)
            )
        v = validators[0]
        parts = ["⚠️ <b>Solana Validator Delinquent</b>"]
        if v.name:
            parts.append(f"Name: {v.name}")
        if v.website:
            parts.append(f"Website: {v.website}")
        if v.keybase:
            parts.append(f"Keybase: {v.keybase}")
        parts += [
            f"Vote: {v.vote_account}",
            f"Identity: {v.identity}",
            f"Stake: {v.activated_stake_sol:,.0f} SOL",
            f"Commission: {v.commission}%",
            f"Last vote: {v.last_vote}",
        ]
        return "\n".join(parts)

    def format_sui_drop(self, validator) -> str:
        return (
            f"🔴 <b>Sui Validator Alert</b>\n"
            f"Name: {validator.name}\n"
            f"Address: {validator.sui_address}\n"
            f"Current stake: {validator.stake_amount:,}\n"
            f"Next epoch stake: {validator.next_epoch_stake:,}"
        )

    async def alert_eth_slashing(self, event) -> None:
        if self.state.is_seen(event.event_id):
            return
        if self._is_quiet_hours():
            logger.info("Quiet hours — suppressing ETH slashing alert %s", event.event_id)
            return
        msg = self.format_eth_slashing(event)
        await self.send_message(msg)
        self.state.mark_seen(event.event_id)
        self.state.record_alert(str(event.validator_index))
        self.state.save()

    async def alert_sol_delinquent(self, validators: list, is_mass: bool) -> None:
        if self._is_quiet_hours():
            logger.info("Quiet hours — suppressing SOL delinquent alert")
            return

        if is_mass:
            msg = self.format_sol_delinquent(validators, is_mass=True)
            await self.send_message(msg)
            for v in validators:
                self.state.record_alert(v.vote_account)
            self.state.save()
            return

        for v in validators:
            if self.state.is_on_cooldown(v.vote_account, self.config.sol_cooldown_seconds):
                logger.debug("Cooldown active for %s, skipping", v.vote_account)
                continue
            msg = self.format_sol_delinquent([v], is_mass=False)
            await self.send_message(msg)
            self.state.record_alert(v.vote_account)
            self.state.save()

    async def alert_sui_drop(self, validator) -> None:
        if self.state.is_on_cooldown(validator.sui_address, self.config.sui_cooldown_seconds):
            logger.debug("Cooldown active for %s, skipping", validator.sui_address)
            return
        if self._is_quiet_hours():
            logger.info("Quiet hours — suppressing Sui drop alert %s", validator.sui_address)
            return
        msg = self.format_sui_drop(validator)
        await self.send_message(msg)
        self.state.record_alert(validator.sui_address)
        self.state.save()

import logging
import re
from datetime import datetime
from typing import List, Optional

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

    def _format_sol_scan_section(self, v) -> str:
        scan = getattr(v, "scan", None)
        if scan is None:
            return "🔍 NOT IN SCAN DB"
        ips_str = ", ".join(scan.ip_addresses) if scan.ip_addresses else "unknown"
        exposed = ", ".join(
            f"{f['service']}:{f['port']}" for f in scan.findings if "service" in f and "port" in f
        )
        critical = sum(1 for f in scan.findings if f.get("severity") == "critical")
        lines = ["🔍 SCAN DATA AVAILABLE"]
        lines.append(f"IPs: {ips_str}")
        if exposed:
            lines.append(f"Exposed: {exposed}")
        lines.append(f"Critical findings: {critical}")
        lines.append(f"Last scanned: {scan.scan_date}")
        return "\n".join(lines)

    def _format_sol_contact_section(self, v) -> str:
        website = getattr(v, "website", "")
        twitter = getattr(v, "twitter", "")
        discord = getattr(v, "discord", "")
        if not any([website, twitter, discord]):
            return "📇 NO CONTACT INFO"
        lines = ["📇 CONTACT"]
        if website:
            lines.append(f"Website: {website}")
        if twitter:
            lines.append(f"Twitter: {twitter}")
        if discord:
            lines.append(f"Discord: {discord}")
        return "\n".join(lines)

    def _format_scan_status(self, result, ips: list) -> str:
        if result is None:
            return ""
        status = result.status
        if status == "triggered":
            ips_str = ", ".join(result.ips) if result.ips else ", ".join(ips)
            return "\n".join([
                "🔍 SCAN TRIGGERED",
                f"IPs: {ips_str}",
                "Source: ferret/solana_delinquency",
                "Status: submitted",
            ])
        if status == "queued":
            ips_str = ", ".join(result.ips) if result.ips else ", ".join(ips)
            return "\n".join([
                f"🔍 SCAN QUEUED (rate limit — position {result.queue_position})",
                f"IPs: {ips_str}",
                "Will submit within ~1 hour",
            ])
        if status == "skipped_no_ips":
            return "\n".join([
                "⚠️ NO IP RESOLVED — cannot auto-scan",
                "Manual lookup: solana.fm, validators.app",
            ])
        if status == "skipped_cooldown":
            return "\n".join([
                "ℹ️ SCAN SKIPPED — scanned recently",
                f"Last scan: {result.last_scan_at}",
            ])
        # skipped_stake / skipped_disabled → silent
        return ""

    def format_sol_delinquent(self, validators: list, is_mass: bool, scan_results: Optional[dict] = None) -> str:
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
        label = "High Value" if v.name else "Unknown Operator"
        parts = [f"🟡 SOL DELINQUENT — {label}"]
        parts.append(f"\nValidator: {v.vote_account[:12]}...")
        if v.name:
            parts.append(f"Name: {v.name}")
        parts.append(f"Stake: {v.activated_stake_sol:,.0f} SOL")
        parts.append(f"Commission: {v.commission}%")
        parts.append("")
        parts.append(self._format_sol_scan_section(v))
        scan_result = (scan_results or {}).get(v.vote_account) if scan_results else None
        scan_status = self._format_scan_status(scan_result, getattr(v, "ips", []))
        if scan_status:
            parts.append("")
            parts.append(scan_status)
        parts.append("")
        parts.append(self._format_sol_contact_section(v))
        if not v.name:
            parts.append("")
            parts.append(f"validators.app: https://validators.app/validators/{v.vote_account}")
            parts.append(f"solana.fm: https://solana.fm/address/{v.vote_account}")
        if v.name:
            parts.append("\nAction: Send disclosure with scan results")
        else:
            parts.append("\nAction: Identify operator, add to known_operators.json")
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

    async def alert_sol_delinquent(self, validators: list, is_mass: bool, scan_results: Optional[dict] = None) -> None:
        if self._is_quiet_hours():
            logger.info("Quiet hours — suppressing SOL delinquent alert")
            return

        if is_mass:
            msg = self.format_sol_delinquent(validators, is_mass=True, scan_results=scan_results)
            await self.send_message(msg)
            for v in validators:
                self.state.record_alert(v.vote_account)
            self.state.save()
            return

        for v in validators:
            if self.state.is_on_cooldown(v.vote_account, self.config.sol_cooldown_seconds):
                logger.debug("Cooldown active for %s, skipping", v.vote_account)
                continue
            msg = self.format_sol_delinquent([v], is_mass=False, scan_results=scan_results)
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

    def format_cosmos_jailed(self, validator) -> str:
        return (
            f"🚨 <b>Cosmos Validator Jailed</b>\n"
            f"Moniker: <b>{validator.moniker}</b>\n"
            f"Address: <code>{validator.operator_address}</code>\n"
            f"Status: {validator.status}"
        )

    def format_cosmos_inactive(self, validator) -> str:
        return (
            f"⚠️ <b>Cosmos Validator Inactive</b>\n"
            f"Moniker: <b>{validator.moniker}</b>\n"
            f"Address: <code>{validator.operator_address}</code>\n"
            f"Status: {validator.status}"
        )

    async def alert_cosmos_jailed(self, validator) -> None:
        if self.state.is_on_cooldown(validator.operator_address, self.config.cosmos_cooldown_seconds):
            logger.debug("Cooldown active for %s, skipping", validator.operator_address)
            return
        if self._is_quiet_hours():
            logger.info("Quiet hours — suppressing Cosmos jailed alert %s", validator.operator_address)
            return
        msg = self.format_cosmos_jailed(validator)
        await self.send_message(msg)
        self.state.record_alert(validator.operator_address)
        self.state.save()

    async def alert_cosmos_inactive(self, validator) -> None:
        if self.state.is_on_cooldown(validator.operator_address, self.config.cosmos_cooldown_seconds):
            logger.debug("Cooldown active for %s, skipping", validator.operator_address)
            return
        if self._is_quiet_hours():
            logger.info("Quiet hours — suppressing Cosmos inactive alert %s", validator.operator_address)
            return
        msg = self.format_cosmos_inactive(validator)
        await self.send_message(msg)
        self.state.record_alert(validator.operator_address)
        self.state.save()

    def format_dot_inactive(self, validator) -> str:
        return (
            f"⚠️ <b>Polkadot Validator Not Elected</b>\n"
            f"Name: <b>{validator.display}</b>\n"
            f"Stash: <code>{validator.stash}</code>"
        )

    def format_dot_slashed(self, validator, event) -> str:
        planck_per_dot = 10_000_000_000
        amount_dot = event.amount / planck_per_dot
        return (
            f"🚨 <b>Polkadot Validator Slashed</b>\n"
            f"Name: <b>{validator.display}</b>\n"
            f"Stash: <code>{validator.stash}</code>\n"
            f"Amount: {amount_dot:.4f} DOT\n"
            f"Block: {event.block_num} | Event: {event.event_index}"
        )

    async def alert_dot_inactive(self, validator) -> None:
        if self.state.is_on_cooldown(validator.stash, self.config.dot_cooldown_seconds):
            logger.debug("Cooldown active for %s, skipping", validator.stash)
            return
        if self._is_quiet_hours():
            logger.info("Quiet hours — suppressing DOT inactive alert %s", validator.stash)
            return
        msg = self.format_dot_inactive(validator)
        await self.send_message(msg)
        self.state.record_alert(validator.stash)
        self.state.save()

    async def alert_dot_slashed(self, validator, event) -> None:
        event_key = f"dot_slash_{event.event_index}"
        if self.state.is_seen(event_key):
            return
        if self._is_quiet_hours():
            logger.info("Quiet hours — suppressing DOT slash alert %s", event.event_index)
            return
        msg = self.format_dot_slashed(validator, event)
        await self.send_message(msg)
        self.state.mark_seen(event_key)
        self.state.save()

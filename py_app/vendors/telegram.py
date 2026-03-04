"""Telegram Bot API client.

Gracefully no-ops when TELEGRAM_BOT_TOKEN is not configured.
Uses the existing httpx dependency — no new package required.
"""

from __future__ import annotations

from typing import Any

import httpx


class TelegramClient:
    def __init__(self, bot_token: str, chat_ids: str):
        self._token = (bot_token or "").strip()
        # TELEGRAM_CHAT_IDS is a comma-separated list.
        self._chat_ids = [c.strip() for c in (chat_ids or "").split(",") if c.strip()]

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_ids)

    async def send(self, text: str) -> None:
        """Send a plain-text message to all configured chat IDs. Silently skips if not enabled."""
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            for chat_id in self._chat_ids:
                try:
                    resp = await client.post(url, json={"chat_id": chat_id, "text": text})
                    resp.raise_for_status()
                except Exception:
                    pass  # Never let Telegram errors break sync

    async def notify_flagged_events(self, flagged: list[dict[str, Any]]) -> None:
        if not flagged or not self.enabled:
            return
        lines = ["⚠️ Door schedule approval required\n"]
        for item in flagged:
            lines.append(f"• {item.get('name', '(unknown)')}")
            lines.append(f"  {item.get('reason', '')}")
        lines.append("\nReview and approve at the dashboard.")
        await self.send("\n".join(lines))

    async def notify_sync_error(self, error: str) -> None:
        await self.send(f"❌ PCO→UniFi sync error:\n{error}")

    async def notify_user_action(self, actor: str, message: str) -> None:
        """Send a notification about a manual user action."""
        await self.send(f"🔔 {actor}: {message}")

    async def send_test(self) -> str:
        """Send a test message and return an error string, or empty string on success."""
        if not self.enabled:
            return "Telegram is not configured (missing bot token or chat IDs)."
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        errors = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for chat_id in self._chat_ids:
                try:
                    resp = await client.post(url, json={"chat_id": chat_id, "text": "✅ PCO→UniFi: Telegram notifications are working."})
                    resp.raise_for_status()
                except Exception as exc:
                    errors.append(f"chat_id {chat_id}: {exc}")
        return "; ".join(errors) if errors else ""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import json
import os
import smtplib
import urllib.parse
import urllib.request


@dataclass
class TelegramConfig:
    bot_token: str | None
    chat_id: str | None

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        return cls(bot_token=os.getenv("TELEGRAM_BOT_TOKEN"), chat_id=os.getenv("TELEGRAM_CHAT_ID"))

    @property
    def ready(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class AlertDeliveryConfig:
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    whatsapp_id: str | None = None
    whatsapp_webhook_url: str | None = None
    email_id: str | None = None

    @classmethod
    def from_contacts(cls, contacts: dict[str, str]) -> "AlertDeliveryConfig":
        return cls(
            telegram_bot_token=contacts.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=contacts.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID"),
            whatsapp_id=contacts.get("whatsapp_id"),
            whatsapp_webhook_url=contacts.get("whatsapp_webhook_url") or os.getenv("WHATSAPP_WEBHOOK_URL"),
            email_id=contacts.get("email_id"),
        )


class TelegramAlerter:
    def __init__(self, config: TelegramConfig | None = None):
        self.config = config or TelegramConfig.from_env()

    def send(self, message: str) -> bool:
        if not self.config.ready:
            print("Telegram not configured. Alert dry run:")
            print(message)
            return False
        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        body = urllib.parse.urlencode(
            {"chat_id": self.config.chat_id, "text": message, "disable_web_page_preview": "true"}
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300


class MultiChannelAlerter:
    """Send forecast alerts to configured user channels.

    Telegram can send directly with bot token and chat id. WhatsApp is provider
    neutral: pass a webhook URL from Twilio, Meta, Make, Zapier, or another
    bridge. Email uses SMTP_* environment variables plus the recipient email ID
    from the GUI.
    """

    def __init__(self, config: AlertDeliveryConfig):
        self.config = config

    def send(self, message: str) -> dict[str, str]:
        statuses: dict[str, str] = {}
        statuses["telegram"] = self._send_telegram(message)
        statuses["whatsapp"] = self._send_whatsapp(message)
        statuses["email"] = self._send_email(message)
        return statuses

    def _send_telegram(self, message: str) -> str:
        if not (self.config.telegram_bot_token and self.config.telegram_chat_id):
            return "not_configured"
        try:
            sent = TelegramAlerter(
                TelegramConfig(self.config.telegram_bot_token, self.config.telegram_chat_id)
            ).send(message)
            return "sent" if sent else "dry_run"
        except Exception as exc:
            return f"error: {exc}"

    def _send_whatsapp(self, message: str) -> str:
        if not self.config.whatsapp_id:
            return "not_configured"
        if not self.config.whatsapp_webhook_url:
            return "waiting_for_webhook"
        try:
            body = json.dumps({"to": self.config.whatsapp_id, "message": message}).encode("utf-8")
            request = urllib.request.Request(
                self.config.whatsapp_webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                return "sent" if 200 <= response.status < 300 else f"http_{response.status}"
        except Exception as exc:
            return f"error: {exc}"

    def _send_email(self, message: str) -> str:
        if not self.config.email_id:
            return "not_configured"
        host = os.getenv("SMTP_HOST")
        username = os.getenv("SMTP_USERNAME")
        password = os.getenv("SMTP_PASSWORD")
        sender = os.getenv("SMTP_FROM") or username
        port = int(os.getenv("SMTP_PORT", "587"))
        if not (host and sender):
            return "waiting_for_smtp"
        try:
            email = EmailMessage()
            email["Subject"] = "UNG Forecast Machine Alert"
            email["From"] = sender
            email["To"] = self.config.email_id
            email.set_content(message)
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls()
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(email)
            return "sent"
        except Exception as exc:
            return f"error: {exc}"

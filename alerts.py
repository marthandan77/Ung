from __future__ import annotations

from dataclasses import dataclass
import os
import urllib.parse
import urllib.request


@dataclass
class TelegramConfig:
    bot_token: str | None
    chat_id: str | None

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        )

    @property
    def ready(self) -> bool:
        return bool(self.bot_token and self.chat_id)


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
            {
                "chat_id": self.config.chat_id,
                "text": message,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300

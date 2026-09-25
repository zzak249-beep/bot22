"""Telegram: avisos (texto plano, nunca lanza excepción) + lectura de comandos del chat autorizado."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("tg")


class Telegram:
    def __init__(self, token: str, chat_id: str, prefix: str = ""):
        self.token, self.chat_id, self.prefix = token, str(chat_id), prefix
        self._last_err = 0.0
        self._offset: int | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str):
        msg = f"{self.prefix} {text}".strip()
        log.info("TG | %s", msg.replace("\n", " | "))
        if not self.enabled:
            return
        try:
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          data={"chat_id": self.chat_id, "text": msg[:4000],
                                "disable_web_page_preview": "true"}, timeout=10)
        except requests.RequestException as e:
            log.warning("telegram: %s", e)

    def error(self, text: str, every_s: int = 900):
        """Errores con límite de frecuencia para no inundar el chat."""
        now = time.time()
        if every_s == 0 or now - self._last_err >= every_s:
            self._last_err = now
            self.send(f"⚠️ {text}")
        else:
            log.error(text)

    def poll(self) -> list[str]:
        """Comandos nuevos (solo del CHAT_ID configurado). La primera llamada descarta los antiguos."""
        if not self.enabled:
            return []
        try:
            params = {"timeout": 0, "allowed_updates": '["message"]'}
            if self._offset is not None:
                params["offset"] = self._offset
            r = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", params=params, timeout=10)
            upd = r.json().get("result", [])
        except (requests.RequestException, ValueError) as e:
            log.debug("poll: %s", e)
            return []
        first = self._offset is None
        if upd:
            self._offset = upd[-1]["update_id"] + 1
        elif first:
            self._offset = 0
        if first:
            return []
        out = []
        for u in upd:
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) != self.chat_id:
                continue
            txt = (m.get("text") or "").strip()
            if txt.startswith("/"):
                out.append(txt.split()[0].split("@")[0].lower())
        return out

"""
notify_telegram.py — non-blocking Telegram push notifications (fail-soft).

Design contract (the three rules every caller can rely on):
  1. NEVER blocks the trading loop. `send()` only enqueues (asyncio.Queue) —
     a background worker task does the HTTP POST via an executor thread. If
     Telegram takes 2s (or 30s), candles keep processing. Outside an event
     loop (sync scripts, crash handlers) it falls back to a daemon thread.
  2. NEVER raises. Missing .env config -> disabled no-op (logged once).
     Network errors / rate limits -> logged to console, message dropped or
     retried once (Telegram 429 retry_after honoured), bot untouched.
  3. Secrets come from .env only: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
     (same python-dotenv pattern as kite_client.py — nothing hardcoded).

.env:
    TELEGRAM_BOT_TOKEN=123456:ABC-...   # from @BotFather
    TELEGRAM_CHAT_ID=987654321          # your chat/group id

Stdlib HTTP (urllib) — no new dependency. PAPER context: these are alerts
about SIMULATED fills; nothing here places or manages orders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import urllib.error
import urllib.request

from dotenv import load_dotenv

log = logging.getLogger("telegram")

API_TIMEOUT_S = 5          # per HTTP attempt — worker-thread time, never loop time
QUEUE_MAX = 100            # if alerts back up past this, drop + log (bot first)
MAX_LEN = 3900             # Telegram hard cap 4096; leave headroom


def _http_post(url: str, payload: dict, timeout: float = API_TIMEOUT_S) -> dict:
    """Blocking POST (runs in an executor/daemon thread, never the loop)."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 (https api)
        return json.loads(r.read().decode())


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None) -> None:
        self.token, self.chat_id = token, chat_id
        self._q: asyncio.Queue[str] | None = None
        self._worker: asyncio.Task | None = None
        if not self.enabled:
            log.info("telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set "
                     "— notifications disabled (no-op)")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    @property
    def _url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}/sendMessage"

    # ── the one public entry point ────────────────────────────────────────────
    def send(self, text: str) -> None:
        """Fire-and-forget. Enqueue in an event loop; daemon thread otherwise.
        Constant-time for the caller in both cases. Never raises."""
        if not self.enabled:
            return
        text = text[:MAX_LEN]
        try:
            asyncio.get_running_loop()
        except RuntimeError:                     # sync context (crash handler…)
            threading.Thread(target=self._post_blocking, args=(text,),
                             daemon=True).start()
            return
        try:
            if self._q is None:
                self._q = asyncio.Queue(maxsize=QUEUE_MAX)
            if self._worker is None or self._worker.done():
                self._worker = asyncio.get_running_loop().create_task(
                    self._drain(), name="telegram-notifier")
            self._q.put_nowait(text)
        except asyncio.QueueFull:
            log.warning("telegram: queue full (%d) — alert dropped, bot "
                        "continues", QUEUE_MAX)
        except Exception as e:                   # rule 2: never raise
            log.warning("telegram: enqueue failed (%s) — alert dropped", e)

    # ── background delivery ───────────────────────────────────────────────────
    async def _drain(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            text = await self._q.get()
            try:
                await loop.run_in_executor(None, self._post_blocking, text)
            except Exception as e:               # belt & braces; _post logs too
                log.warning("telegram: worker error (%s) — continuing", e)

    def _post_blocking(self, text: str) -> None:
        """One delivery attempt (+ one retry on 429/network). Logs, never raises."""
        payload = {"chat_id": self.chat_id, "text": text,
                   "disable_web_page_preview": True}
        for attempt in (1, 2):
            try:
                _http_post(self._url, payload)
                return
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 1:
                    try:
                        retry = json.loads(e.read().decode()).get(
                            "parameters", {}).get("retry_after", 3)
                    except Exception:
                        retry = 3
                    log.warning("telegram: rate-limited — retrying in %ss", retry)
                    threading.Event().wait(min(float(retry), 30.0))
                    continue
                log.warning("telegram: HTTP %s — alert dropped (%s)",
                            e.code, text[:80])
                return
            except Exception as e:
                if attempt == 1:
                    threading.Event().wait(2.0)
                    continue
                log.warning("telegram: send failed (%s) — alert dropped (%s)",
                            e, text[:80])
                return

    async def flush(self, timeout: float = 10.0) -> None:
        """Optional: best-effort drain before shutdown (e.g. end of a sim run)."""
        if not self.enabled or self._q is None:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout         # py3.10-safe (no asyncio.timeout)
        while not self._q.empty() and loop.time() < deadline:
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.2)                 # let the in-flight POST finish
        if not self._q.empty():
            log.warning("telegram: flush timed out — undelivered alerts dropped")


# ── module singleton (env-configured) ─────────────────────────────────────────
_notifier: TelegramNotifier | None = None


def get_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        load_dotenv()
        _notifier = TelegramNotifier(os.getenv("TELEGRAM_BOT_TOKEN"),
                                     os.getenv("TELEGRAM_CHAT_ID"))
    return _notifier


def _discover_chat_id(token: str) -> None:
    """CLI helper: print chat ids the bot can see. Requires that you have
    ALREADY sent the bot any message in Telegram (bots cannot message first)."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=API_TIMEOUT_S) as r:  # nosec B310
            updates = json.loads(r.read().decode()).get("result", [])
    except Exception as e:
        print(f"getUpdates failed: {e}")
        return
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            chats[chat["id"]] = (chat.get("username") or chat.get("title")
                                 or chat.get("first_name") or "?")
    if not chats:
        print("No chats found. Open Telegram, send your bot any message "
              "(e.g. /start), then run this again.")
        return
    for cid, name in chats.items():
        print(f"  TELEGRAM_CHAT_ID={cid}    ({name})")
    print("Copy the id into .env, then test with:  python notify_telegram.py")


if __name__ == "__main__":                       # manual check: python notify_telegram.py
    import sys
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    if "--chatid" in sys.argv:
        tok = os.getenv("TELEGRAM_BOT_TOKEN")
        if not tok:
            print("TELEGRAM_BOT_TOKEN missing from .env")
        else:
            _discover_chat_id(tok)
        raise SystemExit(0)
    n = get_notifier()
    if n.enabled:
        n.send("✅ tradebot Telegram test — if you can read this, wiring works")
        threading.Event().wait(3)               # give the daemon thread a moment
        print("test message dispatched (check your chat)")
    else:
        print("disabled — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env "
              "(find the chat id with:  python notify_telegram.py --chatid)")

# DEPLOY.md — Ubuntu server deployment (systemd, 24/7)

Ubuntu twin of the macOS `deploy/*.plist` setup. Entry point on a server is
**`scheduler.py`** — it is the "bot.py": a long-running daemon that fires the
daily pipeline at 15:45 IST and the hourly mark/risk passes itself, so systemd
only has to keep ONE process alive. `Restart=on-failure` replaces
`watchdog.sh`; `systemctl enable` replaces launchd's RunAtLoad.

PAPER ONLY — everything this deploys is simulation; nothing places an order.

Assumptions: user `ubuntu`, code at `~/trading_bot` (adjust both if different —
they appear in the unit files too).

---

## 1. Server prep

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git sqlite3
python3 --version    # 3.10+ expected on Ubuntu 22.04/24.04
```

Get the code onto the box (pick one):

```bash
git clone <your-remote-url> ~/trading_bot          # if you push this repo
# or from your Mac:
rsync -av --exclude .venv --exclude __pycache__ --exclude .git \
    ~/projects/tradebot/ ubuntu@YOUR_SERVER_IP:~/trading_bot/
```

> The SQLite ledgers (`portfolio.db`, `vwap.db`, …) ARE the paper book —
> rsync them once at migration, then let the server own them. Never let two
> machines write the same book.

## 2. Virtual environment

```bash
cd ~/trading_bot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -c "import apscheduler, pandas, yfinance, flask; print('deps OK')"
```

(systemd calls `.venv/bin/python` by absolute path, so activation is only for
your interactive shell — the service never needs it.)

## 3. Secrets management (.env)

Create the file with owner-only permissions from the first byte:

```bash
cd ~/trading_bot
(umask 077 && nano .env)
```

Contents (same keys the repo already uses — never commit this file; it is
already in `.gitignore`):

```ini
KITE_API_KEY=xxxx
KITE_API_SECRET=xxxx
DASHBOARD_PASSWORD=xxxx
DASHBOARD_SECRET_KEY=xxxx
N8N_RUN_WEBHOOK=https://...
TELEGRAM_BOT_TOKEN=123456:ABC-...    # optional: push alerts (@BotFather)
TELEGRAM_CHAT_ID=987654321           # optional: your chat id
```

Verify and lock down:

```bash
chmod 600 .env && ls -l .env    # must show -rw------- ubuntu ubuntu
```

Python reads it via `python-dotenv` (already in requirements; this is exactly
what `kite_client.py` does — no hardcoded keys anywhere):

```python
import os
from dotenv import load_dotenv

load_dotenv()                              # reads .env from the CWD
api_key = os.getenv("KITE_API_KEY")        # None if missing — handle it
if not api_key:
    raise SystemExit("KITE_API_KEY missing from .env")
```

> Kite reality check: access tokens expire daily (~6 AM IST) and `login.py`
> needs a browser. On a headless server the yfinance-driven pipeline runs
> fine without a fresh token; only Kite-authenticated extras need it.

## 4. Background execution — systemd (the important part)

The unit ships in the repo: `deploy/tradebot.service`. Install:

```bash
sudo cp ~/trading_bot/deploy/tradebot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tradebot      # start now + on every boot
systemctl status tradebot                 # expect: active (running)
```

What the unit guarantees (see the file for the annotated version):

| Requirement            | Directive |
|------------------------|-----------|
| Runs in background     | `Type=simple`, managed by PID 1 |
| Restart on failure     | `Restart=on-failure`, `RestartSec=15` |
| Start on reboot        | `WantedBy=multi-user.target` + `enable` |
| Crash-loop brake       | `StartLimitBurst=5` per 10 min |
| Correct trading day    | `Environment=TZ=Asia/Kolkata` — **critical**: on a UTC box, `date.today()` flips at 18:30 UTC and the session/idempotency guards drift |
| Live logs              | `Environment=PYTHONUNBUFFERED=1` — without it, prints reach journald in 4KB bursts |

Optional extras (same pattern as the Mac launchd agents):

```bash
# daily 17:00 IST ledger snapshots
sudo cp ~/trading_bot/deploy/tradebot-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tradebot-backup.timer
systemctl list-timers tradebot-backup.timer
```

If you also want the dashboard on the server, copy `tradebot.service` to
`tradebot-dashboard.service`, change `ExecStart` to
`.venv/bin/python dashboard.py`, and keep it bound to 127.0.0.1 — reach it
over `ssh -L 5050:127.0.0.1:5050 ubuntu@YOUR_SERVER_IP`, never an open port.

## 5. Logging (journalctl)

```bash
journalctl -u tradebot -f                  # live tail (the print statements)
journalctl -u tradebot -e -n 200           # last 200 lines, jump to end
journalctl -u tradebot --since today       # everything since midnight
journalctl -u tradebot --since "09:00" --until "16:00"
journalctl -u tradebot -p err              # errors/exceptions only
journalctl -u tradebot --since -15m -g "CIRCUIT|KILL|governor"   # risk events
```

The scheduler ALSO appends to `~/trading_bot/logs/scheduler.log` (its own
FileHandler). Keep it from growing forever:

```bash
sudo tee /etc/logrotate.d/tradebot >/dev/null <<'EOF'
/home/ubuntu/trading_bot/logs/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

And cap journald if the box is small:
`sudo mkdir -p /etc/systemd/journald.conf.d && printf '[Journal]\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/size.conf && sudo systemctl restart systemd-journald`

## Smoke test after deploy

```bash
cd ~/trading_bot && source .venv/bin/activate
python scheduler.py --list          # both jobs print with next-run times
python scheduler.py --once mark     # one mark/risk pass right now
python intraday_mark.py             # same thing, direct
deactivate
sudo systemctl restart tradebot && journalctl -u tradebot -f
# expect: "scheduler up — daily 15:45 IST, hourly marks 10-15:15 IST ..."
```

## Common failures

| Symptom | Cause / fix |
|---|---|
| `status=217/USER` | `User=` in the unit doesn't exist — edit to your username |
| `ModuleNotFoundError` on start | `ExecStart` not pointing at `.venv/bin/python`, or requirements not installed into that venv |
| Runs but no journal output until exit | `PYTHONUNBUFFERED=1` missing |
| Rebalance/marks fire at odd hours | `TZ=Asia/Kolkata` missing from the unit |
| `.env` ignored | service `WorkingDirectory` isn't the repo root — dotenv reads from CWD |

"""
exchange.py — Swap a Kite request_token for an access token (no local server).

Use when login.py's redirect flow won't land. Steps:
  1. Open the login URL, log in.
  2. Copy the request_token from the address bar of wherever it redirects
     (even an error/404 page — the token is in the URL: ...request_token=XXXX...).
  3. Run:  python exchange.py <request_token>
It exchanges the token and writes token.json, same as login.py would.
Read-only auth — places no orders.
"""

import json
import os
import sys
from datetime import date

from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()
API_KEY    = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

if len(sys.argv) < 2:
    raise SystemExit("Usage: python exchange.py <request_token>")
if not API_KEY or not API_SECRET:
    raise SystemExit("KITE_API_KEY / KITE_API_SECRET missing from .env")

request_token = sys.argv[1].strip()
kite = KiteConnect(api_key=API_KEY)
try:
    data = kite.generate_session(request_token, api_secret=API_SECRET)
except Exception as e:
    raise SystemExit(f"Exchange failed: {e}\n"
                     "(request_token is one-time and expires in minutes — "
                     "grab a fresh one from the login URL and retry quickly.)")

with open("token.json", "w") as f:
    json.dump({"access_token": data["access_token"], "date": str(date.today())}, f)
print(f"✓ token.json saved for {date.today()} — you can now load holdings.")

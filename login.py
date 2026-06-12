"""
login.py — Daily Kite Connect authentication
Run this script each morning to get a fresh access token.
"""

import json
import os
import webbrowser
from datetime import date
from threading import Timer

from dotenv import load_dotenv
from flask import Flask, request
from kiteconnect import KiteConnect

# ── Load credentials ──────────────────────────────────────────────────────────
load_dotenv()
API_KEY    = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

if not API_KEY or not API_SECRET:
    print("ERROR: KITE_API_KEY or KITE_API_SECRET missing from .env")
    raise SystemExit(1)

TOKEN_FILE = "token.json"
kite       = KiteConnect(api_key=API_KEY)
app        = Flask(__name__)


@app.route("/callback")
def callback():
    status        = request.args.get("status")
    request_token = request.args.get("request_token")

    if status != "success" or not request_token:
        return "<h2>Login failed or was cancelled. Close this tab and run login.py again.</h2>", 400

    try:
        # Exchange request_token → access_token.
        #
        # Plain-language explanation:
        #   After you log in on Kite's website, Zerodha hands your browser a
        #   one-time "request_token" — like a stamped ticket at the door.
        #   We take that ticket, combine it with our API secret (to prove it's
        #   really us), and send both back to Zerodha. Zerodha verifies them
        #   and returns an "access_token" — a day-pass that unlocks the API
        #   until ~6 AM IST the next morning. We save that day-pass to
        #   token.json so other scripts can use it without you logging in again.

        session_data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = session_data["access_token"]

        with open(TOKEN_FILE, "w") as f:
            json.dump({"access_token": access_token, "date": str(date.today())}, f)

        print("\n✓ Token saved successfully!")
        print("  Close the browser tab. This window will exit in 2 seconds.\n")

        Timer(2.0, lambda: os._exit(0)).start()
        return (
            "<h2 style='font-family:sans-serif;color:green'>"
            "✓ Login successful! Token saved. You can close this tab."
            "</h2>"
        )

    except Exception as e:
        print(f"\nERROR during session generation: {e}")
        return f"<h2>Error: {e}</h2>", 500


if __name__ == "__main__":
    login_url = kite.login_url()
    print()
    print("─────────────────────────────────────────────")
    print("  Kite Connect — Daily Login")
    print("─────────────────────────────────────────────")
    print("  Opening Kite login page in your browser...")
    print("  If it doesn't open automatically, visit:")
    print(f"  {login_url}")
    print("─────────────────────────────────────────────")
    print()

    Timer(1.5, lambda: webbrowser.open(login_url)).start()
    app.run(host="127.0.0.1", port=5000, use_reloader=False)

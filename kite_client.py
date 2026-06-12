"""
kite_client.py — Reusable Kite connection module.
All scripts import load_kite() from here instead of duplicating the logic.
"""

import json
import os
from datetime import date

from dotenv import load_dotenv
from kiteconnect import KiteConnect

TOKEN_FILE = "token.json"


def load_kite() -> KiteConnect:
    """
    Load credentials from .env, validate today's token, and return a
    connected KiteConnect instance ready to make API calls.

    Exits with a friendly message if:
      - API key is missing from .env
      - token.json doesn't exist (never logged in)
      - token is from a previous day (expired)
    """
    load_dotenv()
    api_key = os.getenv("KITE_API_KEY")

    if not api_key:
        raise SystemExit("ERROR: KITE_API_KEY missing from .env")

    # Check token file exists
    try:
        with open(TOKEN_FILE) as f:
            token_data = json.load(f)
    except FileNotFoundError:
        raise SystemExit(
            "\nNo token found.\n"
            "→ Run:  python login.py"
        )
    except json.JSONDecodeError:
        raise SystemExit(
            "\ntoken.json is corrupted.\n"
            "→ Run:  python login.py"
        )

    # Check token is from today
    if token_data.get("date") != str(date.today()):
        raise SystemExit(
            f"\nToken is from {token_data.get('date')} and has expired "
            f"(tokens reset daily around 6 AM IST).\n"
            "→ Run:  python login.py"
        )

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token_data["access_token"])
    return kite

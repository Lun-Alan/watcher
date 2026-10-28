"""用 CallMeBot send WhatsApp。電話同 apikey 由環境變數讀（本機可以放喺 watcher/.env）。"""

import os
from pathlib import Path

import requests

API = "https://api.callmebot.com/whatsapp.php"
MAX_CHARS = 1500


def _load_dotenv():
    env_file = Path(__file__).with_name(".env")
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def send(text):
    _load_dotenv()
    phone = os.environ.get("CALLMEBOT_PHONE")
    apikey = os.environ.get("CALLMEBOT_APIKEY")
    if not phone or not apikey:
        print("⚠️ 未設定 CALLMEBOT_PHONE／CALLMEBOT_APIKEY，冇 send 到 WhatsApp。訊息內容：")
        print(text)
        return False

    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 20] + "\n…（訊息太長，截咗）"
    resp = requests.get(API, params={"phone": phone, "text": text, "apikey": apikey}, timeout=30)
    body = resp.text.lower()
    ok = resp.ok and ("queued" in body or "error" not in body)
    if not ok:
        print(f"⚠️ CallMeBot send 唔到（HTTP {resp.status_code}）：{resp.text[:300]}")
    return ok

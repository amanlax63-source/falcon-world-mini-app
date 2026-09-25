import os
import json
import sqlite3
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

CHANNELS = [
    "@EthioMakeMoneyy1",
    "@DailyMoney444",
    "@SmartMoneyyLab",
    "@WorldCryptoMiner07",
    "@DailyIncomeHub55",
    "@Paymentprooff2"
]

DB_FILE = "database.db"

# -------------------------------------------------------------
# DATABASE SETUP (Fixing SQLite Locking Issues)
# -------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance REAL DEFAULT 0.0,
            referrer TEXT,
            referral_bonus_given INTEGER DEFAULT 0,
            last_bonus_time INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    # Transaction Error እንዳይፈጠር timeout እና WAL mode መጠቀም
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

# -------------------------------------------------------------
# TELEGRAM HELPER FUNCTIONS
# -------------------------------------------------------------
async def check_user_joined(user_id: int):
    unjoined = []
    async with httpx.AsyncClient() as client:
        for channel in CHANNELS:
            try:
                res = await client.get(f"{TELEGRAM_API}/getChatMember", params={"chat_id": channel, "user_id": user_id})
                data = res.json()
                if not data.get("ok") or data["result"]["status"] in ["left", "kicked"]:
                    unjoined.append(channel)
            except Exception:
                unjoined.append(channel)
    return unjoined

async def send_message(chat_id, text, reply_markup=None):
    async with httpx.AsyncClient() as client:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)

# -------------------------------------------------------------
# FASTAPI ROUTES & WEBHOOK
# -------------------------------------------------------------
@app.get("/")
def read_root():
    return {"status": "Bot Server is Running!"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    
    # Message handling
    if "message" in data:
        msg = data["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            unjoined = await check_user_joined(user_id)
            if not unjoined:
                await send_main_menu(chat_id)
            else:
                await send_join_buttons(chat_id, unjoined)

    # Callback Query (Verify Button)
    elif "callback_query" in data:
        cb = data["callback_query"]
        user_id = cb["from"]["id"]
        chat_id = cb["message"]["chat"]["id"]
        cb_id = cb["id"]

        if cb["data"] == "verify_membership":
            unjoined = await check_user_joined(user_id)
            async with httpx.AsyncClient() as client:
                if not unjoined:
                    await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": cb_id, "text": "✅ አባልነትዎ ተረጋግጧል!", "show_alert": True})
                    await send_main_menu(chat_id)
                else:
                    await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": cb_id, "text": "❌ አሁንም ያልተቀላቀሏቸው ቻናሎች አሉ!", "show_alert": True})
                    await send_join_buttons(chat_id, unjoined)

    return JSONResponse(content={"status": "ok"})

async def send_join_buttons(chat_id, unjoined_list):
    inline_keyboard = []
    for ch in unjoined_list:
        url = f"https://t.me/{ch.replace('@', '')}"
        inline_keyboard.append([{"text": f"📢 Join {ch}", "url": url}])
    
    inline_keyboard.append([{"text": "✅ Check / Verify", "callback_data": "verify_membership"}])
    
    markup = {"inline_keyboard": inline_keyboard}
    await send_message(
        chat_id,
        "👋 **እንኳን ደህና መጡ!**\n\nቦቱን መጠቀም ለመጀመር እባክዎን ከታች ያሉትን ቻናሎች ይቀላቀሉ እና **Check / Verify** የሚለውን ይጫኑ፡",
        reply_markup=markup
    )

async def send_main_menu(chat_id):
    keyboard = {
        "keyboard": [
            [{"text": "💰 Balance"}, {"text": "🎁 Daily Bonus"}],
            [{"text": "🔗 Referral Link"}, {"text": "🏧 Withdraw"}]
        ],
        "resize_keyboard": True
    }
    await send_message(chat_id, "🎉 **እንኳን ደስ አለዎት!** አሁን ቦቱን መጠቀም ይችላሉ።", reply_markup=keyboard)

import os
import sqlite3
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8519465007:AAE_-oyS5wuiY6Gf8Uci9CJS4NgdHo32638")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# የ Render App አድራሻህ (ለ Mini App Web App URL)
RENDER_URL = "https://falcon-world-mini-app.onrender.com"

# የላክልኝ 6ቱ ትክክለኛ ቻናሎች
CHANNELS = [
    "@Sheger_tech1",
    "@EthioVortex1",
    "@ethiocashflow",
    "@AmanIncomeLab",
    "@OnlineIncomeHub07",
    "@Paymentprooff2"
]

DB_FILE = "database.db"

# -------------------------------------------------------------
# DATABASE SETUP
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
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

# -------------------------------------------------------------
# HELPER FUNCTIONS
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
# MINI APP WEB ROUTE (index.html ማሳያ)
# -------------------------------------------------------------
@app.get("/")
async def serve_mini_app():
    # Mini App ሲከፈት በ GitHub ያለውን index.html ያሳያል
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"status": "Bot Server is Running!"}

# -------------------------------------------------------------
# TELEGRAM WEBHOOK HANDLER
# -------------------------------------------------------------
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    
    # 1. Message Handling
    if "message" in data:
        msg = data["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            unjoined = await check_user_joined(user_id)
            if not unjoined:
                await send_mini_app_button(chat_id)
            else:
                await send_join_buttons(chat_id, unjoined)

    # 2. Callback Query (Verify Button Click)
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
                    await send_mini_app_button(chat_id)
                else:
                    await client.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": cb_id, "text": "❌ አሁንም ያልተቀላቀሏቸው ቻናሎች አሉ!", "show_alert": True})
                    await send_join_buttons(chat_id, unjoined)

    return JSONResponse(content={"status": "ok"})

# ያልተቀላቀላቸውን ቻናሎች ማሳያ
async def send_join_buttons(chat_id, unjoined_list):
    inline_keyboard = []
    for ch in unjoined_list:
        clean_ch = ch.replace('@', '')
        url = f"https://t.me/{clean_ch}"
        inline_keyboard.append([{"text": f"📢 Join {ch}", "url": url}])
    
    inline_keyboard.append([{"text": "✅ Check / Verify", "callback_data": "verify_membership"}])
    
    markup = {"inline_keyboard": inline_keyboard}
    await send_message(
        chat_id,
        "👋 **እንኳን ደህና መጡ!**\n\nቦቱን እና Mini App መጠቀም ለመጀመር እባክዎን ከታች ያሉትን ቻናሎች ይቀላቀሉ እና **Check / Verify** የሚለውን ይጫኑ፡",
        reply_markup=markup
    )

# ቻናሎቹን ሲጨርስ Mini App መክፈቻ በተን መላኪያ
async def send_mini_app_button(chat_id):
    inline_keyboard = [
        [{"text": "🚀 Open Mini App", "web_app": {"url": RENDER_URL}}]
    ]
    markup = {"inline_keyboard": inline_keyboard}
    await send_message(
        chat_id,
        "🎉 **አባልነትዎ ተረጋግጧል!**\n\nከታች ያለውን **Open Mini App** የሚለውን በመጫን ወደ Mini App ገጽ መግባት ይችላሉ፡",
        reply_markup=markup
    )

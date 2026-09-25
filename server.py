import os
import sqlite3
import time
import httpx

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse

app = FastAPI()

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing from Render Environment Variables.")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

RENDER_URL = "https://falcon-world-mini-app.onrender.com"

DB_FILE = "database.db"

DAILY_BONUS = 0.50
REFERRAL_REWARD = 2.00

# =========================================================
# REQUIRED CHANNELS
# =========================================================

CHANNELS = [
    "@Sheger_tech1",
    "@EthioVortex1",
    "@ethiocashflow",
    "@AmanIncomeLab",
    "@OnlineIncomeHub07",
    "@Paymentprooff2",
]

# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance REAL DEFAULT 0,
            referrer TEXT,
            referral_bonus_given INTEGER DEFAULT 0,
            last_bonus_time INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


init_db()

# =========================================================
# TELEGRAM API HELPER
# =========================================================

async def telegram_call(method, payload):
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{TELEGRAM_API}/{method}",
            json=payload,
        )

        try:
            return response.json()
        except Exception:
            return {
                "ok": False,
                "description": response.text,
            }


# =========================================================
# CHECK CHANNEL MEMBERSHIP
# =========================================================

async def check_user_joined(user_id: int):
    unjoined = []

    async with httpx.AsyncClient(timeout=15.0) as client:

        for channel in CHANNELS:

            try:
                response = await client.get(
                    f"{TELEGRAM_API}/getChatMember",
                    params={
                        "chat_id": channel,
                        "user_id": user_id,
                    },
                )

                data = response.json()

                if not data.get("ok"):
                    unjoined.append(channel)
                    continue

                status = data.get("result", {}).get("status")

                if status not in (
                    "creator",
                    "administrator",
                    "member",
                ):
                    unjoined.append(channel)

            except Exception:
                unjoined.append(channel)

    return unjoined


# =========================================================
# SEND TELEGRAM MESSAGE
# =========================================================

async def send_message(chat_id, text, reply_markup=None):

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    return await telegram_call(
        "sendMessage",
        payload,
    )


# =========================================================
# JOIN BUTTONS
# =========================================================

async def send_join_buttons(chat_id, unjoined_list):

    keyboard = []

    for channel in unjoined_list:

        clean_channel = channel.replace("@", "")

        keyboard.append([
            {
                "text": f"📢 JOIN {channel}",
                "url": f"https://t.me/{clean_channel}",
            }
        ])

    keyboard.append([
        {
            "text": "✅ VERIFY",
            "callback_data": "verify_membership",
        }
    ])

    markup = {
        "inline_keyboard": keyboard
    }

    text = (
        "🦅 <b>FALCON WORLD</b>\n\n"
        "Welcome! 👋\n\n"
        "Please join the channels below to continue.\n\n"
        f"❌ <b>{len(unjoined_list)}</b> channel(s) remaining."
    )

    await send_message(
        chat_id,
        text,
        markup,
    )


# =========================================================
# MINI APP BUTTON
# =========================================================

async def send_mini_app_button(chat_id):

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "🚀 Open Falcon World",
                    "web_app": {
                        "url": RENDER_URL
                    },
                }
            ]
        ]
    }

    await send_message(
        chat_id,
        (
            "🎉 <b>Verification Complete!</b>\n\n"
            "✅ All required channels have been verified.\n\n"
            "🦅 Welcome to Falcon World!\n\n"
            "👇 Open the Mini App:"
        ),
        keyboard,
    )


# =========================================================
# SAVE / REGISTER USER
# =========================================================

def register_user(user_id, referrer=None):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (str(user_id),)
    )

    existing = cur.fetchone()

    if not existing:

        cur.execute(
            """
            INSERT INTO users
            (user_id, balance, referrer)
            VALUES (?, 0, ?)
            """,
            (
                str(user_id),
                str(referrer) if referrer else None,
            ),
        )

    conn.commit()
    conn.close()


# =========================================================
# /START + REFERRAL
# =========================================================

async def handle_start(message):

    user = message.get("from", {})
    chat = message.get("chat", {})

    user_id = user.get("id")
    chat_id = chat.get("id")

    if not user_id or not chat_id:
        return

    text = message.get("text", "")

    referrer = None

    parts = text.split(maxsplit=1)

    if len(parts) == 2:

        start_param = parts[1].strip()

        if start_param.startswith("ref_"):

            possible_referrer = start_param.replace(
                "ref_",
                "",
                1,
            )

            if possible_referrer.isdigit():
                referrer = possible_referrer

                if str(referrer) == str(user_id):
                    referrer = None

    register_user(
        user_id,
        referrer,
    )

    unjoined = await check_user_joined(
        user_id
    )

    if not unjoined:

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE users
            SET verified = 1
            WHERE user_id = ?
            """,
            (str(user_id),)
        )

        conn.commit()
        conn.close()

        await send_mini_app_button(
            chat_id
        )

    else:

        await send_join_buttons(
            chat_id,
            unjoined,
        )


# =========================================================
# VERIFY MEMBERSHIP
# =========================================================

async def handle_verify(callback_query):

    cb_id = callback_query.get("id")

    user = callback_query.get("from", {})
    message = callback_query.get("message", {})

    user_id = user.get("id")
    chat_id = message.get("chat", {}).get("id")

    if not user_id or not chat_id:
        return

    await telegram_call(
        "answerCallbackQuery",
        {
            "callback_query_id": cb_id,
            "text": "🔍 Checking membership...",
        },
    )

    unjoined = await check_user_joined(
        user_id
    )

    # -----------------------------------------------------
    # ALL JOINED
    # -----------------------------------------------------

    if not unjoined:

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE users
            SET verified = 1
            WHERE user_id = ?
            """,
            (str(user_id),)
        )

        conn.commit()
        conn.close()

        await telegram_call(
            "answerCallbackQuery",
            {
                "callback_query_id": cb_id,
                "text": "✅ Verification complete!",
                "show_alert": True,
            },
        )

        await send_mini_app_button(
            chat_id
        )

        return

    # -----------------------------------------------------
    # STILL UNJOINED
    # -----------------------------------------------------

    await telegram_call(
        "answerCallbackQuery",
        {
            "callback_query_id": cb_id,
            "text": (
                f"❌ {len(unjoined)} channel(s) remaining."
            ),
            "show_alert": True,
        },
    )

    await send_join_buttons(
        chat_id,
        unjoined,
    )


# =========================================================
# WEBHOOK
# =========================================================

@app.post("/webhook")
async def telegram_webhook(request: Request):

    try:
        data = await request.json()

    except Exception:
        return JSONResponse(
            {
                "ok": False,
                "error": "Invalid JSON",
            },
            status_code=400,
        )

    try:

        # ---------------------------------------------
        # MESSAGE
        # ---------------------------------------------

        if "message" in data:

            message = data["message"]

            text = message.get(
                "text",
                "",
            )

            if text.startswith("/start"):

                await handle_start(
                    message
                )

        # ---------------------------------------------
        # CALLBACK
        # ---------------------------------------------

        elif "callback_query" in data:

            callback_query = data[
                "callback_query"
            ]

            if (
                callback_query.get("data")
                == "verify_membership"
            ):

                await handle_verify(
                    callback_query
                )

        return JSONResponse(
            {
                "ok": True
            }
        )

    except Exception as error:

        print(
            "Webhook error:",
            repr(error),
        )

        return JSONResponse(
            {
                "ok": False,
                "error": "Webhook processing failed",
            },
            status_code=500,
        )


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",
        "service": "Falcon World",
    }


# =========================================================
# MINI APP HOME
# =========================================================

@app.get("/")
async def serve_mini_app():

    if os.path.exists("index.html"):

        return FileResponse(
            "index.html"
        )

    return {
        "status": "Falcon World server is running"
    }


# =========================================================
# REGISTER API
# =========================================================

@app.post("/api/register")
async def api_register(request: Request):

    try:
        data = await request.json()

        user_id = str(
            data.get("user_id", "")
        )

        if not user_id:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "user_id required",
                },
                status_code=400,
            )

        register_user(
            user_id
        )

        return {
            "ok": True
        }

    except Exception as error:

        return JSONResponse(
            {
                "ok": False,
                "error": str(error),
            },
            status_code=500,
        )


# =========================================================
# CURRENT USER
# =========================================================

@app.get("/api/me")
async def api_me(user_id: str = ""):

    if not user_id:
        return JSONResponse(
            {
                "ok": False,
                "error": "user_id required",
            },
            status_code=400,
        )

    register_user(
        user_id
    )

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            user_id,
            balance,
            referrer,
            verified,
            last_bonus_time
        FROM users
        WHERE user_id = ?
        """,
        (str(user_id),)
    )

    row = cur.fetchone()

    conn.close()

    if not row:
        return JSONResponse(
            {
                "ok": False,
                "error": "User not found",
            },
            status_code=404,
        )

    return {
        "ok": True,
        "user": dict(row),
    }


# =========================================================
# REQUIRED CHANNELS API
# =========================================================

@app.get("/api/required-channels")
async def required_channels():

    return {
        "ok": True,
        "channels": [
            {
                "username": channel,
                "url": f"https://t.me/{channel.replace('@', '')}",
            }
            for channel in CHANNELS
        ],
    }


# =========================================================
# DAILY BONUS STATUS
# =========================================================

@app.get("/api/daily-bonus")
async def daily_bonus_status(user_id: str = ""):

    if not user_id:
        return JSONResponse(
            {
                "ok": False,
                "error": "user_id required",
            },
            status_code=400,
        )

    register_user(
        user_id
    )

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            balance,
            last_bonus_time,
            verified
        FROM users
        WHERE user_id = ?
        """,
        (str(user_id),)
    )

    row = cur.fetchone()

    conn.close()

    if not row:
        return JSONResponse(
            {
                "ok": False,
                "error": "User not found",
            },
            status_code=404,
        )

    now = int(time.time())
    last_claim = int(
        row["last_bonus_time"] or 0
    )

    # 24 hours
    remaining = max(
        0,
        86400 - (now - last_claim)
    )

    return {
        "ok": True,
        "amount": DAILY_BONUS,
        "balance": float(
            row["balance"]
        ),
        "can_claim": (
            row["verified"] == 1
            and remaining == 0
        ),
        "remaining_seconds": remaining,
    }


# =========================================================
# DAILY BONUS CLAIM
# =========================================================

@app.post("/api/daily-bonus")
async def claim_daily_bonus(request: Request):

    try:
        data = await request.json()

    except Exception:
        return JSONResponse(
            {
                "ok": False,
                "error": "Invalid JSON",
            },
            status_code=400,
        )

    user_id = str(
        data.get("user_id", "")
    )

    if not user_id:
        return JSONResponse(
            {
                "ok": False,
                "error": "user_id required",
            },
            status_code=400,
        )

    register_user(
        user_id
    )

    # ---------------------------------------------
    # VERIFY CHANNELS AGAIN
    # ---------------------------------------------

    unjoined = await check_user_joined(
        int(user_id)
    )

    if unjoined:

        return JSONResponse(
            {
                "ok": False,
                "error": "Please join all required channels first.",
                "unjoined": unjoined,
            },
            status_code=403,
        )

    # ---------------------------------------------
    # CLAIM
    # ---------------------------------------------

    now = int(time.time())

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT balance, last_bonus_time
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    row = cur.fetchone()

    if not row:
        conn.close()

        return JSONResponse(
            {
                "ok": False,
                "error": "User not found",
            },
            status_code=404,
        )

    last_claim = int(
        row["last_bonus_time"] or 0
    )

    if now - last_claim < 86400:

        remaining = (
            86400
            - (now - last_claim)
        )

        conn.close()

        return JSONResponse(
            {
                "ok": False,
                "error": "Daily bonus already claimed.",
                "remaining_seconds": remaining,
            },
            status_code=429,
        )

    new_balance = (
        float(row["balance"])
        + DAILY_BONUS
    )

    cur.execute(
        """
        UPDATE users
        SET
            balance = ?,
            last_bonus_time = ?,
            verified = 1
        WHERE user_id = ?
        """,
        (
            new_balance,
            now,
            user_id,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "message": "Daily bonus claimed successfully.",
        "amount": DAILY_BONUS,
        "balance": new_balance,
    }

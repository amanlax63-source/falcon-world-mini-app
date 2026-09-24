import os
import hmac
import hashlib
import json
import sqlite3
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel


# ============================================================
# FALCON WORLD MINI APP SERVER
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_FILE = os.getenv("DB_FILE", "global_cash.db")

MIN_WITHDRAWAL = 30.0


app = FastAPI(
    title="Falcon World Mini App",
    version="1.0.0",
)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# TELEGRAM MINI APP AUTH
# ============================================================

def validate_init_data(init_data: str):
    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication data is missing."
        )

    if not BOT_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="BOT_TOKEN is not configured."
        )

    data = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = data.pop("hash", None)

    if not received_hash:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram authentication data."
        )

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(data.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(
        calculated_hash,
        received_hash
    ):
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication failed."
        )

    try:
        user = json.loads(data.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user data."
        )

    if not user.get("id"):
        raise HTTPException(
            status_code=401,
            detail="Telegram user ID is missing."
        )

    return user


# ============================================================
# CURRENT USER
# ============================================================

def get_current_user(
    authorization: str | None
):
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization required."
        )

    if not authorization.startswith("tma "):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization format."
        )

    init_data = authorization[4:].strip()

    return validate_init_data(init_data)


# ============================================================
# HOME
# ============================================================

@app.get("/")
async def home():
    return FileResponse("index.html")


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": "Falcon World",
        "bot": "FalconWorld_Bot"
    }


# ============================================================
# USER PROFILE
# ============================================================

@app.get("/api/me")
async def get_me(
    authorization: str | None = Header(default=None)
):
    user = get_current_user(authorization)

    user_id = int(user["id"])

    conn = db()

    row = conn.execute(
        """
        SELECT
            user_id,
            username,
            first_name,
            balance,
            referred_by,
            referral_paid,
            joined_all,
            suspicious,
            wallet_type,
            wallet_number
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    if not row:
        return {
            "exists": False,
            "user": user
        }

    return {
        "exists": True,
        "user": {
            "id": row["user_id"],
            "username": row["username"],
            "first_name": row["first_name"],
        },
        "balance": float(row["balance"]),
        "joined_all": bool(row["joined_all"]),
        "referral_count": get_referral_count(user_id),
        "wallet": (
            {
                "type": row["wallet_type"],
                "number": row["wallet_number"]
            }
            if row["wallet_type"]
            and row["wallet_number"]
            else None
        )
    }


# ============================================================
# REFERRALS
# ============================================================

def get_referral_count(user_id):
    conn = db()

    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM referrals
        WHERE referrer_id=?
        AND status='paid'
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    return int(row["c"])


@app.get("/api/referral")
async def referral(
    authorization: str | None = Header(default=None)
):
    user = get_current_user(authorization)

    user_id = int(user["id"])

    return {
        "count": get_referral_count(user_id),
        "reward_min": 1.00,
        "reward_max": 5.00,
        "link": (
            f"https://t.me/FalconWorld_Bot"
            f"?start={user_id}"
        )
    }


# ============================================================
# WALLET
# ============================================================

class WalletRequest(BaseModel):
    wallet_type: str
    wallet_number: str


@app.get("/api/wallet")
async def wallet(
    authorization: str | None = Header(default=None)
):
    user = get_current_user(authorization)

    user_id = int(user["id"])

    conn = db()

    row = conn.execute(
        """
        SELECT wallet_type, wallet_number
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    return {
        "wallet_type": row["wallet_type"],
        "wallet_number": row["wallet_number"]
    }


@app.post("/api/wallet")
async def save_wallet(
    request: WalletRequest,
    authorization: str | None = Header(default=None)
):
    user = get_current_user(authorization)

    user_id = int(user["id"])

    wallet_type = request.wallet_type.strip()
    wallet_number = request.wallet_number.strip()

    if wallet_type not in (
        "CBE",
        "Telebirr"
    ):
        raise HTTPException(
            status_code=400,
            detail="Wallet must be CBE or Telebirr."
        )

    if wallet_type == "CBE":
        if not wallet_number.isdigit():
            raise HTTPException(
                status_code=400,
                detail="Invalid CBE account."
            )

        if not wallet_number.startswith("1000"):
            raise HTTPException(
                status_code=400,
                detail="CBE account must start with 1000."
            )

        if len(wallet_number) != 13:
            raise HTTPException(
                status_code=400,
                detail="CBE account must contain 13 digits."
            )

    if wallet_type == "Telebirr":
        if not wallet_number.isdigit():
            raise HTTPException(
                status_code=400,
                detail="Invalid Telebirr number."
            )

        if not (
            wallet_number.startswith("09")
            or wallet_number.startswith("07")
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid Telebirr number."
            )

        if len(wallet_number) != 10:
            raise HTTPException(
                status_code=400,
                detail="Telebirr number must contain 10 digits."
            )

    conn = db()

    duplicate = conn.execute(
        """
        SELECT user_id
        FROM users
        WHERE wallet_type=?
        AND wallet_number=?
        AND user_id !=?
        """,
        (
            wallet_type,
            wallet_number,
            user_id
        )
    ).fetchone()

    if duplicate:
        conn.close()

        conn = db()

        conn.execute(
            """
            UPDATE users
            SET suspicious=1
            WHERE user_id=?
            """,
            (user_id,)
        )

        conn.commit()
        conn.close()

        raise HTTPException(
            status_code=400,
            detail="This wallet is already used by another account."
        )

    conn.execute(
        """
        UPDATE users
        SET wallet_type=?,
            wallet_number=?
        WHERE user_id=?
        """,
        (
            wallet_type,
            wallet_number,
            user_id
        )
    )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "wallet_type": wallet_type,
        "wallet_number": wallet_number
    }


# ============================================================
# WITHDRAW
# ============================================================

class WithdrawRequest(BaseModel):
    amount: float


@app.post("/api/withdraw")
async def withdraw(
    request: WithdrawRequest,
    authorization: str | None = Header(default=None)
):
    user = get_current_user(authorization)

    user_id = int(user["id"])

    amount = float(request.amount)

    if amount < MIN_WITHDRAWAL:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum withdrawal is {MIN_WITHDRAWAL:.0f} ETB."
        )

    conn = db()

    row = conn.execute(
        """
        SELECT
            balance,
            wallet_type,
            wallet_number,
            suspicious,
            joined_all
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    if not row:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    if row["suspicious"]:
        conn.close()

        raise HTTPException(
            status_code=403,
            detail="Account is under review."
        )

    if not row["joined_all"]:
        conn.close()

        raise HTTPException(
            status_code=403,
            detail="Please complete channel verification first."
        )

    if not row["wallet_type"]:
        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Please add your wallet first."
        )

    if amount > float(row["balance"]):
        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Insufficient balance."
        )

    conn.execute(
        """
        UPDATE users
        SET balance=balance-?
        WHERE user_id=?
        AND balance>=?
        """,
        (
            amount,
            user_id,
            amount
        )
    )

    changed = conn.execute(
        "SELECT changes() AS c"
    ).fetchone()["c"]

    if changed != 1:
        conn.rollback()
        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Withdrawal could not be processed."
        )

    cur = conn.execute(
        """
        INSERT INTO withdrawals(
            user_id,
            amount,
            wallet_type,
            wallet_number,
            status,
            created_at
        )
        VALUES(?,?,?,?,?,datetime('now'))
        """,
        (
            user_id,
            amount,
            row["wallet_type"],
            row["wallet_number"],
            "pending"
        )
    )

    withdrawal_id = cur.lastrowid

    conn.commit()
    conn.close()

    return {
        "success": True,
        "withdrawal_id": withdrawal_id,
        "amount": amount,
        "status": "pending"
    }


# ============================================================
# WITHDRAWAL HISTORY
# ============================================================

@app.get("/api/withdrawals")
async def withdrawals(
    authorization: str | None = Header(default=None)
):
    user = get_current_user(authorization)

    user_id = int(user["id"])

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            amount,
            wallet_type,
            wallet_number,
            status,
            created_at
        FROM withdrawals
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 50
        """,
        (user_id,)
    ).fetchall()

    conn.close()

    return {
        "withdrawals": [
            {
                "id": row["id"],
                "amount": float(row["amount"]),
                "wallet_type": row["wallet_type"],
                "wallet_number": row["wallet_number"],
                "status": row["status"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
    }


# ============================================================
# TASKS
# ============================================================

@app.get("/api/tasks")
async def tasks(
    authorization: str | None = Header(default=None)
):
    user = get_current_user(authorization)

    user_id = int(user["id"])

    conn = db()

    rows = conn.execute(
        """
        SELECT
            t.id,
            t.title,
            t.description,
            t.channel_username,
            t.channel_url,
            t.reward,
            t.active,
            COALESCE(ut.paid, 0) AS paid
        FROM tasks t
        LEFT JOIN user_tasks ut
            ON ut.task_id=t.id
            AND ut.user_id=?
        WHERE t.active=1
        ORDER BY t.id DESC
        """,
        (user_id,)
    ).fetchall()

    conn.close()

    return {
        "tasks": [
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "channel_username": row["channel_username"],
                "channel_url": row["channel_url"],
                "reward": float(row["reward"]),
                "paid": bool(row["paid"])
            }
            for row in rows
        ]
    }

import os
import hmac
import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qsl
from typing import Optional

import httpx

from fastapi import (
    FastAPI,
    HTTPException,
    Header,
    UploadFile,
    File,
    Form,
)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel


# ============================================================
# FALCON WORLD MINI APP SERVER
# ============================================================

APP_NAME = "Falcon World"
BOT_USERNAME = "FalconWorld_Bot"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_FILE = os.getenv("DB_FILE", "global_cash.db")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

REQUIRED_CHANNELS = [
    x.strip()
    for x in os.getenv("REQUIRED_CHANNELS", "").split(",")
    if x.strip()
]

# ============================================================
# FIXED BUSINESS VALUES
# ============================================================

REFERRAL_REWARD = 2.00
DAILY_BONUS = 0.50
MIN_WITHDRAWAL = 30.00

DAILY_BONUS_COOLDOWN_HOURS = 24

MAX_PROOF_SIZE = 8 * 1024 * 1024


app = FastAPI(
    title="Falcon World Mini App",
    version="2.1.0",
)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def now_utc():
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_utc(value):
    if not value:
        return None

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

    except ValueError:
        return None


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():

    conn = db()

    conn.executescript(
        """

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,

            username TEXT,
            first_name TEXT,
            last_name TEXT,

            balance REAL NOT NULL DEFAULT 0,

            referred_by INTEGER,

            referral_paid INTEGER NOT NULL DEFAULT 0,

            joined_all INTEGER NOT NULL DEFAULT 0,

            suspicious INTEGER NOT NULL DEFAULT 0,

            cbe_number TEXT,
            telebirr_number TEXT,

            registered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL UNIQUE,

            status TEXT NOT NULL DEFAULT 'pending',

            reward REAL NOT NULL DEFAULT 2.00,

            created_at TEXT NOT NULL,
            paid_at TEXT,

            FOREIGN KEY(referrer_id)
                REFERENCES users(user_id),

            FOREIGN KEY(referred_id)
                REFERENCES users(user_id)
        );


        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,
            description TEXT,

            channel_username TEXT,
            channel_url TEXT,

            reward REAL NOT NULL DEFAULT 0,

            active INTEGER NOT NULL DEFAULT 1,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS user_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,

            status TEXT NOT NULL DEFAULT 'pending',

            proof_text TEXT,
            proof_data BLOB,
            proof_filename TEXT,
            proof_content_type TEXT,

            reward REAL NOT NULL DEFAULT 0,

            submitted_at TEXT,
            reviewed_at TEXT,

            rejection_reason TEXT,

            paid INTEGER NOT NULL DEFAULT 0,

            UNIQUE(user_id, task_id),

            FOREIGN KEY(user_id)
                REFERENCES users(user_id),

            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
        );


        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            amount REAL NOT NULL,

            wallet_type TEXT NOT NULL,
            wallet_number TEXT NOT NULL,

            status TEXT NOT NULL DEFAULT 'pending',

            created_at TEXT NOT NULL,
            reviewed_at TEXT,

            rejection_reason TEXT,

            FOREIGN KEY(user_id)
                REFERENCES users(user_id)
        );


        CREATE TABLE IF NOT EXISTS balance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            amount REAL NOT NULL,

            type TEXT NOT NULL,

            description TEXT,

            reference_id INTEGER,

            balance_after REAL NOT NULL,

            created_at TEXT NOT NULL,

            FOREIGN KEY(user_id)
                REFERENCES users(user_id)
        );


        CREATE TABLE IF NOT EXISTS daily_bonus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            bonus_date TEXT NOT NULL,

            amount REAL NOT NULL DEFAULT 0.50,

            created_at TEXT NOT NULL,

            UNIQUE(user_id, bonus_date),

            FOREIGN KEY(user_id)
                REFERENCES users(user_id)
        );


        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            title TEXT NOT NULL,
            message TEXT NOT NULL,

            is_read INTEGER NOT NULL DEFAULT 0,

            created_at TEXT NOT NULL,

            FOREIGN KEY(user_id)
                REFERENCES users(user_id)
        );


        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,
            message TEXT NOT NULL,

            active INTEGER NOT NULL DEFAULT 1,

            created_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            admin_id INTEGER NOT NULL,

            action TEXT NOT NULL,

            target_type TEXT,
            target_id INTEGER,

            details TEXT,

            created_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        """
    )

    # --------------------------------------------------------
    # DEFAULT SETTINGS
    # --------------------------------------------------------

    conn.execute(
        """
        INSERT OR IGNORE INTO settings(key, value)
        VALUES('maintenance_mode', '0')
        """
    )

    conn.commit()

    conn.close()


init_db()


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key, default=None):

    conn = db()

    row = conn.execute(
        """
        SELECT value
        FROM settings
        WHERE key=?
        """,
        (key,),
    ).fetchone()

    conn.close()

    if not row:
        return default

    return row["value"]


def set_setting(key, value):

    conn = db()

    conn.execute(
        """
        INSERT INTO settings(key, value)
        VALUES(?, ?)

        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )

    conn.commit()
    conn.close()


# ============================================================
# TELEGRAM MINI APP AUTH
# ============================================================

def validate_init_data(init_data: str):

    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication data is missing.",
        )

    if not BOT_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="BOT_TOKEN is not configured.",
        )

    data = dict(
        parse_qsl(
            init_data,
            keep_blank_values=True,
        )
    )

    received_hash = data.pop("hash", None)

    if not received_hash:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram authentication data.",
        )

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(data.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(
        calculated_hash,
        received_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication failed.",
        )

    try:

        user = json.loads(
            data.get("user", "{}")
        )

    except json.JSONDecodeError:

        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user data.",
        )

    if not user.get("id"):

        raise HTTPException(
            status_code=401,
            detail="Telegram user ID is missing.",
        )

    return user, data


def get_current_user(
    authorization: Optional[str],
):

    if not authorization:

        raise HTTPException(
            status_code=401,
            detail="Authorization required.",
        )

    if not authorization.startswith("tma "):

        raise HTTPException(
            status_code=401,
            detail="Invalid authorization format.",
        )

    init_data = authorization[4:].strip()

    return validate_init_data(init_data)


# ============================================================
# USER HELPERS
# ============================================================

def ensure_user(user):

    user_id = int(user["id"])

    username = user.get("username")
    first_name = user.get("first_name", "")
    last_name = user.get("last_name", "")

    current_time = now_utc()

    conn = db()

    existing = conn.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id=?
        """,
        (user_id,),
    ).fetchone()

    if not existing:

        conn.execute(
            """
            INSERT INTO users(
                user_id,
                username,
                first_name,
                last_name,
                registered_at,
                updated_at
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                user_id,
                username,
                first_name,
                last_name,
                current_time,
                current_time,
            ),
        )

    else:

        conn.execute(
            """
            UPDATE users
            SET username=?,
                first_name=?,
                last_name=?,
                updated_at=?
            WHERE user_id=?
            """,
            (
                username,
                first_name,
                last_name,
                current_time,
                user_id,
            ),
        )

    conn.commit()
    conn.close()

    return user_id


# ============================================================
# BALANCE HELPER
# ============================================================

def add_balance(
    conn,
    user_id,
    amount,
    history_type,
    description,
    reference_id=None,
):

    row = conn.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id=?
        """,
        (user_id,),
    ).fetchone()

    if not row:
        raise ValueError("User not found")

    new_balance = (
        float(row["balance"])
        + float(amount)
    )

    conn.execute(
        """
        UPDATE users
        SET balance=?,
            updated_at=?
        WHERE user_id=?
        """,
        (
            new_balance,
            now_utc(),
            user_id,
        ),
    )

    conn.execute(
        """
        INSERT INTO balance_history(
            user_id,
            amount,
            type,
            description,
            reference_id,
            balance_after,
            created_at
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            user_id,
            amount,
            history_type,
            description,
            reference_id,
            new_balance,
            now_utc(),
        ),
    )

    return new_balance


# ============================================================
# NOTIFICATION
# ============================================================

def notify(
    conn,
    user_id,
    title,
    message,
):

    conn.execute(
        """
        INSERT INTO notifications(
            user_id,
            title,
            message,
            created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            user_id,
            title,
            message,
            now_utc(),
        ),
    )


# ============================================================
# ADMIN HELPERS
# ============================================================

def admin_required(user):

    user_id = int(user["id"])

    if user_id not in ADMIN_IDS:

        raise HTTPException(
            status_code=403,
            detail="Admin access required.",
        )

    return user_id


def admin_log(
    conn,
    admin_id,
    action,
    target_type=None,
    target_id=None,
    details=None,
):

    conn.execute(
        """
        INSERT INTO admin_logs(
            admin_id,
            action,
            target_type,
            target_id,
            details,
            created_at
        )
        VALUES(?,?,?,?,?,?)
        """,
        (
            admin_id,
            action,
            target_type,
            target_id,
            details,
            now_utc(),
        ),
    )


# ============================================================
# HOME
# ============================================================

@app.get("/")
async def home():

    return FileResponse("index.html")


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",
        "app": APP_NAME,
        "bot": BOT_USERNAME,
        "database": "ok",
    }


# ============================================================
# REGISTER
# ============================================================

@app.post("/api/register")
async def register(
    authorization: Optional[str] = Header(default=None),
):

    user, init_data = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    start_param = init_data.get(
        "start_param",
        "",
    ).strip()

    if (
        start_param
        and start_param.isdigit()
        and int(start_param) != user_id
    ):

        referrer_id = int(start_param)

        conn = db()

        user_row = conn.execute(
            """
            SELECT referred_by
            FROM users
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()

        referrer_exists = conn.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id=?
            """,
            (referrer_id,),
        ).fetchone()

        if (
            user_row
            and user_row["referred_by"] is None
            and referrer_exists
        ):

            conn.execute(
                """
                UPDATE users
                SET referred_by=?
                WHERE user_id=?
                """,
                (
                    referrer_id,
                    user_id,
                ),
            )

            conn.execute(
                """
                INSERT OR IGNORE INTO referrals(
                    referrer_id,
                    referred_id,
                    status,
                    reward,
                    created_at
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    referrer_id,
                    user_id,
                    "pending",
                    REFERRAL_REWARD,
                    now_utc(),
                ),
            )

            conn.commit()

        conn.close()

    return {
        "success": True,
        "user_id": user_id,
    }


# ============================================================
# USER PROFILE
# ============================================================

@app.get("/api/me")
async def get_me(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM users
        WHERE user_id=?
        """,
        (user_id,),
    ).fetchone()

    conn.close()

    if not row:

        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )

    return {
        "exists": True,

        "user": {
            "id": row["user_id"],
            "username": row["username"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
        },

        "balance": float(
            row["balance"]
        ),

        "joined_all": bool(
            row["joined_all"]
        ),

        "referral_count": get_referral_count(
            user_id
        ),

        "wallets": {
            "CBE": row["cbe_number"],
            "Telebirr": row["telebirr_number"],
        },

        "suspicious": bool(
            row["suspicious"]
        ),

        "daily_bonus": DAILY_BONUS,

        "referral_reward": REFERRAL_REWARD,

        "minimum_withdrawal": MIN_WITHDRAWAL,

        "maintenance": (
            get_setting(
                "maintenance_mode",
                "0",
            ) == "1"
        ),
    }


# ============================================================
# REFERRAL COUNT
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
        (user_id,),
    ).fetchone()

    conn.close()

    return int(row["c"])


# ============================================================
# REFERRAL
# ============================================================

@app.get("/api/referral")
async def referral(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            r.referred_id,
            r.status,
            r.reward,
            r.created_at,

            u.username,
            u.first_name,
            u.joined_all

        FROM referrals r

        LEFT JOIN users u
            ON u.user_id=r.referred_id

        WHERE r.referrer_id=?

        ORDER BY r.id DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return {
        "count": get_referral_count(
            user_id
        ),

        "reward": REFERRAL_REWARD,

        "link": (
            f"https://t.me/"
            f"{BOT_USERNAME}"
            f"?start={user_id}"
        ),

        "referrals": [
            {
                "user_id": row["referred_id"],
                "username": row["username"],
                "first_name": row["first_name"],
                "joined_all": bool(
                    row["joined_all"]
                ),
                "status": row["status"],
                "reward": float(
                    REFERRAL_REWARD
                ),
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ],
    }


# ============================================================
# TELEGRAM CHANNEL VERIFICATION
# ============================================================

async def telegram_member_status(
    channel,
    user_id,
):

    if not BOT_TOKEN:
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{BOT_TOKEN}/getChatMember"
    )

    try:

        async with httpx.AsyncClient(
            timeout=15
        ) as client:

            response = await client.get(
                url,
                params={
                    "chat_id": channel,
                    "user_id": user_id,
                },
            )

        data = response.json()

        if not data.get("ok"):
            return False

        status = data["result"]["status"]

        return status in (
            "creator",
            "administrator",
            "member",
        )

    except Exception:
        return False


# ============================================================
# VERIFY CHANNELS
# ============================================================

@app.post("/api/verify")
async def verify(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    if not REQUIRED_CHANNELS:

        conn = db()

        conn.execute(
            """
            UPDATE users
            SET joined_all=1,
                updated_at=?
            WHERE user_id=?
            """,
            (
                now_utc(),
                user_id,
            ),
        )

        conn.commit()
        conn.close()

        return {
            "success": True,
            "joined_all": True,
            "channels": [],
            "message": "Verification completed.",
        }

    results = []

    for channel in REQUIRED_CHANNELS:

        joined = await telegram_member_status(
            channel,
            user_id,
        )

        results.append(
            {
                "channel": channel,
                "joined": joined,
            }
        )

    all_joined = all(
        item["joined"]
        for item in results
    )

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET joined_all=?,
            updated_at=?
        WHERE user_id=?
        """,
        (
            1 if all_joined else 0,
            now_utc(),
            user_id,
        ),
    )

    # --------------------------------------------------------
    # PAY REFERRAL AFTER FULL VERIFICATION
    # --------------------------------------------------------

    if all_joined:

        referral_row = conn.execute(
            """
            SELECT
                id,
                referrer_id,
                status
            FROM referrals
            WHERE referred_id=?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if (
            referral_row
            and referral_row["status"] != "paid"
        ):

            referrer_id = int(
                referral_row["referrer_id"]
            )

            referrer_exists = conn.execute(
                """
                SELECT user_id
                FROM users
                WHERE user_id=?
                """,
                (referrer_id,),
            ).fetchone()

            if referrer_exists:

                # ALWAYS fixed 2 ETB.
                reward = REFERRAL_REWARD

                add_balance(
                    conn,
                    referrer_id,
                    reward,
                    "referral",
                    "Successful referral reward",
                    referral_row["id"],
                )

                conn.execute(
                    """
                    UPDATE referrals
                    SET status='paid',
                        reward=?,
                        paid_at=?
                    WHERE id=?
                    AND status!='paid'
                    """,
                    (
                        reward,
                        now_utc(),
                        referral_row["id"],
                    ),
                )

                notify(
                    conn,
                    referrer_id,
                    "🎉 Referral Reward",
                    (
                        f"You earned "
                        f"{reward:.2f} ETB "
                        f"from a successful referral."
                    ),
                )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "joined_all": all_joined,
        "channels": results,
    }


# ============================================================
# DAILY BONUS
# ============================================================

@app.post("/api/daily-bonus")
async def claim_daily_bonus(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    try:

        # ----------------------------------------------------
        # ATOMIC LOCK
        # ----------------------------------------------------

        conn.execute("BEGIN IMMEDIATE")

        user_row = conn.execute(
            """
            SELECT
                balance,
                joined_all
            FROM users
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()

        if not user_row:

            conn.rollback()

            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        if not user_row["joined_all"]:

            conn.rollback()

            raise HTTPException(
                status_code=403,
                detail=(
                    "Please complete channel "
                    "verification first."
                ),
            )

        # ----------------------------------------------------
        # CHECK LAST CLAIM
        # ----------------------------------------------------

        last_claim = conn.execute(
            """
            SELECT
                created_at
            FROM daily_bonus
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if last_claim:

            last_time = parse_utc(
                last_claim["created_at"]
            )

            if last_time:

                current_time = datetime.now(
                    timezone.utc
                )

                next_claim = (
                    last_time
                    + timedelta(
                        hours=DAILY_BONUS_COOLDOWN_HOURS
                    )
                )

                if current_time < next_claim:

                    remaining = (
                        next_claim
                        - current_time
                    )

                    total_seconds = int(
                        remaining.total_seconds()
                    )

                    hours = total_seconds // 3600

                    minutes = (
                        total_seconds % 3600
                    ) // 60

                    conn.rollback()

                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Daily Bonus already "
                            f"claimed. Try again in "
                            f"{hours}h {minutes}m."
                        ),
                    )

        # ----------------------------------------------------
        # CREATE UNIQUE DAILY CLAIM RECORD
        # ----------------------------------------------------

        claim_time = now_utc()

        ethiopia_now = (
            datetime.now(timezone.utc)
            + timedelta(hours=3)
        )

        bonus_date = ethiopia_now.strftime(
            "%Y-%m-%d"
        )

        try:

            conn.execute(
                """
                INSERT INTO daily_bonus(
                    user_id,
                    bonus_date,
                    amount,
                    created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    user_id,
                    bonus_date,
                    DAILY_BONUS,
                    claim_time,
                ),
            )

        except sqlite3.IntegrityError:

            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail=(
                    "Daily Bonus has already "
                    "been claimed."
                ),
            )

        # ----------------------------------------------------
        # ADD 0.50 ETB
        # ----------------------------------------------------

        new_balance = add_balance(
            conn,
            user_id,
            DAILY_BONUS,
            "daily_bonus",
            "Daily Bonus",
            None,
        )

        notify(
            conn,
            user_id,
            "🎁 Daily Bonus",
            (
                f"{DAILY_BONUS:.2f} ETB "
                "has been added to your balance."
            ),
        )

        conn.commit()

        return {
            "success": True,
            "amount": DAILY_BONUS,
            "balance": new_balance,
            "date": bonus_date,
            "cooldown_hours": 24,
        }

    except HTTPException:
        raise

    except Exception:

        conn.rollback()

        raise HTTPException(
            status_code=500,
            detail="Daily Bonus could not be processed.",
        )

    finally:

        conn.close()


# ============================================================
# DAILY BONUS STATUS
# ============================================================

@app.get("/api/daily-bonus")
async def daily_bonus_status(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    row = conn.execute(
        """
        SELECT
            created_at
        FROM daily_bonus
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()

    conn.close()

    claimed = False
    next_claim_at = None
    remaining_seconds = 0

    if row:

        last_claim = parse_utc(
            row["created_at"]
        )

        if last_claim:

            current_time = datetime.now(
                timezone.utc
            )

            next_claim = (
                last_claim
                + timedelta(hours=24)
            )

            if current_time < next_claim:

                claimed = True

                next_claim_at = (
                    next_claim.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )

                remaining_seconds = max(
                    0,
                    int(
                        (
                            next_claim
                            - current_time
                        ).total_seconds()
                    ),
                )

    return {
        "amount": DAILY_BONUS,
        "claimed": claimed,
        "next_claim_at": next_claim_at,
        "remaining_seconds": remaining_seconds,
        "cooldown_hours": 24,
    }


# ============================================================
# WALLET
# ============================================================

class WalletRequest(BaseModel):

    wallet_type: str
    wallet_number: str


@app.get("/api/wallet")
async def wallet(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    row = conn.execute(
        """
        SELECT
            cbe_number,
            telebirr_number
        FROM users
        WHERE user_id=?
        """,
        (user_id,),
    ).fetchone()

    conn.close()

    return {
        "CBE": row["cbe_number"],
        "Telebirr": row["telebirr_number"],
    }


@app.post("/api/wallet")
async def save_wallet(
    request: WalletRequest,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    wallet_type = request.wallet_type.strip()
    wallet_number = request.wallet_number.strip()

    if wallet_type not in (
        "CBE",
        "Telebirr",
    ):

        raise HTTPException(
            status_code=400,
            detail="Wallet must be CBE or Telebirr.",
        )

    # --------------------------------------------------------
    # CBE VALIDATION
    # --------------------------------------------------------

    if wallet_type == "CBE":

        if not wallet_number.isdigit():

            raise HTTPException(
                status_code=400,
                detail=(
                    "CBE account must contain "
                    "digits only."
                ),
            )

        if len(wallet_number) != 13:

            raise HTTPException(
                status_code=400,
                detail=(
                    "CBE account must contain "
                    "13 digits."
                ),
            )

        if not wallet_number.startswith("1000"):

            raise HTTPException(
                status_code=400,
                detail=(
                    "CBE account must start "
                    "with 1000."
                ),
            )

    # --------------------------------------------------------
    # TELEBIRR VALIDATION
    # --------------------------------------------------------

    if wallet_type == "Telebirr":

        if not wallet_number.isdigit():

            raise HTTPException(
                status_code=400,
                detail=(
                    "Telebirr number must contain "
                    "digits only."
                ),
            )

        if len(wallet_number) != 10:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Telebirr number must contain "
                    "10 digits."
                ),
            )

        if not wallet_number.startswith("09"):

            raise HTTPException(
                status_code=400,
                detail=(
                    "Telebirr number must start "
                    "with 09."
                ),
            )

    conn = db()

    # --------------------------------------------------------
    # DUPLICATE WALLET CHECK
    # --------------------------------------------------------

    if wallet_type == "CBE":

        duplicate = conn.execute(
            """
            SELECT user_id
            FROM users
            WHERE cbe_number=?
            AND user_id !=?
            """,
            (
                wallet_number,
                user_id,
            ),
        ).fetchone()

    else:

        duplicate = conn.execute(
            """
            SELECT user_id
            FROM users
            WHERE telebirr_number=?
            AND user_id !=?
            """,
            (
                wallet_number,
                user_id,
            ),
        ).fetchone()

    if duplicate:

        conn.execute(
            """
            UPDATE users
            SET suspicious=1,
                updated_at=?
            WHERE user_id=?
            """,
            (
                now_utc(),
                user_id,
            ),
        )

        conn.commit()
        conn.close()

        raise HTTPException(
            status_code=400,
            detail=(
                "This wallet is already used "
                "by another account."
            ),
        )

    # --------------------------------------------------------
    # SAVE WALLET
    # --------------------------------------------------------

    if wallet_type == "CBE":

        conn.execute(
            """
            UPDATE users
            SET cbe_number=?,
                updated_at=?
            WHERE user_id=?
            """,
            (
                wallet_number,
                now_utc(),
                user_id,
            ),
        )

    else:

        conn.execute(
            """
            UPDATE users
            SET telebirr_number=?,
                updated_at=?
            WHERE user_id=?
            """,
            (
                wallet_number,
                now_utc(),
                user_id,
            ),
        )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "wallet_type": wallet_type,
        "wallet_number": wallet_number,
    }


# ============================================================
# WITHDRAW
# ============================================================

class WithdrawRequest(BaseModel):

    amount: float

    # Optional:
    # If user has both wallets, frontend can send
    # CBE or Telebirr.
    #
    # If only one wallet exists, backend auto-selects it.
    wallet_type: Optional[str] = None


@app.post("/api/withdraw")
async def withdraw(
    request: WithdrawRequest,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    amount = float(request.amount)

    if amount < MIN_WITHDRAWAL:

        raise HTTPException(
            status_code=400,
            detail=(
                f"Minimum withdrawal is "
                f"{MIN_WITHDRAWAL:.0f} ETB."
            ),
        )

    requested_wallet = (
        request.wallet_type.strip()
        if request.wallet_type
        else None
    )

    if requested_wallet not in (
        None,
        "CBE",
        "Telebirr",
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid wallet type.",
        )

    conn = db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT
                balance,
                cbe_number,
                telebirr_number,
                suspicious,
                joined_all
            FROM users
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()

        if not row:

            conn.rollback()

            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        if row["suspicious"]:

            conn.rollback()

            raise HTTPException(
                status_code=403,
                detail="Account is under review.",
            )

        if not row["joined_all"]:

            conn.rollback()

            raise HTTPException(
                status_code=403,
                detail=(
                    "Please complete channel "
                    "verification first."
                ),
            )

        cbe = row["cbe_number"]
        telebirr = row["telebirr_number"]

        # ----------------------------------------------------
        # SELECT WALLET
        # ----------------------------------------------------

        if requested_wallet == "CBE":

            if not cbe:

                conn.rollback()

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Please add your CBE wallet first."
                    ),
                )

            wallet_type = "CBE"
            wallet_number = cbe

        elif requested_wallet == "Telebirr":

            if not telebirr:

                conn.rollback()

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Please add your Telebirr wallet first."
                    ),
                )

            wallet_type = "Telebirr"
            wallet_number = telebirr

        else:

            if cbe and not telebirr:

                wallet_type = "CBE"
                wallet_number = cbe

            elif telebirr and not cbe:

                wallet_type = "Telebirr"
                wallet_number = telebirr

            elif cbe and telebirr:

                conn.rollback()

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Please select CBE or "
                        "Telebirr for this withdrawal."
                    ),
                )

            else:

                conn.rollback()

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Please add a wallet first."
                    ),
                )

        balance = float(row["balance"])

        if amount > balance:

            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail="Insufficient balance.",
            )

        # ----------------------------------------------------
        # DEDUCT BALANCE ATOMICALLY
        # ----------------------------------------------------

        conn.execute(
            """
            UPDATE users
            SET balance=balance-?,
                updated_at=?
            WHERE user_id=?
            AND balance>=?
            """,
            (
                amount,
                now_utc(),
                user_id,
                amount,
            ),
        )

        changed = conn.execute(
            "SELECT changes() AS c"
        ).fetchone()["c"]

        if changed != 1:

            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail=(
                    "Withdrawal could not "
                    "be processed."
                ),
            )

        new_balance = balance - amount

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
            VALUES(?,?,?,?,?,?)
            """,
            (
                user_id,
                amount,
                wallet_type,
                wallet_number,
                "pending",
                now_utc(),
            ),
        )

        withdrawal_id = cur.lastrowid

        conn.execute(
            """
            INSERT INTO balance_history(
                user_id,
                amount,
                type,
                description,
                reference_id,
                balance_after,
                created_at
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                user_id,
                -amount,
                "withdrawal",
                "Withdrawal request",
                withdrawal_id,
                new_balance,
                now_utc(),
            ),
        )

        notify(
            conn,
            user_id,
            "💸 Withdrawal Submitted",
            (
                f"Your withdrawal of "
                f"{amount:.2f} ETB is pending review."
            ),
        )

        conn.commit()

        return {
            "success": True,
            "withdrawal_id": withdrawal_id,
            "amount": amount,
            "wallet_type": wallet_type,
            "wallet_number": wallet_number,
            "status": "pending",
            "balance": new_balance,
        }

    except HTTPException:
        raise

    except Exception:

        conn.rollback()

        raise HTTPException(
            status_code=500,
            detail=(
                "Withdrawal could not be processed."
            ),
        )

    finally:

        conn.close()


# ============================================================
# WITHDRAWAL HISTORY
# ============================================================

@app.get("/api/withdrawals")
async def withdrawals(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            amount,
            wallet_type,
            wallet_number,
            status,
            created_at,
            reviewed_at,
            rejection_reason
        FROM withdrawals
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 50
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return {
        "withdrawals": [
            {
                "id": row["id"],
                "amount": float(
                    row["amount"]
                ),
                "wallet_type": row[
                    "wallet_type"
                ],
                "wallet_number": row[
                    "wallet_number"
                ],
                "status": row["status"],
                "created_at": row[
                    "created_at"
                ],
                "reviewed_at": row[
                    "reviewed_at"
                ],
                "rejection_reason": row[
                    "rejection_reason"
                ],
            }
            for row in rows
        ]
    }


# ============================================================
# TASKS
# ============================================================

@app.get("/api/tasks")
async def tasks(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

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

            COALESCE(
                ut.status,
                'available'
            ) AS status,

            COALESCE(
                ut.paid,
                0
            ) AS paid

        FROM tasks t

        LEFT JOIN user_tasks ut
            ON ut.task_id=t.id
            AND ut.user_id=?

        WHERE t.active=1

        ORDER BY t.id DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return {
        "tasks": [
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "channel_username": row[
                    "channel_username"
                ],
                "channel_url": row[
                    "channel_url"
                ],
                "reward": float(
                    row["reward"]
                ),
                "status": row["status"],
                "paid": bool(row["paid"]),
            }
            for row in rows
        ],

        "coming_soon": len(rows) == 0,

        "coming_soon_title": (
            "🚀 Coming Soon"
            if len(rows) == 0
            else None
        ),

        "coming_soon_message": (
            "New tasks will be available soon. "
            "Stay tuned!"
            if len(rows) == 0
            else None
        ),
    }


# ============================================================
# SUBMIT TASK PROOF
# ============================================================

@app.post("/api/tasks/{task_id}/submit")
async def submit_task(
    task_id: int,
    proof_text: str = Form(default=""),
    proof: Optional[UploadFile] = File(default=None),
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    task = conn.execute(
        """
        SELECT
            id,
            reward,
            active
        FROM tasks
        WHERE id=?
        """,
        (task_id,),
    ).fetchone()

    if not task:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    if not task["active"]:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="This task is no longer active.",
        )

    existing = conn.execute(
        """
        SELECT
            id,
            status,
            paid
        FROM user_tasks
        WHERE user_id=?
        AND task_id=?
        """,
        (
            user_id,
            task_id,
        ),
    ).fetchone()

    if existing:

        if existing["paid"]:

            conn.close()

            raise HTTPException(
                status_code=400,
                detail=(
                    "Reward for this task "
                    "has already been paid."
                ),
            )

        if existing["status"] == "pending":

            conn.close()

            raise HTTPException(
                status_code=400,
                detail=(
                    "Your proof is already "
                    "under review."
                ),
            )

    proof_data = None
    proof_filename = None
    proof_content_type = None

    if proof:

        proof_data = await proof.read()

        if len(proof_data) > MAX_PROOF_SIZE:

            conn.close()

            raise HTTPException(
                status_code=400,
                detail=(
                    "Proof file is too large. "
                    "Maximum size is 8 MB."
                ),
            )

        proof_filename = proof.filename

        proof_content_type = (
            proof.content_type
            or "application/octet-stream"
        )

        if not proof_content_type.startswith(
            "image/"
        ):

            conn.close()

            raise HTTPException(
                status_code=400,
                detail="Proof must be an image.",
            )

    if (
        not proof_data
        and not proof_text.strip()
    ):

        conn.close()

        raise HTTPException(
            status_code=400,
            detail=(
                "Please submit a screenshot "
                "or proof text."
            ),
        )

    if existing:

        conn.execute(
            """
            UPDATE user_tasks
            SET status='pending',
                proof_text=?,
                proof_data=?,
                proof_filename=?,
                proof_content_type=?,
                submitted_at=?,
                reviewed_at=NULL,
                rejection_reason=NULL
            WHERE id=?
            """,
            (
                proof_text.strip(),
                proof_data,
                proof_filename,
                proof_content_type,
                now_utc(),
                existing["id"],
            ),
        )

    else:

        conn.execute(
            """
            INSERT INTO user_tasks(
                user_id,
                task_id,
                status,
                proof_text,
                proof_data,
                proof_filename,
                proof_content_type,
                reward,
                submitted_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                task_id,
                "pending",
                proof_text.strip(),
                proof_data,
                proof_filename,
                proof_content_type,
                float(task["reward"]),
                now_utc(),
            ),
        )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "task_id": task_id,
        "status": "pending",
        "message": (
            "Proof submitted successfully."
        ),
    }


# ============================================================
# USER TASK SUBMISSIONS
# ============================================================

@app.get("/api/my-task-submissions")
async def my_task_submissions(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            ut.id,
            ut.task_id,
            t.title,
            ut.status,
            ut.reward,
            ut.submitted_at,
            ut.reviewed_at,
            ut.rejection_reason,
            ut.paid

        FROM user_tasks ut

        JOIN tasks t
            ON t.id=ut.task_id

        WHERE ut.user_id=?

        ORDER BY ut.id DESC
        LIMIT 100
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return {
        "submissions": [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "title": row["title"],
                "status": row["status"],
                "reward": float(
                    row["reward"]
                ),
                "submitted_at": row[
                    "submitted_at"
                ],
                "reviewed_at": row[
                    "reviewed_at"
                ],
                "rejection_reason": row[
                    "rejection_reason"
                ],
                "paid": bool(row["paid"]),
            }
            for row in rows
        ]
    }


# ============================================================
# BALANCE HISTORY
# ============================================================

@app.get("/api/balance-history")
async def balance_history(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            amount,
            type,
            description,
            reference_id,
            balance_after,
            created_at

        FROM balance_history

        WHERE user_id=?

        ORDER BY id DESC

        LIMIT 100
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return {
        "history": [
            {
                "id": row["id"],
                "amount": float(
                    row["amount"]
                ),
                "type": row["type"],
                "description": row[
                    "description"
                ],
                "reference_id": row[
                    "reference_id"
                ],
                "balance_after": float(
                    row["balance_after"]
                ),
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ]
    }


# ============================================================
# NOTIFICATIONS
# ============================================================

@app.get("/api/notifications")
async def notifications(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            title,
            message,
            is_read,
            created_at

        FROM notifications

        WHERE user_id=?

        ORDER BY id DESC

        LIMIT 50
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return {
        "notifications": [
            {
                "id": row["id"],
                "title": row["title"],
                "message": row["message"],
                "is_read": bool(
                    row["is_read"]
                ),
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ]
    }


@app.post("/api/notifications/read")
async def mark_notifications_read(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    user_id = ensure_user(user)

    conn = db()

    conn.execute(
        """
        UPDATE notifications
        SET is_read=1
        WHERE user_id=?
        """,
        (user_id,),
    )

    conn.commit()
    conn.close()

    return {
        "success": True
    }


# ============================================================
# ANNOUNCEMENTS
# ============================================================

@app.get("/api/announcements")
async def announcements(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    ensure_user(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            title,
            message,
            created_at

        FROM announcements

        WHERE active=1

        ORDER BY id DESC

        LIMIT 20
        """
    ).fetchall()

    conn.close()

    return {
        "announcements": [
            {
                "id": row["id"],
                "title": row["title"],
                "message": row["message"],
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ]
    }


# ============================================================
# ADMIN
# ============================================================

@app.get("/api/admin/me")
async def admin_me(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    return {
        "success": True,
        "admin_id": admin_id,
    }


# ============================================================
# ADMIN DASHBOARD
# ============================================================

@app.get("/api/admin/dashboard")
async def admin_dashboard(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    conn = db()

    total_users = conn.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    verified_users = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM users
        WHERE joined_all=1
        """
    ).fetchone()["c"]

    total_referrals = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM referrals
        """
    ).fetchone()["c"]

    successful_referrals = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM referrals
        WHERE status='paid'
        """
    ).fetchone()["c"]

    total_referral_rewards = conn.execute(
        """
        SELECT COALESCE(
            SUM(amount),
            0
        ) AS total

        FROM balance_history

        WHERE type='referral'
        """
    ).fetchone()["total"]

    pending_withdrawals = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM withdrawals
        WHERE status='pending'
        """
    ).fetchone()["c"]

    total_withdrawn = conn.execute(
        """
        SELECT COALESCE(
            SUM(amount),
            0
        ) AS total

        FROM withdrawals

        WHERE status='paid'
        """
    ).fetchone()["total"]

    total_balance = conn.execute(
        """
        SELECT COALESCE(
            SUM(balance),
            0
        ) AS total

        FROM users
        """
    ).fetchone()["total"]

    pending_tasks = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM user_tasks
        WHERE status='pending'
        """
    ).fetchone()["c"]

    conn.close()

    return {
        "admin_id": admin_id,

        "total_users": int(
            total_users
        ),

        "verified_users": int(
            verified_users
        ),

        "total_referrals": int(
            total_referrals
        ),

        "successful_referrals": int(
            successful_referrals
        ),

        "total_referral_rewards": float(
            total_referral_rewards
        ),

        "pending_withdrawals": int(
            pending_withdrawals
        ),

        "total_withdrawn": float(
            total_withdrawn
        ),

        "total_balance": float(
            total_balance
        ),

        "pending_task_submissions": int(
            pending_tasks
        ),

        "maintenance_mode": (
            get_setting(
                "maintenance_mode",
                "0",
            ) == "1"
        ),
    }


# ============================================================
# ADMIN WITHDRAWALS
# ============================================================

@app.get("/api/admin/withdrawals")
async def admin_withdrawals(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_required(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            w.id,
            w.user_id,
            w.amount,
            w.wallet_type,
            w.wallet_number,
            w.status,
            w.created_at,
            w.reviewed_at,
            w.rejection_reason,

            u.username,
            u.first_name,
            u.last_name

        FROM withdrawals w

        LEFT JOIN users u
            ON u.user_id=w.user_id

        ORDER BY
            CASE
                WHEN w.status='pending'
                THEN 0
                ELSE 1
            END,

            w.id DESC
        """
    ).fetchall()

    conn.close()

    return {
        "withdrawals": [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "username": row["username"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "amount": float(
                    row["amount"]
                ),
                "wallet_type": row[
                    "wallet_type"
                ],
                "wallet_number": row[
                    "wallet_number"
                ],
                "status": row["status"],
                "created_at": row[
                    "created_at"
                ],
                "reviewed_at": row[
                    "reviewed_at"
                ],
                "rejection_reason": row[
                    "rejection_reason"
                ],
            }
            for row in rows
        ]
    }


class WithdrawalDecision(BaseModel):

    reason: str = ""


# ============================================================
# ADMIN APPROVE WITHDRAWAL
# ============================================================

@app.post(
    "/api/admin/withdrawals/{withdrawal_id}/approve"
)
async def approve_withdrawal(
    withdrawal_id: int,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    conn = db()

    row = conn.execute(
        """
        SELECT
            user_id,
            amount,
            status

        FROM withdrawals

        WHERE id=?
        """,
        (withdrawal_id,),
    ).fetchone()

    if not row:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Withdrawal not found.",
        )

    if row["status"] != "pending":

        conn.close()

        raise HTTPException(
            status_code=400,
            detail=(
                "This withdrawal has "
                "already been reviewed."
            ),
        )

    conn.execute(
        """
        UPDATE withdrawals
        SET status='paid',
            reviewed_at=?
        WHERE id=?
        """,
        (
            now_utc(),
            withdrawal_id,
        ),
    )

    notify(
        conn,
        row["user_id"],
        "✅ Withdrawal Approved",
        (
            f"Your withdrawal of "
            f"{float(row['amount']):.2f} ETB "
            f"has been approved."
        ),
    )

    admin_log(
        conn,
        admin_id,
        "approve_withdrawal",
        "withdrawal",
        withdrawal_id,
        f"Amount={row['amount']}",
    )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "status": "paid",
    }


# ============================================================
# ADMIN REJECT WITHDRAWAL
# ============================================================

@app.post(
    "/api/admin/withdrawals/{withdrawal_id}/reject"
)
async def reject_withdrawal(
    withdrawal_id: int,
    request: WithdrawalDecision,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    conn = db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT
                user_id,
                amount,
                status

            FROM withdrawals

            WHERE id=?
            """,
            (withdrawal_id,),
        ).fetchone()

        if not row:

            conn.rollback()

            raise HTTPException(
                status_code=404,
                detail="Withdrawal not found.",
            )

        if row["status"] != "pending":

            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail=(
                    "This withdrawal has "
                    "already been reviewed."
                ),
            )

        amount = float(
            row["amount"]
        )

        new_balance = add_balance(
            conn,
            row["user_id"],
            amount,
            "withdrawal_refund",
            "Rejected withdrawal refund",
            withdrawal_id,
        )

        conn.execute(
            """
            UPDATE withdrawals
            SET status='rejected',
                reviewed_at=?,
                rejection_reason=?
            WHERE id=?
            AND status='pending'
            """,
            (
                now_utc(),
                request.reason.strip(),
                withdrawal_id,
            ),
        )

        notify(
            conn,
            row["user_id"],
            "❌ Withdrawal Rejected",
            (
                f"Your withdrawal of "
                f"{amount:.2f} ETB was rejected. "
                f"The amount has been returned "
                f"to your balance."
            ),
        )

        admin_log(
            conn,
            admin_id,
            "reject_withdrawal",
            "withdrawal",
            withdrawal_id,
            (
                f"Refund={amount}; "
                f"Reason={request.reason}"
            ),
        )

        conn.commit()

        return {
            "success": True,
            "status": "rejected",
            "refunded": amount,
            "balance": new_balance,
        }

    except HTTPException:
        raise

    except Exception:

        conn.rollback()

        raise HTTPException(
            status_code=500,
            detail=(
                "Withdrawal rejection failed."
            ),
        )

    finally:

        conn.close()


# ============================================================
# ADMIN TASK CREATE
# ============================================================

class TaskCreateRequest(BaseModel):

    title: str
    description: str = ""
    channel_username: str = ""
    channel_url: str = ""
    reward: float


@app.post("/api/admin/tasks")
async def create_task(
    request: TaskCreateRequest,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    if request.reward <= 0:

        raise HTTPException(
            status_code=400,
            detail="Reward must be greater than 0.",
        )

    if not request.title.strip():

        raise HTTPException(
            status_code=400,
            detail="Task title is required.",
        )

    conn = db()

    cur = conn.execute(
        """
        INSERT INTO tasks(
            title,
            description,
            channel_username,
            channel_url,
            reward,
            active,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            request.title.strip(),
            request.description.strip(),
            request.channel_username.strip(),
            request.channel_url.strip(),
            request.reward,
            1,
            now_utc(),
            now_utc(),
        ),
    )

    task_id = cur.lastrowid

    admin_log(
        conn,
        admin_id,
        "create_task",
        "task",
        task_id,
        request.title.strip(),
    )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "task_id": task_id,
    }


# ============================================================
# ADMIN TASKS
# ============================================================

@app.get("/api/admin/tasks")
async def admin_tasks(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_required(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            title,
            description,
            channel_username,
            channel_url,
            reward,
            active,
            created_at

        FROM tasks

        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return {
        "tasks": [
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "channel_username": row[
                    "channel_username"
                ],
                "channel_url": row[
                    "channel_url"
                ],
                "reward": float(
                    row["reward"]
                ),
                "active": bool(
                    row["active"]
                ),
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ]
    }


# ============================================================
# ADMIN TASK SUBMISSIONS
# ============================================================

@app.get("/api/admin/task-submissions")
async def admin_task_submissions(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_required(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            ut.id,
            ut.user_id,
            ut.task_id,
            ut.status,
            ut.reward,
            ut.proof_text,
            ut.proof_filename,
            ut.proof_content_type,
            ut.submitted_at,
            ut.reviewed_at,
            ut.rejection_reason,
            ut.paid,

            t.title,

            u.username,
            u.first_name

        FROM user_tasks ut

        JOIN tasks t
            ON t.id=ut.task_id

        JOIN users u
            ON u.user_id=ut.user_id

        ORDER BY
            CASE
                WHEN ut.status='pending'
                THEN 0
                ELSE 1
            END,

            ut.id DESC
        """
    ).fetchall()

    conn.close()

    return {
        "submissions": [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "task_id": row["task_id"],
                "username": row["username"],
                "first_name": row["first_name"],
                "task_title": row["title"],
                "status": row["status"],
                "reward": float(
                    row["reward"]
                ),
                "proof_text": row[
                    "proof_text"
                ],
                "proof_filename": row[
                    "proof_filename"
                ],
                "proof_available": bool(
                    row["proof_filename"]
                ),
                "submitted_at": row[
                    "submitted_at"
                ],
                "reviewed_at": row[
                    "reviewed_at"
                ],
                "rejection_reason": row[
                    "rejection_reason"
                ],
                "paid": bool(
                    row["paid"]
                ),
            }
            for row in rows
        ]
    }


# ============================================================
# ADMIN VIEW PROOF IMAGE
# ============================================================

@app.get(
    "/api/admin/task-submissions/{submission_id}/proof"
)
async def admin_task_proof(
    submission_id: int,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_required(user)

    conn = db()

    row = conn.execute(
        """
        SELECT
            proof_data,
            proof_content_type

        FROM user_tasks

        WHERE id=?
        """,
        (submission_id,),
    ).fetchone()

    conn.close()

    if not row or not row["proof_data"]:

        raise HTTPException(
            status_code=404,
            detail="Proof image not found.",
        )

    return Response(
        content=row["proof_data"],
        media_type=(
            row["proof_content_type"]
            or "image/jpeg"
        ),
    )


class TaskDecision(BaseModel):

    reason: str = ""


# ============================================================
# ADMIN APPROVE TASK
# ============================================================

@app.post(
    "/api/admin/task-submissions/{submission_id}/approve"
)
async def approve_task(
    submission_id: int,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    conn = db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT
                ut.id,
                ut.user_id,
                ut.task_id,
                ut.reward,
                ut.status,
                ut.paid,

                t.title

            FROM user_tasks ut

            JOIN tasks t
                ON t.id=ut.task_id

            WHERE ut.id=?
            """,
            (submission_id,),
        ).fetchone()

        if not row:

            conn.rollback()

            raise HTTPException(
                status_code=404,
                detail="Submission not found.",
            )

        if row["paid"]:

            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail=(
                    "Task reward has already "
                    "been paid."
                ),
            )

        if row["status"] != "pending":

            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail=(
                    "This submission is not "
                    "pending."
                ),
            )

        reward = float(
            row["reward"]
        )

        new_balance = add_balance(
            conn,
            row["user_id"],
            reward,
            "task_reward",
            f"Task reward: {row['title']}",
            row["task_id"],
        )

        conn.execute(
            """
            UPDATE user_tasks
            SET status='approved',
                paid=1,
                reviewed_at=?
            WHERE id=?
            AND status='pending'
            AND paid=0
            """,
            (
                now_utc(),
                submission_id,
            ),
        )

        notify(
            conn,
            row["user_id"],
            "✅ Task Approved",
            (
                f"Your task was approved "
                f"and {reward:.2f} ETB "
                f"was added to your balance."
            ),
        )

        admin_log(
            conn,
            admin_id,
            "approve_task",
            "task_submission",
            submission_id,
            f"Reward={reward}",
        )

        conn.commit()

        return {
            "success": True,
            "status": "approved",
            "reward": reward,
            "balance": new_balance,
        }

    except HTTPException:
        raise

    except Exception:

        conn.rollback()

        raise HTTPException(
            status_code=500,
            detail="Task approval failed.",
        )

    finally:

        conn.close()


# ============================================================
# ADMIN REJECT TASK
# ============================================================

@app.post(
    "/api/admin/task-submissions/{submission_id}/reject"
)
async def reject_task(
    submission_id: int,
    request: TaskDecision,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    conn = db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT
                id,
                user_id,
                status,
                paid

            FROM user_tasks

            WHERE id=?
            """,
            (submission_id,),
        ).fetchone()

        if not row:

            conn.rollback()

            raise HTTPException(
                status_code=404,
                detail="Submission not found.",
            )

        if row["paid"]:

            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail=(
                    "This task has already "
                    "been paid."
                ),
            )

        if row["status"] != "pending":

            conn.rollback()

            raise HTTPException(
                status_code=400,
                detail=(
                    "This submission is not "
                    "pending."
                ),
            )

        conn.execute(
            """
            UPDATE user_tasks
            SET status='rejected',
                reviewed_at=?,
                rejection_reason=?
            WHERE id=?
            AND status='pending'
            """,
            (
                now_utc(),
                request.reason.strip(),
                submission_id,
            ),
        )

        notify(
            conn,
            row["user_id"],
            "❌ Task Rejected",
            (
                "Your task proof was rejected."
                + (
                    f" Reason: {request.reason.strip()}"
                    if request.reason.strip()
                    else ""
                )
            ),
        )

        admin_log(
            conn,
            admin_id,
            "reject_task",
            "task_submission",
            submission_id,
            request.reason.strip(),
        )

        conn.commit()

        return {
            "success": True,
            "status": "rejected",
        }

    except HTTPException:
        raise

    except Exception:

        conn.rollback()

        raise HTTPException(
            status_code=500,
            detail="Task rejection failed.",
        )

    finally:

        conn.close()


# ============================================================
# ADMIN USERS
# ============================================================

@app.get("/api/admin/users")
async def admin_users(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_required(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            user_id,
            username,
            first_name,
            last_name,
            balance,
            joined_all,
            suspicious,
            cbe_number,
            telebirr_number,
            registered_at

        FROM users

        ORDER BY user_id DESC
        """
    ).fetchall()

    conn.close()

    return {
        "users": [
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "balance": float(
                    row["balance"]
                ),
                "successful_referrals":
                    get_referral_count(
                        row["user_id"]
                    ),
                "joined_all": bool(
                    row["joined_all"]
                ),
                "suspicious": bool(
                    row["suspicious"]
                ),
                "cbe": row["cbe_number"],
                "telebirr": row[
                    "telebirr_number"
                ],
                "registered_at": row[
                    "registered_at"
                ],
            }
            for row in rows
        ]
    }


# ============================================================
# ADMIN USER DETAILS
# ============================================================

@app.get("/api/admin/users/{target_user_id}")
async def admin_user_details(
    target_user_id: int,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_required(user)

    conn = db()

    user_row = conn.execute(
        """
        SELECT *
        FROM users
        WHERE user_id=?
        """,
        (target_user_id,),
    ).fetchone()

    if not user_row:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )

    referrals = conn.execute(
        """
        SELECT
            r.id,
            r.referred_id,
            r.status,
            r.reward,
            r.created_at,
            r.paid_at,

            u.username,
            u.first_name,
            u.joined_all

        FROM referrals r

        LEFT JOIN users u
            ON u.user_id=r.referred_id

        WHERE r.referrer_id=?

        ORDER BY r.id DESC
        """,
        (target_user_id,),
    ).fetchall()

    withdrawals_rows = conn.execute(
        """
        SELECT
            id,
            amount,
            wallet_type,
            wallet_number,
            status,
            created_at,
            reviewed_at,
            rejection_reason

        FROM withdrawals

        WHERE user_id=?

        ORDER BY id DESC
        """,
        (target_user_id,),
    ).fetchall()

    history = conn.execute(
        """
        SELECT
            id,
            amount,
            type,
            description,
            reference_id,
            balance_after,
            created_at

        FROM balance_history

        WHERE user_id=?

        ORDER BY id DESC

        LIMIT 200
        """,
        (target_user_id,),
    ).fetchall()

    conn.close()

    return {
        "user": {
            "user_id": user_row[
                "user_id"
            ],
            "username": user_row[
                "username"
            ],
            "first_name": user_row[
                "first_name"
            ],
            "last_name": user_row[
                "last_name"
            ],
            "balance": float(
                user_row["balance"]
            ),
            "joined_all": bool(
                user_row["joined_all"]
            ),
            "suspicious": bool(
                user_row["suspicious"]
            ),
            "cbe": user_row[
                "cbe_number"
            ],
            "telebirr": user_row[
                "telebirr_number"
            ],
            "registered_at": user_row[
                "registered_at"
            ],
        },

        "referrals": [
            {
                "id": row["id"],
                "user_id": row[
                    "referred_id"
                ],
                "username": row[
                    "username"
                ],
                "first_name": row[
                    "first_name"
                ],
                "joined_all": bool(
                    row["joined_all"]
                ),
                "status": row[
                    "status"
                ],
                "reward": float(
                    row["reward"]
                ),
                "created_at": row[
                    "created_at"
                ],
                "paid_at": row[
                    "paid_at"
                ],
            }
            for row in referrals
        ],

        "withdrawals": [
            {
                "id": row["id"],
                "amount": float(
                    row["amount"]
                ),
                "wallet_type": row[
                    "wallet_type"
                ],
                "wallet_number": row[
                    "wallet_number"
                ],
                "status": row[
                    "status"
                ],
                "created_at": row[
                    "created_at"
                ],
                "reviewed_at": row[
                    "reviewed_at"
                ],
                "rejection_reason": row[
                    "rejection_reason"
                ],
            }
            for row in withdrawals_rows
        ],

        "balance_history": [
            {
                "id": row["id"],
                "amount": float(
                    row["amount"]
                ),
                "type": row[
                    "type"
                ],
                "description": row[
                    "description"
                ],
                "reference_id": row[
                    "reference_id"
                ],
                "balance_after": float(
                    row["balance_after"]
                ),
                "created_at": row[
                    "created_at"
                ],
            }
            for row in history
        ],
    }


# ============================================================
# ADMIN BALANCE ADJUSTMENT
# ============================================================

class BalanceAdjustment(BaseModel):

    amount: float
    reason: str


@app.post(
    "/api/admin/users/{target_user_id}/balance"
)
async def adjust_balance(
    target_user_id: int,
    request: BalanceAdjustment,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    if request.amount == 0:

        raise HTTPException(
            status_code=400,
            detail="Amount cannot be zero.",
        )

    if not request.reason.strip():

        raise HTTPException(
            status_code=400,
            detail="Reason is required.",
        )

    conn = db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id=?
            """,
            (target_user_id,),
        ).fetchone()

        if not row:

            conn.rollback()

            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        new_balance = add_balance(
            conn,
            target_user_id,
            request.amount,
            "admin_adjustment",
            request.reason.strip(),
            None,
        )

        notify(
            conn,
            target_user_id,
            "💰 Balance Updated",
            (
                f"Admin balance adjustment: "
                f"{request.amount:+.2f} ETB."
            ),
        )

        admin_log(
            conn,
            admin_id,
            "balance_adjustment",
            "user",
            target_user_id,
            (
                f"Amount={request.amount}; "
                f"Reason={request.reason}"
            ),
        )

        conn.commit()

        return {
            "success": True,
            "amount": request.amount,
            "balance": new_balance,
        }

    except HTTPException:
        raise

    except Exception:

        conn.rollback()

        raise HTTPException(
            status_code=500,
            detail="Balance adjustment failed.",
        )

    finally:

        conn.close()


# ============================================================
# ADMIN ANNOUNCEMENTS
# ============================================================

class AnnouncementRequest(BaseModel):

    title: str
    message: str


@app.post("/api/admin/announcements")
async def create_announcement(
    request: AnnouncementRequest,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    if not request.title.strip():

        raise HTTPException(
            status_code=400,
            detail="Announcement title is required.",
        )

    if not request.message.strip():

        raise HTTPException(
            status_code=400,
            detail="Announcement message is required.",
        )

    conn = db()

    cur = conn.execute(
        """
        INSERT INTO announcements(
            title,
            message,
            active,
            created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            request.title.strip(),
            request.message.strip(),
            1,
            now_utc(),
        ),
    )

    announcement_id = cur.lastrowid

    users = conn.execute(
        "SELECT user_id FROM users"
    ).fetchall()

    for row in users:

        notify(
            conn,
            row["user_id"],
            request.title.strip(),
            request.message.strip(),
        )

    admin_log(
        conn,
        admin_id,
        "create_announcement",
        "announcement",
        announcement_id,
        request.title.strip(),
    )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "announcement_id": announcement_id,
    }


# ============================================================
# ADMIN MAINTENANCE
# ============================================================

class MaintenanceRequest(BaseModel):

    enabled: bool


@app.post("/api/admin/maintenance")
async def maintenance(
    request: MaintenanceRequest,
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_id = admin_required(user)

    set_setting(
        "maintenance_mode",
        "1" if request.enabled else "0",
    )

    conn = db()

    admin_log(
        conn,
        admin_id,
        "maintenance_mode",
        "settings",
        None,
        f"enabled={request.enabled}",
    )

    conn.commit()
    conn.close()

    return {
        "success": True,
        "maintenance_mode": request.enabled,
    }


# ============================================================
# ADMIN LOGS
# ============================================================

@app.get("/api/admin/logs")
async def admin_logs(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_required(user)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            admin_id,
            action,
            target_type,
            target_id,
            details,
            created_at

        FROM admin_logs

        ORDER BY id DESC

        LIMIT 300
        """
    ).fetchall()

    conn.close()

    return {
        "logs": [
            {
                "id": row["id"],
                "admin_id": row[
                    "admin_id"
                ],
                "action": row[
                    "action"
                ],
                "target_type": row[
                    "target_type"
                ],
                "target_id": row[
                    "target_id"
                ],
                "details": row[
                    "details"
                ],
                "created_at": row[
                    "created_at"
                ],
            }
            for row in rows
        ]
    }


# ============================================================
# TESTING
# ============================================================

@app.get("/api/admin/testing")
async def testing_info(
    authorization: Optional[str] = Header(default=None),
):

    user, _ = get_current_user(
        authorization
    )

    admin_required(user)

    conn = db()

    users = conn.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    tasks_count = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks"
    ).fetchone()["c"]

    submissions = conn.execute(
        "SELECT COUNT(*) AS c FROM user_tasks"
    ).fetchone()["c"]

    withdrawals_count = conn.execute(
        "SELECT COUNT(*) AS c FROM withdrawals"
    ).fetchone()["c"]

    conn.close()

    return {
        "database": "ok",

        "users": int(users),

        "tasks": int(
            tasks_count
        ),

        "task_submissions": int(
            submissions
        ),

        "withdrawals": int(
            withdrawals_count
        ),

        "referral_reward": REFERRAL_REWARD,

        "daily_bonus": DAILY_BONUS,

        "daily_bonus_cooldown_hours": 24,

        "minimum_withdrawal": MIN_WITHDRAWAL,

        "admin_configured": bool(
            ADMIN_IDS
        ),

        "required_channels": REQUIRED_CHANNELS,
    }

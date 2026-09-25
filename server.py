import os
import hmac
import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qsl
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

APP_NAME = "Falcon World"
BOT_USERNAME = "FalconWorld_Bot"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_FILE = os.getenv("DB_FILE", "global_cash.db")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DEFAULT_REQUIRED_CHANNELS = [
    "@ethiocashflow",
    "@Sheger_tech1",
    "@EthioVortex1",
    "@AmanIncomeLab",
    "@OnlineIncomeHub07",
    "@Paymentprooff2",
]
REQUIRED_CHANNELS = [x.strip() for x in os.getenv("REQUIRED_CHANNELS", "").split(",") if x.strip()] or DEFAULT_REQUIRED_CHANNELS
REFERRAL_REWARD = 2.00
DAILY_BONUS = 0.50
MIN_WITHDRAWAL = 30.00
SUPPORT_USERNAME = "@AmanM_12"
DAILY_BONUS_COOLDOWN_HOURS = 24
MAX_PROOF_SIZE = 8 * 1024 * 1024
ETHIOPIA_TZ = timezone(timedelta(hours=3))

app = FastAPI(title=APP_NAME)


def db():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now_utc()).isoformat()


def init_db():
    conn = db()
    conn.executescript("""
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
        referrer_id INTEGER NOT NULL,
        referred_id INTEGER PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'pending',
        reward REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        paid_at TEXT
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        channel_username TEXT,
        channel_url TEXT,
        reward REAL NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_tasks (
        user_id INTEGER NOT NULL,
        task_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'submitted',
        proof_text TEXT,
        proof_data BLOB,
        proof_filename TEXT,
        content_type TEXT,
        reward REAL NOT NULL DEFAULT 0,
        submitted_at TEXT NOT NULL,
        reviewed_at TEXT,
        rejection_reason TEXT,
        paid INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id, task_id)
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
        rejection_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS balance_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        balance_after REAL NOT NULL,
        reason TEXT NOT NULL,
        reference TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS daily_bonus (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        bonus_date TEXT NOT NULL,
        amount REAL NOT NULL DEFAULT 0.50,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, bonus_date)
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        is_read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
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
        details TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    conn.commit()
    conn.close()


init_db()


def add_balance(conn, user_id, amount, reason, reference=None):
    row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "User not found")
    new_balance = round(float(row["balance"]) + float(amount), 2)
    conn.execute("UPDATE users SET balance=?, updated_at=? WHERE user_id=?", (new_balance, iso(), user_id))
    conn.execute(
        "INSERT INTO balance_history(user_id,amount,balance_after,reason,reference,created_at) VALUES(?,?,?,?,?,?)",
        (user_id, float(amount), new_balance, reason, reference, iso()),
    )
    return new_balance


def notify(conn, user_id, title, message):
    conn.execute(
        "INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
        (user_id, title, message, iso()),
    )


def validate_init_data(init_data: str):
    if not init_data:
        raise HTTPException(401, "Telegram initData is required")
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash or not BOT_TOKEN:
            raise ValueError()
        data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise ValueError()
        auth_date = int(pairs.get("auth_date", "0"))
        if auth_date and datetime.now(timezone.utc).timestamp() - auth_date > 86400:
            raise ValueError()
        return pairs
    except Exception:
        raise HTTPException(401, "Invalid Telegram initData")


def current_user(init_data):
    data = validate_init_data(init_data)
    try:
        tg = json.loads(data.get("user", "{}"))
        user_id = int(tg["id"])
    except Exception:
        raise HTTPException(401, "Invalid Telegram user data")
    return user_id, tg, data


def ensure_user(conn, tg, referred_by=None):
    user_id = int(tg["id"])
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    t = iso()
    if row:
        conn.execute(
            "UPDATE users SET username=?,first_name=?,last_name=?,updated_at=? WHERE user_id=?",
            (tg.get("username"), tg.get("first_name"), tg.get("last_name"), t, user_id),
        )
        return
    conn.execute(
        "INSERT INTO users(user_id,username,first_name,last_name,referred_by,registered_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (user_id, tg.get("username"), tg.get("first_name"), tg.get("last_name"), referred_by, t, t),
    )
    if referred_by and referred_by != user_id:
        exists = conn.execute("SELECT 1 FROM users WHERE user_id=?", (referred_by,)).fetchone()
        if exists:
            conn.execute(
                "INSERT OR IGNORE INTO referrals(referrer_id,referred_id,status,reward,created_at) VALUES(?,?, 'pending', ?, ?)",
                (referred_by, user_id, REFERRAL_REWARD, t),
            )


def auth_user(init_data, conn, start_param=None):
    user_id, tg, data = current_user(init_data)
    start_param = start_param or data.get("start_param", "") or ""
    referred_by = int(start_param) if str(start_param).isdigit() and int(start_param) != user_id else None
    ensure_user(conn, tg, referred_by)
    return user_id, tg


class RegisterIn(BaseModel):
    init_data: str
    start_param: Optional[str] = None

class WalletIn(BaseModel):
    init_data: str
    cbe_number: Optional[str] = None
    telebirr_number: Optional[str] = None

class WithdrawIn(BaseModel):
    init_data: str
    amount: float
    wallet_type: Optional[str] = None

class VerifyIn(BaseModel):
    init_data: str

class DailyBonusIn(BaseModel):
    init_data: str


def is_admin(user_id):
    return user_id in ADMIN_IDS


async def telegram_get_chat_member(user_id, channel):
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"
    chat = channel if channel.startswith("@") else "@" + channel
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params={"chat_id": chat, "user_id": user_id})
            data = r.json()
        if not data.get("ok"):
            return False
        status = data.get("result", {}).get("status")
        return status in {"creator", "administrator", "member"} or (status == "restricted" and data.get("result", {}).get("is_member"))
    except Exception:
        return False


@app.get("/")
async def home():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return Response("Falcon World API is running.", media_type="text/plain")

@app.get("/health")
async def health():
    return {"ok": True, "app": APP_NAME, "daily_bonus": DAILY_BONUS, "referral_reward": REFERRAL_REWARD, "min_withdrawal": MIN_WITHDRAWAL}

@app.post("/api/register")
async def register(body: RegisterIn):
    conn = db()
    try:
        user_id, tg = auth_user(body.init_data, conn, body.start_param)
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return {"success": True, "user": dict(row)}
    finally:
        conn.close()

@app.get("/api/me")
async def me(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return {"success": True, "user": dict(row)}
    finally:
        conn.close()

@app.get("/api/referral")
async def referral(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND status='paid'", (user_id,)).fetchone()["c"]
        total = conn.execute("SELECT COALESCE(SUM(reward),0) s FROM referrals WHERE referrer_id=? AND status='paid'", (user_id,)).fetchone()["s"]
        return {"success": True, "count": count, "earned": float(total), "reward": REFERRAL_REWARD, "link": f"https://t.me/{BOT_USERNAME}?start={user_id}"}
    finally:
        conn.close()

@app.get("/api/required-channels")
async def required_channels():
    channels = []
    for ch in REQUIRED_CHANNELS:
        username = ch.lstrip("@").strip()
        channels.append({
            "username": "@" + username,
            "url": f"https://t.me/{username}"
        })
    return {"success": True, "channels": channels}

@app.post("/api/verify")
async def verify(body: VerifyIn):
    conn = db()
    try:
        user_id, tg = auth_user(body.init_data, conn)
        conn.execute("BEGIN IMMEDIATE")
        missing = []
        for ch in REQUIRED_CHANNELS:
            if not await telegram_get_chat_member(user_id, ch):
                missing.append(ch.lstrip("@"))
        joined_all = not missing
        conn.execute("UPDATE users SET joined_all=?,updated_at=? WHERE user_id=?", (1 if joined_all else 0, iso(), user_id))
        referral_paid = False
        if joined_all:
            ref = conn.execute("SELECT * FROM referrals WHERE referred_id=? AND status='pending'", (user_id,)).fetchone()
            if ref:
                changed = conn.execute("UPDATE referrals SET status='paid',paid_at=? WHERE referred_id=? AND status='pending'", (iso(), user_id)).rowcount
                if changed:
                    add_balance(conn, ref["referrer_id"], REFERRAL_REWARD, "referral_reward", str(user_id))
                    conn.execute("UPDATE users SET referral_paid=1,updated_at=? WHERE user_id=?", (iso(), user_id))
                    notify(conn, ref["referrer_id"], "🎉 Referral Reward", f"You earned {REFERRAL_REWARD:.2f} ETB from a referral.")
                    referral_paid = True
        conn.commit()
        return {"success": True, "joined_all": joined_all, "missing": missing, "referral_paid": referral_paid, "reward": REFERRAL_REWARD}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@app.get("/api/daily-bonus")
async def daily_bonus_status(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        last = conn.execute("SELECT created_at FROM daily_bonus WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        available = True
        next_claim_at = None
        if last:
            last_dt = datetime.fromisoformat(last["created_at"])
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            nxt = last_dt + timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS)
            if now_utc() < nxt:
                available = False
                next_claim_at = nxt.isoformat()
        return {"success": True, "amount": DAILY_BONUS, "available": available, "next_claim_at": next_claim_at, "cooldown_hours": DAILY_BONUS_COOLDOWN_HOURS}
    finally:
        conn.close()

@app.post("/api/daily-bonus")
async def claim_daily_bonus(body: DailyBonusIn):
    conn = db()
    try:
        user_id, tg = auth_user(body.init_data, conn)
        conn.commit()
        row = conn.execute("SELECT joined_all,suspicious FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row["joined_all"]:
            raise HTTPException(403, "Please verify all required channels first")
        if row["suspicious"]:
            raise HTTPException(403, "Account is restricted")
        conn.execute("BEGIN IMMEDIATE")
        last = conn.execute("SELECT created_at FROM daily_bonus WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        if last:
            last_dt = datetime.fromisoformat(last["created_at"])
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if now_utc() < last_dt + timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS):
                raise HTTPException(429, "Daily Bonus is not available yet")
        bonus_date = now_utc().astimezone(ETHIOPIA_TZ).date().isoformat()
        try:
            conn.execute("INSERT INTO daily_bonus(user_id,bonus_date,amount,created_at) VALUES(?,?,?,?)", (user_id, bonus_date, DAILY_BONUS, iso()))
        except sqlite3.IntegrityError:
            raise HTTPException(429, "Daily Bonus already claimed today")
        new_balance = add_balance(conn, user_id, DAILY_BONUS, "daily_bonus", None)
        notify(conn, user_id, "🎁 Daily Bonus", f"Daily Bonus claimed successfully. You earned {DAILY_BONUS:.2f} ETB.")
        conn.commit()
        return {"success": True, "amount": DAILY_BONUS, "balance": new_balance, "date": bonus_date, "cooldown_hours": 24, "message": f"Daily Bonus claimed! +{DAILY_BONUS:.2f} ETB"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@app.get("/api/wallet")
async def get_wallet(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        row = conn.execute("SELECT cbe_number,telebirr_number FROM users WHERE user_id=?", (user_id,)).fetchone()
        return {"success": True, "cbe_number": row["cbe_number"], "telebirr_number": row["telebirr_number"]}
    finally:
        conn.close()

@app.post("/api/wallet")
async def save_wallet(body: WalletIn):
    conn = db()
    try:
        user_id, tg = auth_user(body.init_data, conn)
        cbe = (body.cbe_number or "").strip()
        tele = (body.telebirr_number or "").strip()
        if cbe:
            if not (cbe.isdigit() and len(cbe) == 13 and cbe.startswith("1000")):
                raise HTTPException(400, "Invalid CBE number")
        if tele:
            if not (tele.isdigit() and len(tele) == 10 and tele.startswith(("09", "07"))):
                raise HTTPException(400, "Invalid Telebirr number")
        if not cbe and not tele:
            raise HTTPException(400, "Enter a wallet number")
        conn.execute("UPDATE users SET cbe_number=COALESCE(NULLIF(?,''),cbe_number), telebirr_number=COALESCE(NULLIF(?,''),telebirr_number),updated_at=? WHERE user_id=?", (cbe, tele, iso(), user_id))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

@app.post("/api/withdraw")
async def withdraw(body: WithdrawIn):
    conn = db()
    try:
        user_id, tg = auth_user(body.init_data, conn)
        conn.commit()
        amount = round(float(body.amount), 2)
        if amount < MIN_WITHDRAWAL:
            raise HTTPException(400, f"Minimum withdrawal is {MIN_WITHDRAWAL:.2f} ETB")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row["suspicious"]:
            raise HTTPException(403, "Account is restricted")
        if not row["joined_all"]:
            raise HTTPException(403, "Please verify all required channels first")
        wallet_type = body.wallet_type
        if wallet_type not in {"cbe", "telebirr"}:
            if row["cbe_number"] and not row["telebirr_number"]:
                wallet_type = "cbe"
            elif row["telebirr_number"] and not row["cbe_number"]:
                wallet_type = "telebirr"
            else:
                raise HTTPException(400, "Select a wallet type")
        wallet_number = row["cbe_number"] if wallet_type == "cbe" else row["telebirr_number"]
        if not wallet_number:
            raise HTTPException(400, "Save this wallet first")
        if float(row["balance"]) < amount:
            raise HTTPException(400, "Insufficient balance")
        new_balance = add_balance(conn, user_id, -amount, "withdrawal", None)
        cur = conn.execute("INSERT INTO withdrawals(user_id,amount,wallet_type,wallet_number,status,created_at) VALUES(?,?,?,?, 'pending',?)", (user_id, amount, wallet_type, wallet_number, iso()))
        notify(conn, user_id, "💸 Withdrawal", f"Your {amount:.2f} ETB withdrawal is pending.")
        conn.commit()
        return {"success": True, "withdrawal_id": cur.lastrowid, "balance": new_balance}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@app.get("/api/withdrawals")
async def withdrawals(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        rows = conn.execute("SELECT * FROM withdrawals WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.get("/api/tasks")
async def tasks(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        rows = conn.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.get("/api/balance-history")
async def balance_history(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        rows = conn.execute("SELECT * FROM balance_history WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,)).fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.get("/api/notifications")
async def notifications(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        rows = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,)).fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.post("/api/notifications/read")
async def notifications_read(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

@app.get("/api/announcements")
async def announcements():
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM announcements WHERE active=1 ORDER BY id DESC").fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.post("/api/tasks/{task_id}/submit")
async def submit_task(task_id: int, init_data: str = Form(...), proof_text: str = Form(default=""), proof: Optional[UploadFile] = File(default=None)):
    conn = db()
    try:
        user_id, tg = auth_user(init_data, conn)
        conn.commit()
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        existing = conn.execute("SELECT status FROM user_tasks WHERE user_id=? AND task_id=?", (user_id, task_id)).fetchone()
        if existing and existing["status"] in {"submitted", "approved"}:
            raise HTTPException(400, "Task already submitted")
        data = None
        filename = None
        content_type = None
        if proof:
            content_type = proof.content_type or ""
            if not content_type.startswith("image/"):
                raise HTTPException(400, "Proof must be an image")
            data = await proof.read(MAX_PROOF_SIZE + 1)
            if len(data) > MAX_PROOF_SIZE:
                raise HTTPException(400, "Proof is too large")
            filename = proof.filename
        conn.execute("INSERT OR REPLACE INTO user_tasks(user_id,task_id,status,proof_text,proof_data,proof_filename,content_type,reward,submitted_at) VALUES(?,?, 'submitted',?,?,?,?,?,?)", (user_id, task_id, proof_text, data, filename, content_type, task["reward"], iso()))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

@app.get("/api/my-task-submissions")
async def my_task_submissions(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        rows = conn.execute("SELECT ut.*,t.title FROM user_tasks ut JOIN tasks t ON t.id=ut.task_id WHERE ut.user_id=? ORDER BY ut.submitted_at DESC", (user_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("proof_data", None)
            out.append(d)
        return {"success": True, "items": out}
    finally:
        conn.close()

@app.get("/api/admin/dashboard")
async def admin_dashboard(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(user_id): raise HTTPException(403, "Admin only")
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        pending_w = conn.execute("SELECT COUNT(*) c FROM withdrawals WHERE status='pending'").fetchone()["c"]
        pending_t = conn.execute("SELECT COUNT(*) c FROM user_tasks WHERE status='submitted'").fetchone()["c"]
        total_balance = conn.execute("SELECT COALESCE(SUM(balance),0) s FROM users").fetchone()["s"]
        return {"success": True, "users": users, "pending_withdrawals": pending_w, "pending_tasks": pending_t, "total_balance": total_balance}
    finally:
        conn.close()

@app.get("/api/admin/withdrawals")
async def admin_withdrawals(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        user_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(user_id): raise HTTPException(403, "Admin only")
        rows = conn.execute("SELECT w.*,u.username,u.first_name FROM withdrawals w LEFT JOIN users u ON u.user_id=w.user_id ORDER BY w.id DESC").fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.post("/api/admin/withdrawals/{withdrawal_id}/approve")
async def approve_withdrawal(withdrawal_id: int, x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        conn.execute("BEGIN IMMEDIATE")
        w = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
        if not w: raise HTTPException(404, "Withdrawal not found")
        if w["status"] != "pending": raise HTTPException(400, "Withdrawal already reviewed")
        conn.execute("UPDATE withdrawals SET status='paid',reviewed_at=? WHERE id=? AND status='pending'", (iso(), withdrawal_id))
        notify(conn, w["user_id"], "✅ Withdrawal Paid", f"Your {w['amount']:.2f} ETB withdrawal has been paid.")
        conn.execute("INSERT INTO admin_logs(admin_id,action,details,created_at) VALUES(?,?,?,?)", (admin_id, "approve_withdrawal", str(withdrawal_id), iso()))
        conn.commit()
        return {"success": True}
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

@app.post("/api/admin/withdrawals/{withdrawal_id}/reject")
async def reject_withdrawal(withdrawal_id: int, reason: str = Form(default="Rejected"), x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        conn.execute("BEGIN IMMEDIATE")
        w = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
        if not w: raise HTTPException(404, "Withdrawal not found")
        if w["status"] != "pending": raise HTTPException(400, "Withdrawal already reviewed")
        new_balance = add_balance(conn, w["user_id"], w["amount"], "withdrawal_refund", str(withdrawal_id))
        conn.execute("UPDATE withdrawals SET status='rejected',reviewed_at=?,rejection_reason=? WHERE id=? AND status='pending'", (iso(), reason, withdrawal_id))
        notify(conn, w["user_id"], "❌ Withdrawal Rejected", f"Your withdrawal was rejected. {reason}. {w['amount']:.2f} ETB returned to your balance.")
        conn.execute("INSERT INTO admin_logs(admin_id,action,details,created_at) VALUES(?,?,?,?)", (admin_id, "reject_withdrawal", json.dumps({"id": withdrawal_id, "reason": reason} ), iso()))
        conn.commit()
        return {"success": True, "balance": new_balance}
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

@app.get("/api/admin/users")
async def admin_users(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        rows = conn.execute("SELECT user_id,username,first_name,last_name,balance,joined_all,suspicious,cbe_number,telebirr_number,registered_at FROM users ORDER BY user_id DESC").fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.get("/api/admin/users/{target_id}")
async def admin_user(target_id: int, x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (target_id,)).fetchone()
        if not row: raise HTTPException(404, "User not found")
        return {"success": True, "user": dict(row)}
    finally:
        conn.close()

@app.post("/api/admin/balance")
async def admin_balance(target_user_id: int = Form(...), amount: float = Form(...), reason: str = Form(default="Admin adjustment"), x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        conn.execute("BEGIN IMMEDIATE")
        new_balance = add_balance(conn, target_user_id, amount, "admin_adjustment", reason)
        notify(conn, target_user_id, "💰 Balance Update", f"Your balance changed by {amount:+.2f} ETB. {reason}")
        conn.execute("INSERT INTO admin_logs(admin_id,action,details,created_at) VALUES(?,?,?,?)", (admin_id, "balance_adjustment", json.dumps({"user_id": target_user_id, "amount": amount, "reason": reason}), iso()))
        conn.commit()
        return {"success": True, "balance": new_balance}
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

@app.get("/api/admin/logs")
async def admin_logs(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        rows = conn.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 200").fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.post("/api/admin/tasks")
async def create_task(title: str = Form(...), description: str = Form(default=""), channel_username: str = Form(default=""), channel_url: str = Form(default=""), reward: float = Form(...), x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        cur = conn.execute("INSERT INTO tasks(title,description,channel_username,channel_url,reward,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (title, description, channel_username, channel_url, reward, iso(), iso()))
        conn.commit()
        return {"success": True, "task_id": cur.lastrowid}
    finally:
        conn.close()

@app.get("/api/admin/tasks")
async def admin_tasks(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return {"success": True, "items": [dict(x) for x in rows]}
    finally:
        conn.close()

@app.get("/api/admin/task-submissions")
async def admin_task_submissions(x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        rows = conn.execute("SELECT ut.*,t.title,u.username,u.first_name FROM user_tasks ut JOIN tasks t ON t.id=ut.task_id JOIN users u ON u.user_id=ut.user_id ORDER BY ut.submitted_at DESC").fetchall()
        out=[]
        for r in rows:
            d=dict(r); d.pop("proof_data",None); out.append(d)
        return {"success": True, "items": out}
    finally:
        conn.close()

@app.get("/api/admin/task-submissions/{user_id}/{task_id}/proof")
async def admin_proof(user_id: int, task_id: int, x_telegram_init_data: str = Header(default="")):
    conn = db()
    try:
        admin_id, tg = auth_user(x_telegram_init_data, conn)
        conn.commit()
        if not is_admin(admin_id): raise HTTPException(403, "Admin only")
        row=conn.execute("SELECT proof_data,proof_filename,content_type FROM user_tasks WHERE user_id=? AND task_id=?",(user_id,task_id)).fetchone()
        if not row or not row["proof_data"]: raise HTTPException(404,"Proof not found")
        return Response(content=row["proof_data"], media_type=row["content_type"] or "application/octet-stream", headers={"Content-Disposition": f'inline; filename="{row["proof_filename"] or "proof"}"'})
    finally:
        conn.close()

@app.post("/api/admin/task-submissions/{user_id}/{task_id}/approve")
async def approve_task(user_id: int, task_id: int, x_telegram_init_data: str = Header(default="")):
    conn=db()
    try:
        admin_id,tg=auth_user(x_telegram_init_data,conn); conn.commit()
        if not is_admin(admin_id): raise HTTPException(403,"Admin only")
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM user_tasks WHERE user_id=? AND task_id=?",(user_id,task_id)).fetchone()
        if not row: raise HTTPException(404,"Submission not found")
        if row["status"]=="approved": raise HTTPException(400,"Already approved")
        task=conn.execute("SELECT reward FROM tasks WHERE id=?",(task_id,)).fetchone()
        reward=float(task["reward"] if task else row["reward"])
        add_balance(conn,user_id,reward,"task_reward",str(task_id))
        conn.execute("UPDATE user_tasks SET status='approved',reward=?,paid=1,reviewed_at=? WHERE user_id=? AND task_id=?",(reward,iso(),user_id,task_id))
        notify(conn,user_id,"✅ Task Approved",f"You earned {reward:.2f} ETB from a task.")
        conn.commit(); return {"success":True}
    except Exception:
        conn.rollback(); raise
    finally: conn.close()

@app.post("/api/admin/task-submissions/{user_id}/{task_id}/reject")
async def reject_task(user_id:int,task_id:int,reason:str=Form(default="Rejected"),x_telegram_init_data:str=Header(default="")):
    conn=db()
    try:
        admin_id,tg=auth_user(x_telegram_init_data,conn); conn.commit()
        if not is_admin(admin_id): raise HTTPException(403,"Admin only")
        row=conn.execute("SELECT status FROM user_tasks WHERE user_id=? AND task_id=?",(user_id,task_id)).fetchone()
        if not row: raise HTTPException(404,"Submission not found")
        conn.execute("UPDATE user_tasks SET status='rejected',rejection_reason=?,reviewed_at=? WHERE user_id=? AND task_id=?",(reason,iso(),user_id,task_id))
        notify(conn,user_id,"❌ Task Rejected",reason)
        conn.commit(); return {"success":True}
    finally: conn.close()

@app.post("/api/admin/announcements")
async def create_announcement(title:str=Form(...),message:str=Form(...),x_telegram_init_data:str=Header(default="")):
    conn=db()
    try:
        admin_id,tg=auth_user(x_telegram_init_data,conn); conn.commit()
        if not is_admin(admin_id): raise HTTPException(403,"Admin only")
        cur=conn.execute("INSERT INTO announcements(title,message,active,created_at) VALUES(?,?,1,?)",(title,message,iso()))
        conn.commit(); return {"success":True,"id":cur.lastrowid}
    finally: conn.close()

@app.post("/api/admin/maintenance")
async def maintenance(enabled:bool=Form(...),x_telegram_init_data:str=Header(default="")):
    conn=db()
    try:
        admin_id,tg=auth_user(x_telegram_init_data,conn); conn.commit()
        if not is_admin(admin_id): raise HTTPException(403,"Admin only")
        conn.execute("INSERT INTO settings(key,value) VALUES('maintenance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",("1" if enabled else "0",))
        conn.commit(); return {"success":True,"maintenance":enabled}
    finally: conn.close()

# Run with: uvicorn server:app --host 0.0.0.0 --port 8000

import os
import json
import time
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# -------------------------------------------------------------
# 1. BOT SETTINGS & CONFIGURATION
# -------------------------------------------------------------
# Render ላይ Environment Variable "BOT_TOKEN" ብለህ ካስገባህ ይወስደዋል፤
# ወይም ቀጥታ 'YOUR_BOT_TOKEN_HERE' በሚለው ቦታ የቦትህ Token ተካው።
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
bot = telebot.TeleBot(BOT_TOKEN)

# መረጋገጥ ያለባቸው 6ቱ ቻናሎች
CHANNELS = [
    "@EthioMakeMoneyy1",
    "@DailyMoney444",
    "@SmartMoneyyLab",
    "@WorldCryptoMiner07",
    "@DailyIncomeHub55",
    "@Paymentprooff2"
]

# የትራንስፖርት ቦነስ እና የሪፈራል መጠን (እንደፍላጎትህ ቀይረው)
REFERRAL_BONUS = 5.0  # ለአንድ ሰው ጥሪ የሚከፈል
DAILY_BONUS = 1.0     # የየቀኑ ቦነስ

DB_FILE = "database.json"

# -------------------------------------------------------------
# 2. SIMPLE DATABASE HANDLING (JSON)
# -------------------------------------------------------------
def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4, ensure_ascii=False)

def get_user_data(user_id):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        db[uid] = {
            "balance": 0.0,
            "referrer": None,
            "referral_bonus_given": False,
            "last_bonus_time": 0
        }
        save_db(db)
    return db[uid]

def update_user_data(user_id, key, value):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        get_user_data(user_id)
        db = load_db()
    db[uid][key] = value
    save_db(db)

# -------------------------------------------------------------
# 3. CHANNEL MEMBERSHIP CHECKER
# -------------------------------------------------------------
def get_unjoined_channels(user_id):
    """የተጠቃሚውን አባልነት አጣርቶ ያልተቀላቀላቸውን ቻናሎች ብቻ ይመልሳል"""
    unjoined = []
    for channel in CHANNELS:
        try:
            member = bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ['left', 'kicked']:
                unjoined.append(channel)
        except Exception:
            # ቦቱ አድሚን ካልሆነ ወይም ኤረር ካለ ቻናሉን እንዳልተቀላቀለ ይቆጥራል
            unjoined.append(channel)
    return unjoined

# -------------------------------------------------------------
# 4. COMMAND HANDLERS
# -------------------------------------------------------------
@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id
    user_data = get_user_data(user_id)

    # Referral link መያዝ
    args = message.text.split()
    if len(args) > 1:
        referrer_id = args[1]
        if referrer_id != str(user_id) and user_data["referrer"] is None:
            update_user_data(user_id, "referrer", referrer_id)

    unjoined = get_unjoined_channels(user_id)
    
    if not unjoined:
        send_main_menu(message.chat.id, "🎉 **እንኳን ደስ አለዎት!** ሁሉንም ቻናሎች ተቀላቅለዋል። ቦቱን መጠቀም ይችላሉ።")
    else:
        send_join_keyboard(message.chat.id, unjoined)

def send_join_keyboard(chat_id, unjoined_list):
    markup = InlineKeyboardMarkup()
    for ch in unjoined_list:
        url = f"https://t.me/{ch.replace('@', '')}"
        markup.add(InlineKeyboardButton(text=f"📢 Join {ch}", url=url))
    
    markup.add(InlineKeyboardButton(text="✅ Check / Verify", callback_data="verify_membership"))
    
    bot.send_message(
        chat_id,
        "👋 **እንኳን ደህና መጡ!**\n\nቦቱን መጠቀም ለመጀመር እባክዎን ከታች ያሉትን ቻናሎች ይቀላቀሉ እና **Check / Verify** የሚለውን ይጫኑ፡",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "verify_membership")
def verify_callback(call):
    user_id = call.from_user.id
    unjoined = get_unjoined_channels(user_id)

    if not unjoined:
        # 1. ሪፈራል መስራቱን ማረጋገጥና ቦነስ መስጠት
        user_data = get_user_data(user_id)
        if user_data["referrer"] and not user_data["referral_bonus_given"]:
            ref_id = user_data["referrer"]
            ref_data = get_user_data(ref_id)
            new_bal = ref_data.get("balance", 0.0) + REFERRAL_BONUS
            update_user_data(ref_id, "balance", new_bal)
            update_user_data(user_id, "referral_bonus_given", True)
            
            try:
                bot.send_message(ref_id, f"🎉 **አዲስ አባል በሪፈራል ሊንክህ ተቀላቅሏል!**\n\n💰 +{REFERRAL_BONUS} ETB ወደ ባላንስህ ተጨምሯል።")
            except Exception:
                pass

        bot.answer_callback_query(call.id, "✅ አባልነትዎ ተረጋግጧል!", show_alert=True)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        send_main_menu(call.message.chat.id, "🎉 **እንኳን ደስ አለዎት!** አሁን ቦቱን መጠቀም ይችላሉ።")
    else:
        bot.answer_callback_query(call.id, "❌ አሁንም ያልተቀላቀሏቸው ቻናሎች አሉ!", show_alert=True)
        # ያልተቀላቀላቸውን ብቻ አጣርቶ ማሳየት
        send_join_keyboard(call.message.chat.id, unjoined)

# -------------------------------------------------------------
# 5. MAIN MENU FUNCTIONS
# -------------------------------------------------------------
def send_main_menu(chat_id, text):
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        KeyboardButton("💰 Balance"),
        KeyboardButton("🎁 Daily Bonus"),
        KeyboardButton("🔗 Referral Link"),
        KeyboardButton("🏧 Withdraw")
    )
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "💰 Balance")
def balance_cmd(message):
    user_data = get_user_data(message.from_user.id)
    bal = user_data.get("balance", 0.0)
    bot.send_message(message.chat.id, f"💵 **የእርስዎ የአሁኑ ባላንስ:** {bal:.2f} ETB", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🎁 Daily Bonus")
def bonus_cmd(message):
    user_id = message.from_user.id
    user_data = get_user_data(user_id)
    last_bonus = user_data.get("last_bonus_time", 0)
    now = time.time()

    # 24 ሰዓት (86400 ሴኮንድ) መሙላቱን መፈተሽ
    if now - last_bonus < 86400:
        remaining_sec = 86400 - (now - last_bonus)
        hours = int(remaining_sec // 3600)
        minutes = int((remaining_sec % 3600) // 60)
        bot.send_message(message.chat.id, f"⏳ **የዛሬውን ቦነስ ወስደዋል።**\n\nእባክዎን ከ **{hours} ሰዓት እና {minutes} ደቂቃ** በኋላ ተመልሰው ይሞክሩ።", parse_mode="Markdown")
    else:
        new_bal = user_data.get("balance", 0.0) + DAILY_BONUS
        update_user_data(user_id, "balance", new_bal)
        update_user_data(user_id, "last_bonus_time", now)
        bot.send_message(message.chat.id, f"🎁 **እንኳን ደስ አለዎት!**\n\nየዛሬውን የ **{DAILY_BONUS:.2f} ETB** ቦነስ ወስደዋል።", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🔗 Referral Link")
def referral_cmd(message):
    bot_username = bot.get_me().username
    ref_link = f"https://t.me/{bot_username}?start={message.from_user.id}"
    bot.send_message(
        message.chat.id,
        f"🔗 **የእርስዎ የጥሪ (Referral) ሊንክ:**\n\n`{ref_link}`\n\n👥 ሰዎችን በመጋበዝ ለአንድ ሰው **{REFERRAL_BONUS} ETB** ያግኙ!",
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda m: m.text == "🏧 Withdraw")
def withdraw_cmd(message):
    user_data = get_user_data(message.from_user.id)
    bal = user_data.get("balance", 0.0)
    
    if bal < 50.0: # ዝቅተኛው የማውጫ መጠን (እንደፍላጎትህ ቀይረው)
        bot.send_message(message.chat.id, f"⚠️ **ገንዘብ ለማውጣት በትንሹ 50 ETB ያስፈልጋል።**\n\nየእርስዎ ባላንስ: {bal:.2f} ETB", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "🏧 እባክዎን የመውጫ መንገድዎን እና የስልክ/ሒሳብ ቁጥርዎን ይላኩ፡", parse_mode="Markdown")

# -------------------------------------------------------------
# 6. START BOT
# -------------------------------------------------------------
if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling(skip_pending=True)

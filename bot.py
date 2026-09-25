import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEB_APP_URL = os.getenv("WEB_APP_URL", "").strip()
BOT_USERNAME = "FalconWorld_Bot"

CHANNELS = [
    ("@Sheger_tech1", "https://t.me/Sheger_tech1"),
    ("@EthioVortex1", "https://t.me/EthioVortex1"),
    ("@ethiocashflow", "https://t.me/ethiocashflow"),
    ("@AmanIncomeLab", "https://t.me/AmanIncomeLab"),
    ("@OnlineIncomeHub07", "https://t.me/OnlineIncomeHub07"),
    ("@Paymentprooff2", "https://t.me/Paymentprooff2"),
]
BATCH_SIZE = 3


def batches():
    return [CHANNELS[i:i+BATCH_SIZE] for i in range(0, len(CHANNELS), BATCH_SIZE)]


async def is_member(bot, chat, user_id):
    try:
        m = await bot.get_chat_member(chat_id=chat, user_id=user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def missing_in_batch(bot, user_id, batch):
    missing = []
    for username, url in batch:
        if not await is_member(bot, username, user_id):
            missing.append((username, url))
    return missing


def join_keyboard(items, verify_data="verify_1"):
    rows = [[InlineKeyboardButton(f"📢 Join {name}", url=url)] for name, url in items]
    rows.append([InlineKeyboardButton("✅ Verify", callback_data=verify_data)])
    return InlineKeyboardMarkup(rows)


def app_keyboard():
    if not WEB_APP_URL:
        return InlineKeyboardMarkup([])
    return InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Open Falcon World", web_app=WebAppInfo(url=WEB_APP_URL))]])


async def show_batch(update, context, batch_index):
    user_id = update.effective_user.id
    bs = batches()
    if batch_index >= len(bs):
        text = "🎉 <b>All channels verified!</b>\n\nYou can now open Falcon World Mini App."
        markup = app_keyboard()
    else:
        missing = await missing_in_batch(context.bot, user_id, bs[batch_index])
        if not missing:
            await show_batch(update, context, batch_index + 1)
            return
        text = (
            "🦅 <b>Welcome to Falcon World</b>\n\n"
            f"🔐 <b>Step {batch_index + 1}/{len(bs)}</b>\n"
            "Join the channels below, then press <b>Verify</b>.\n\n"
            "ከታች ያሉትን ቻናሎች Join ካደረጉ በኋላ Verify ይጫኑ።"
        )
        markup = join_keyboard(missing, f"verify_{batch_index + 1}")

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_batch(update, context, 0)


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Checking...")
    try:
        batch_index = max(0, int(query.data.split("_")[-1]) - 1)
    except Exception:
        batch_index = 0

    bs = batches()
    if batch_index >= len(bs):
        await show_batch(update, context, len(bs))
        return

    missing = await missing_in_batch(context.bot, update.effective_user.id, bs[batch_index])
    if missing:
        await query.edit_message_text(
            "❌ <b>Not completed</b>\n\nPlease join the channels you have not joined yet, then press Verify again.",
            reply_markup=join_keyboard(missing, f"verify_{batch_index + 1}"),
            parse_mode="HTML",
        )
        return

    if batch_index + 1 < len(bs):
        await query.edit_message_text(
            "✅ <b>Verified!</b>\n\nThe next 3 required channels are now shown.",
            reply_markup=join_keyboard(bs[batch_index + 1], f"verify_{batch_index + 2}"),
            parse_mode="HTML",
        )
    else:
        await query.edit_message_text(
            "🎉 <b>All 6 channels verified!</b>\n\nYour Falcon World account is ready.",
            reply_markup=app_keyboard(),
            parse_mode="HTML",
        )


async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(verify, pattern=r"^verify_\d+$"))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    import asyncio
    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

import os
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("falcon_world")


# =========================================================
# FALCON WORLD REQUIRED CHANNELS
# =========================================================

REQUIRED_CHANNELS = [
    {
        "username": "@Sheger_tech1",
        "url": "https://t.me/Sheger_tech1",
        "name": "Sheger Tech",
    },
    {
        "username": "@EthioVortex1",
        "url": "https://t.me/EthioVortex1",
        "name": "Ethio Vortex",
    },
    {
        "username": "@ethiocashflow",
        "url": "https://t.me/ethiocashflow",
        "name": "Ethio Cash Flow",
    },
    {
        "username": "@AmanIncomeLab",
        "url": "https://t.me/AmanIncomeLab",
        "name": "Aman Income Lab",
    },
    {
        "username": "@OnlineIncomeHub07",
        "url": "https://t.me/OnlineIncomeHub07",
        "name": "Online Income Hub",
    },
    {
        "username": "@Paymentprooff2",
        "url": "https://t.me/Paymentprooff2",
        "name": "Payment Proof",
    },
]


# =========================================================
# CHECK WHETHER USER JOINED A CHANNEL
# =========================================================

async def is_user_joined(bot, user_id, channel_username):
    try:
        member = await bot.get_chat_member(
            chat_id=channel_username,
            user_id=user_id,
        )

        return member.status in (
            "creator",
            "administrator",
            "member",
        )

    except Exception as error:
        logger.error(
            "Could not check %s for user %s: %s",
            channel_username,
            user_id,
            error,
        )
        return False


# =========================================================
# GET ALL CHANNELS USER HAS NOT JOINED
# =========================================================

async def get_unjoined_channels(bot, user_id):
    unjoined = []

    for channel in REQUIRED_CHANNELS:
        joined = await is_user_joined(
            bot,
            user_id,
            channel["username"],
        )

        if not joined:
            unjoined.append(channel)

    return unjoined


# =========================================================
# BUILD JOIN BUTTONS
# =========================================================

def build_channel_keyboard(channels):
    keyboard = []

    for channel in channels:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📢 {channel['name']}",
                    url=channel["url"],
                ),
                InlineKeyboardButton(
                    "JOIN",
                    url=channel["url"],
                ),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "✅ VERIFY",
                callback_data="verify_channels",
            )
        ]
    )

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# SHOW CHANNEL VERIFICATION
# =========================================================

async def show_verification(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    unjoined = await get_unjoined_channels(
        context.bot,
        user_id,
    )

    # -----------------------------------------------------
    # ALL CHANNELS JOINED
    # -----------------------------------------------------

    if not unjoined:
        text = (
            "🎉 <b>Verification Complete!</b>\n\n"
            "✅ All required channels are joined.\n\n"
            "🦅 <b>Welcome to Falcon World!</b>"
        )

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                text,
                parse_mode="HTML",
            )

        return

    # -----------------------------------------------------
    # SHOW ONLY CHANNELS NOT JOINED
    # -----------------------------------------------------

    text = (
        "🦅 <b>FALCON WORLD</b>\n\n"
        "Please join the channels below to continue.\n\n"
        f"📌 <b>{len(unjoined)}</b> channel(s) remaining."
    )

    keyboard = build_channel_keyboard(unjoined)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await show_verification(
        update,
        context,
    )


# =========================================================
# VERIFY BUTTON
# =========================================================

async def verify_channels(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer(
        "Checking your channel membership..."
    )

    user_id = query.from_user.id

    unjoined = await get_unjoined_channels(
        context.bot,
        user_id,
    )

    # -----------------------------------------------------
    # EVERYTHING IS JOINED
    # -----------------------------------------------------

    if not unjoined:
        await query.edit_message_text(
            "🎉 <b>Verification Complete!</b>\n\n"
            "✅ All 6 channels are verified.\n\n"
            "🦅 <b>Welcome to Falcon World!</b>",
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # SOME CHANNELS ARE STILL MISSING
    # -----------------------------------------------------

    text = (
        "⚠️ <b>Verification Result</b>\n\n"
        "You still need to join these channels:\n\n"
    )

    for channel in unjoined:
        text += (
            f"❌ {channel['username']}\n"
        )

    text += (
        "\n👇 Join the remaining channels, "
        "then press VERIFY again."
    )

    keyboard = build_channel_keyboard(unjoined)

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Unhandled exception:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. "
            "Add BOT_TOKEN to Render Environment Variables."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            verify_channels,
            pattern=r"^verify_channels$",
        )
    )

    app.add_error_handler(
        error_handler
    )

    logger.info(
        "Falcon World bot is starting..."
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

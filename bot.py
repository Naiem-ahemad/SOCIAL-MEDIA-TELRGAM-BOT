# bot.py
import os , re 
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup , LabeledPrice
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    InlineQueryHandler,
    PreCheckoutQueryHandler,)
from core.utils import run_in_background
from services.spotify import SPOTIFY_DOMAIN
from core.utils import logger , db
from services import ( 
    TWITTER_HANDLER,
    INSTAGRAM_HANDLER,
    YOUTUBE_HANDLER,
    SPOTIFY_HANDLER,
    FACEBOOK_HANDLER,
    LINKEDIN_HANDLER,
    PINTEREST_HANDLER,
    GENERIC_HANDLER,
    inline_search,
    inline_query_pin)

from admin.admin import admin_menu , admin_callback , handle_broadcast , ai_callback
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY")

# -------------------- START COMMAND --------------------
@run_in_background
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    print("Chat ID:", chat.id)
    print("Chat Type:", chat.type)
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = update.effective_user.id
    username = update.effective_user.username

    display_name = user.first_name or "User"
    if user.last_name:
        display_name += f" {user.last_name}"

    db.add_user(user_id , username , display_name)

    if user.username:
        display_name += f" (@{user.username})"

    # Big friendly welcome message with quick actions
    text = (

        f"🌟 <b>Welcome, {display_name}!</b> \n\n"
        "<b>Social Media Downloader</b> — your quick and privacy-minded helper for saving media from the web.\n\n"
        "What you can do:\n"
        "• Download or stream videos & photos from Instagram, X (Twitter), Facebook, Pinterest and more.\n"
        "• Get fast direct download links, audio extraction, and friendly buttons.\n"
        "• Works in groups and private chats.\n\n"
        "Before continuing, please review and accept our Terms & Conditions so we can safely process URLs for you."
    )

    # Action buttons: Terms, Help, Quick Examples (switch to inline)
    btns = [
        [
            InlineKeyboardButton("📜 Terms & Conditions", callback_data="open_terms"),
            InlineKeyboardButton("❓ Help", callback_data="open_help"),
        ],
        [InlineKeyboardButton("🔁 Share Bot (inline)", switch_inline_query="")]
    ]

    keyboard = InlineKeyboardMarkup(btns)

    await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard
    )


# -------------------- TERMS HANDLER --------------------
@run_in_background
async def terms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Terms & Conditions related callbacks.

    Callback flow:
    - open_terms: show full terms with Accept/Decline
    - terms_yes: record acceptance and show quick start
    - terms_no: show decline message
    - open_help: show help panel (from start button)
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "open_terms":
        terms_text = (
            "📜 <b>Terms & Conditions</b>\n\n"
            "Please read these terms carefully before using the bot. By clicking <b>Accept</b> you consent to these terms.\n\n"
            "<b>1) Purpose</b>\n"
            "This bot helps you fetch media (videos, photos, audio) from public web URLs for personal use.\n\n"
            "<b>2) Allowed Use</b>\n"
            "You may only use the bot to download content that you have the right to access or that is publicly available.\n\n"
            "<b>3) Copyright & DMCA</b>\n"
            "You are responsible for complying with copyright and third-party rights. If you believe content violates copyright, contact the content owner or file a takedown request — this bot does not host content permanently.\n\n"
            "<b>4) Privacy</b>\n"
            "The bot processes URLs you send to generate download links. We do not sell personal data. Minimal metadata (user id and accepted terms flag) may be stored locally for UX.\n\n"
            "<b>5) Liability</b>\n"
            "The bot is provided as-is. The maintainers are not liable for how you use the downloaded content.\n\n"
            "<b>6) Updates</b>\n"
            "These terms may be updated; continued use implies acceptance of the updated terms.\n\n"
            "Do you accept these Terms & Conditions?"
        )

        btns = [
            [
                InlineKeyboardButton("✅ Accept & Continue", callback_data="terms_yes"),
                InlineKeyboardButton("❌ Decline", callback_data="terms_no"),
            ]
        ]
        keyboard = InlineKeyboardMarkup(btns)

        await query.edit_message_text(terms_text, parse_mode="HTML", reply_markup=keyboard)

    elif data == "terms_yes":
        # Mark acceptance in user_data so other handlers can check it
        try:
            context.user_data["accepted_terms"] = True
        except Exception:
            pass

        await query.edit_message_text(
            "✅ Thank you — you accepted the Terms.\n\n" 
            "You can now send a link (Instagram, X, Facebook, YouTube, Pinterest, Spotify) and I will help you download or stream media.\n\n"
            "Tip: Use the inline Share button from the main menu to quickly post this bot in other chats."
        )

    elif data == "terms_no":
        await query.edit_message_text(
            "❌ You declined the Terms & Conditions.\n\n" 
            "You won't be able to use this bot until you accept. If you changed your mind, press /start to view the terms again."
        )

    elif data == "open_help":
        
        await help_command(update, context)

# -------------------- HELP HANDLER --------------------
@run_in_background
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a friendly, feature-rich help message to the user.

    Uses Telegram formatting and inline buttons for quick actions.
    """
    chat_id = update.effective_chat.id
    help_text = (
        "💡 <b>How to use this bot</b>\n\n"
        "Send a single public URL (Instagram, X/Twitter, Facebook, YouTube, Pinterest, Spotify) and the bot will analyze it and return downloadable media when possible.\n\n"
        "<b>Quick Tips</b>\n"
        "• Use the original platform's " "Share → Copy Link" " to send links.\n"
        "• For long videos, prefer audio-only extraction (Spotify/YouTube) to save bandwidth.\n"
        "• If an extraction fails, try opening the link in the app or a browser and re-share the link.\n\n"
        "<b>Privacy & Safety</b>\n"
        "The bot stores minimal metadata for UX. Do not send sensitive private links.\n\n"
        "<b>Need more help?</b>\n"
        "Use the buttons below to open the Terms, contact support, or get the bot's source."
    )

    btns = [
        [InlineKeyboardButton("📜 Terms", callback_data="open_terms"), InlineKeyboardButton("📞 Support", url="https://t.me/zer0_spectrum")],
        [InlineKeyboardButton("🔗 Source", url="https://github.com/Naiem-ahemad/SOCIAL-MEDIA-TELRGAM-BOT"), InlineKeyboardButton("⚙️ Settings", callback_data="open_settings")],
    ]

    keyboard = InlineKeyboardMarkup(btns)

    # If called from a callback query, edit message; otherwise send a new message
    if update.callback_query:
        await update.callback_query.edit_message_text(help_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=chat_id, text=help_text, parse_mode="HTML", reply_markup=keyboard)


async def supported_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
             🌍 <b>Supported Platforms</b>

    🎬 <b>YouTube</b> — Videos, Shorts, and Audio\n
    📸 <b>Instagram</b> — Reels, Posts, and Stories\n
    📘 <b>Facebook</b> — Videos & Reels\n
    🐦 <b>Twitter / X</b> — Tweets with media\n
    💬 <b>Linkedin</b> — Posts & videos\n
    🎧 <b>Spotify</b> — Tracks and playlists\n
    📌 <b>Pinterest</b> — Image & video pins\n
    
    All downloads include automatic thumbnail detection, \nsmart captioning, and multiple quality options for stream or download.\n\n
    We plan to add more platforms soon!
    """

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 View Source Code", url="https://github.com/Naiem-ahemad/SOCIAL-MEDIA-TELRGAM-BOT")]
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )

async def buy(update, context):
    title = "Premium Access"
    description = "Get access to premium bot features"
    payload = "stars-payment"
    currency = "XTR"  # Required for Telegram Stars
    prices = [LabeledPrice("Premium Plan", 100)]  # 100 Stars

    await update.message.reply_invoice(
        title=title,
        description=description,
        payload=payload,
        provider_token="",  # leave empty for Stars
        currency=currency,
        prices=prices,
        start_parameter="test-stars",
    )

async def precheckout(update, context):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update, context):
    amount = update.message.successful_payment.total_amount
    await update.message.reply_text(f"✅ Payment successful! You paid {amount} ⭐")


def main():
    # --- Webhook / Bot Setup ---
    # WEBHOOK_URL = "https://bot.zer0spectrum.dpdns.org"
    # PORT = 8002
    app = Application.builder().token(TOKEN).build()

    # --- Filters ---
    INSTAGRAM_FILTER = filters.TEXT & filters.Regex(r"(instagram\.com|instagr\.am)")
    TWITTER_FILTER   = filters.TEXT & filters.Regex(r"(twitter\.com|x\.com)")
    FACEBOOK_FILTER  = filters.TEXT & filters.Regex(r"(facebook\.com|fb\.watch)")
    PINTEREST_FILTER = filters.TEXT & filters.Regex(r"(pinterest\.com|pin\.it)")
    LINKDIN_FILTER   = filters.TEXT & filters.Regex(r"(linkedin\.com)")
    SPOTIFY_FILTER   = filters.TEXT & filters.Regex(
        r"https?://(?:www\.)?(?:{})/.*".format("|".join(map(re.escape, SPOTIFY_DOMAIN)))
    )
    YOUTUBE_FILTER   = filters.TEXT & filters.Regex(r"(youtube\.com|youtu\.be)")
    GENRIC_FILTER    = filters.TEXT & filters.Regex(r"https?://")

    # --- Command Handlers ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("terms", start))
    app.add_handler(CommandHandler("supported", supported_urls))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(PreCheckoutQueryHandler(precheckout))

    # --- Callback Query Handlers (Specific → Generic) ---
    app.add_handler(CallbackQueryHandler(ai_callback, pattern="^(ai_edit|ai_send|ai_send_original)$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|ban_|user_|toggleban_)"))
    app.add_handler(CallbackQueryHandler(terms_handler, pattern="^(open_terms|terms_yes|terms_no)$"))
    app.add_handler(CallbackQueryHandler(YOUTUBE_HANDLER.button_handler))  # generic button handler last

    # --- Message Handlers (Most specific → Generic) ---
    app.add_handler(MessageHandler(INSTAGRAM_FILTER, INSTAGRAM_HANDLER.handle_instagram_url))
    app.add_handler(MessageHandler(TWITTER_FILTER, TWITTER_HANDLER.handle_twitter_url))
    app.add_handler(MessageHandler(FACEBOOK_FILTER, FACEBOOK_HANDLER.handle_facebook_url))
    app.add_handler(MessageHandler(PINTEREST_FILTER, PINTEREST_HANDLER.handle_pinterest_url))
    app.add_handler(MessageHandler(LINKDIN_FILTER, LINKEDIN_HANDLER.handle_linkdin_url))
    app.add_handler(MessageHandler(SPOTIFY_FILTER, SPOTIFY_HANDLER.handle_spotify_url))
    app.add_handler(MessageHandler(YOUTUBE_FILTER, YOUTUBE_HANDLER.handle_youtube_url))
    app.add_handler(MessageHandler(GENRIC_FILTER, GENERIC_HANDLER.handle_generic_url))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast))
    app.add_handler(InlineQueryHandler(inline_search, pattern=r"^(?!pin ).*"))
    app.add_handler(InlineQueryHandler(inline_query_pin, pattern=r"^pin "))
    
    # --- Broadcast Texts (must be last to not block URL handlers) ---

    logger.info("BOT STARTED")
    app.run_polling()

    # print(f"Bot started with webhook on port {PORT}...")
    # app.run_webhook(
    #     listen="0.0.0.0",         # listen on all interfaces
    #     port=PORT,                # your internal port (Caddy reverse proxies here)
    #     url_path=TOKEN,       # webhook path
    #     webhook_url=f"{WEBHOOK_URL}/{TOKEN}"  # Telegram webhook URL
    # )

if __name__ == "__main__":
    main()

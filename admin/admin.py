import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from core.utils import db  
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
ADMIN_IDS = [7840020962] 

async def is_admin(user_id: int):
    return user_id in ADMIN_IDS


# ------------------ MAIN ADMIN MENU ------------------ #
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
    user = update.effective_user
    user_id = user.id

    if not await is_admin(user_id):
        text = "🚫 You are not authorized to access the admin panel."
        if from_callback:
            return await update.callback_query.message.edit_text(text)
        return await update.message.reply_text(text)

    full_name = user.full_name
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            InlineKeyboardButton("🎞 Media", callback_data="admin_media"),
        ],
    ]

    text = (
        f"👋 <b>Welcome, {full_name}!</b>\n"
        f"You're in the <b>Admin Control Panel</b>.\n\n"
        f"Use the options below to manage the bot:"
    )
    markup = InlineKeyboardMarkup(keyboard)

    if from_callback:
        await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)

# ------------------ STATS ------------------ #
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = db.get_total_users()
    total_downloads = db.get_total_downloads()
    top_users = db.get_top_users()

    top_str = "\n".join([f"• {u[0] or 'N/A'} — {u[1]} downloads" for u in top_users]) or "No data"

    text = (
        f"<b>📊 Stats</b>\n\n"
        f"👥 Total Users: <b>{total_users}</b>\n\n"
        f"⬇️ Total Downloads: <b>{total_downloads}</b>\n\n"
        f"🏆 <b>Top Users:</b>\n\n{top_str}"
    )

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
    await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ------------------ USERS ------------------ #
async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_users()[:10]
    text = "<b>👥 Latest Users</b>\n\n"
    for u in users:
        text += f"• {u['first_name']} ({u['username'] or 'N/A'}) — {u['plan']} plan\n"

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
    await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ------------------ MEDIA ------------------ #
async def admin_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    media = db.get_all_media()[:10]
    text = "<b>🎞 Latest Media</b>\n\n"
    for m in media:
        text += f"• {m['platform']} — {m['title'] or 'Untitled'}\n\n"

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
    await update.callback_query.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_broadcast(update, context):
    query = update.callback_query
    await query.message.edit_text("✏️ Send the broadcast message:")
    context.user_data["awaiting_broadcast"] = True
    context.user_data["temp_msgs"] = [query.message.message_id]


async def handle_broadcast(update, context):
    if not context.user_data.get("awaiting_broadcast"):
        return

    msg = update.message.text
    context.user_data["original_text"] = msg
    context.user_data["awaiting_broadcast"] = False
    context.user_data["temp_msgs"].append(update.message.message_id)

    # Buttons
    keyboard = [
        [
            InlineKeyboardButton("✨ Edit with AI", callback_data="ai_edit"),
            InlineKeyboardButton("✅ Send Now", callback_data="ai_send_original")
        ]
    ]
    sent = await update.message.reply_text(
        f"📝 <b>Preview:</b>\n\n{msg}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["temp_msgs"].append(sent.message_id)
async def ai_edit_broadcast(update, context):
    query = update.callback_query
    await query.answer("🧠 Rewriting with AI...")
    original_text = context.user_data.get("original_text")

    prompt = f"""
    You are an expert copywriter and social media communication strategist. 
    Your task is to **rewrite and enhance** the following Telegram broadcast message.

    🎯 Goals:
    - Make the message sound **natural, human, and emotionally engaging**.
    - Maintain the **original intent** and tone (don’t change the meaning).
    - Improve **grammar, flow, and readability**.
    - Keep it **short, clear, and suitable for Telegram users**.
    - Use light emojis (if fitting) to add warmth and personality.
    - Avoid over-promotion, slang, or robotic tone.
    - Don’t add extra hashtags, links, or signatures unless clearly necessary.
    - Make sure it works **as a single concise Telegram message**.

    🧠 When rewriting:
    - Prioritize clarity and friendly tone.
    - Keep a balance between professionalism and approachability.
    - Do not include any intro like “Here’s your rewritten text” or “Output:”.
    - Output only the improved message text — nothing else.
    - Optimize structure for easy reading on mobile screens.
    - If the message feels too short or dull, slightly enrich it for engagement.

    Now rewrite the following message based on these rules:

    --- Original Message ---
    {original_text}
    --- End ---
    """


    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)
    improved = response.text.strip()

    context.user_data["ai_text"] = improved
    keyboard = [
        [
            InlineKeyboardButton("✅ Send", callback_data="ai_send"),
            InlineKeyboardButton("♻️ Re-edit", callback_data="ai_edit")
        ]
    ]
    msg = await query.message.edit_text(
        f"🤖 <b>AI-Edited Version:</b>\n\n{improved}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["temp_msgs"].append(msg.message_id)


# ------------------ SEND FINAL ------------------ #
async def ai_send_broadcast(update, context, use_ai=False):
    query = update.callback_query
    await query.answer("📤 Sending broadcast...")

    msg_text = context.user_data["ai_text"] if use_ai else context.user_data["original_text"]
    users = db.get_all_users()
    sent, failed = 0, 0

    for u in users:
        try:
            await context.bot.send_message(chat_id=u["id"], text=msg_text)
            sent += 1
        except:
            failed += 1

    final_msg = await query.message.reply_text(
        f"✅ Broadcast sent to <b>{sent}</b> users\n",
        parse_mode="HTML"
    )

    # 🧹 Delete all temp messages for clean chat
    for mid in set(context.user_data.get("temp_msgs", [])):
        try:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=mid)
        except:
            pass
    context.user_data.clear()

async def ai_callback(update, context):
    query = update.callback_query
    data = query.data

    if data == "ai_edit":
        return await ai_edit_broadcast(update, context)
    elif data == "ai_send":
        return await ai_send_broadcast(update, context, use_ai=True)
    elif data == "ai_send_original":
        return await ai_send_broadcast(update, context, use_ai=False)
    
# ------------------ CALLBACK ROUTER ------------------ #
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # ✅ Prevents "Invalid selection"
    data = query.data

    if data == "admin_stats":
        return await admin_stats(update, context)
    elif data == "admin_users":
        return await admin_users(update, context)
    elif data == "admin_media":
        return await admin_media(update, context)
    elif data == "admin_broadcast":
        return await admin_broadcast(update, context)
    elif data == "admin_back":
        return await admin_menu(update, context, from_callback=True)

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
import asyncio
from core.utils import EXTRACTER, run_in_background, logger, db
from core.rate_limiter import check_and_record_user_activity
from services.facebook import get_content_length
from core.uploader_from_user import upload_video

# Platforms with custom extractors — we skip these here
CUSTOM_DOMAINS = ("instagram.com", "instagr.am" , "x.com", "twitter.com", "pinterest.com", "pin.it" , "youtube.com" , "youtu.be" , "facebook.com" , "spotify.link", "open.spotify.com" , "spotify.com" , "fb.watch")

def is_generic_url(url: str) -> bool:
    return not any(domain in url for domain in CUSTOM_DOMAINS)

class GENERIC_HANDLER:

    @staticmethod
    @run_in_background
    async def handle_generic_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        
        url = update.message.text.strip()

        if not is_generic_url(url) or "https://" not in url or "t.me" in url or "telegram.me" in url:
            return

        print(url)
        user = update.effective_user
        user_id = user.id
        username = user.username
        first_name = user.first_name

        # Save user
        try:
            await asyncio.to_thread(db.add_user, user_id, username, first_name)
        except Exception:
            logger.warning("db.add_user failed", platform="generic")

        # Check rate-limit
        allowed, reason = await check_and_record_user_activity(user_id)
        if not allowed:
            await update.message.reply_text(f"⛔ You are temporarily banned: {reason}")
            return

        # Cache lookup
        try:
            cached = await asyncio.to_thread(db.get_media_by_url, url)
        except Exception:
            cached = None

        if cached and cached.get("file_id"):
            caption = cached.get("metadata", {}).get("caption") or cached.get("title") or ""
            try:
                await update.message.reply_video(video=cached["file_id"], caption=caption , parse_mode="HTML")
            except Exception:
                await update.message.reply_photo(photo=cached["file_id"], caption=caption , parse_mode="HTML")
            return

        status_msg = await update.message.reply_text("⚙️")
        chat_id, msg_id = status_msg.chat.id, status_msg.message_id

        try:
            data = await asyncio.to_thread(EXTRACTER.Yt_dlp_extract, url)
            print("DATA : " , data)
            if not data:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="⚠️ No media found.")
                return

            entries = data.get("entries") or [data]
            first = entries[0]

            title = first.get("title") or "Media"
            caption = f"<b>{title}</b>"
            thumbnail = first.get("thumbnail")
            ext = first.get("ext")
            formats = first.get("formats") or []

            # 🎯 Pick preferred format (720p else fallback)
            preferred = None
            for f in formats:
                height = f.get("height")
                if height and height == 720:
                    preferred = f
                    break
            if not preferred:
                # fallback to highest
                preferred = max(formats, key=lambda x: x.get("height") or 0)

            raw_media_url = preferred.get("url")
            ext = preferred.get("ext") or "mp4"
            size = await get_content_length(raw_media_url)
            
            if size < 50 * 1024 * 1024:
                media_url = EXTRACTER.download_video_m3u8(raw_media_url)
            else:
                media_url = await upload_video(raw_media_url)

            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            
            # Decide video or image
            if ext in ("mp4", "webm", "mov", "mkv"):
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                msg = await update.message.reply_video(video=media_url, caption=caption, parse_mode="HTML")
            else:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                msg = await update.message.reply_photo(photo=thumbnail or media_url, caption=caption, parse_mode="HTML")

            # DB save
            try:
                file_id = getattr(msg.video, 'file_id', None) if hasattr(msg, 'video') else (
                    getattr(msg.photo[-1], 'file_id', None) if getattr(msg, 'photo', None) else None
                )
                meta = {"media_url": media_url, "title": title, "caption": caption}
                media_id = await asyncio.to_thread(db.add_media, "generic", url, file_id, msg.message_id, user_id, title, ext, meta)
                await asyncio.to_thread(db.add_download, user_id, media_id, "completed")
                await asyncio.to_thread(db.increment_download_count, user_id)
            except Exception:
                logger.warning("db save failed", platform="generic")

        except Exception as e:
            logger.error(f"generic extractor failed: {e}", platform="generic")
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ Failed to extract media.")

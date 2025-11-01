import os 
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from core.utils import run_in_background, logger, db
import asyncio
from core.rate_limiter import check_and_record_user_activity
from extracters.spotify import spotify_extracter

SPOTIFY_DOMAIN = ("spotify.link", "open.spotify.com" , "spotify.com")

def is_spotify_url(url):
    return any(domain in url for domain in SPOTIFY_DOMAIN)

platform = "spotify"

class SPOTIFY_HANDLER:

    @staticmethod
    @run_in_background
    async def handle_spotify_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()

        if not is_spotify_url(url):
            return

        # user + rate-limit
        user = update.effective_user or getattr(update.message, "from_user", None)
        user_id = getattr(user, "id", None)
        username = getattr(user, "username", None)
        first_name = getattr(user, "first_name", None)
        try:
            await asyncio.to_thread(db.add_user, user_id, username, first_name)
        except Exception:
            logger.warning("db.add_user failed", platform=platform)

        allowed, reason = await check_and_record_user_activity(user_id)
        if not allowed:
            await update.message.reply_text(f"⛔ You are temporarily banned: {reason}")
            return

        # cache check: if we already have this URL stored, reuse Telegram file_id
        try:
            cached = await asyncio.to_thread(db.get_media_by_url, url)
        except Exception:
            cached = None

        if cached and cached.get("file_id"):
            try:
                # try to send cached audio by file_id (include caption if available)
                cached_meta = cached.get("metadata") or {}
                cached_caption = cached_meta.get("caption") or cached.get("title") or cached_meta.get("title") or None
                await update.message.reply_audio(audio=cached.get("file_id"), caption=cached_caption, title=cached.get("title") or None)
                try:
                    media_id = cached.get("id")
                    if media_id:
                        await asyncio.to_thread(db.add_download, user_id, media_id, "cached")
                        await asyncio.to_thread(db.increment_download_count, user_id)
                except Exception:
                    logger.warning("Failed to record cached download", platform=platform)
                return
            except Exception:
                logger.warning("Failed to send cached spotify audio; continuing to extract", platform=platform)

        status_msg = await update.message.reply_text("👾 Extracting...")

        data = await spotify_extracter(url)
        chat_id = status_msg.chat.id

        if not data:
            await status_msg.edit_text("❌ No tracks found.")
            return

        # Normalize single track
        if isinstance(data, dict):
            data = [data]

        for track in data:
            title = track.get("title", "Unknown Title")
            uploader = track.get("uploader", "Unknown Uploader")
            duration = track.get("duration", 0)
            thumbnail = track.get("thumbnail")
            audio_path = track.get("audio_path")  # <-- added path support
            if duration:
                if duration < 60:
                    duration_text = f"{round(duration)} sec"
                else:
                    mins, secs = divmod(int(duration), 60)
                    duration_text = f"{mins} min {secs} sec"
            else:
                duration_text = "Unknown"

            caption = f"🎵 {title}\n\n👤 {uploader}\n\n⏱ {duration_text}"

            # Send thumbnail or caption
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

                if thumbnail:
                    await update.message.reply_photo(photo=thumbnail, caption=caption)
                else:
                    await update.message.reply_text(caption)

            except Exception:
                logger.error("Failed to send thumbnail/caption", platform=platform)

            # Try to send audio (if available)
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
            logger.debug("spotify: audio path", platform=platform)

            if audio_path and os.path.exists(audio_path):
                try:
                    with open(audio_path, "rb") as f:
                        msg = await update.message.reply_audio(
                            audio=f,
                            title=title,
                            performer=uploader,
                            read_timeout=300,
                            pool_timeout=300,
                            connect_timeout=100,
                            write_timeout=500,
                        )

                    logger.info("Sent audio", platform=platform)

                    # persist media + download
                    try:
                        file_id = getattr(getattr(msg, 'audio', None), 'file_id', None)
                        meta = {"audio_path": audio_path, "title": title, "caption": caption}
                        # store the original post URL (url) in media.url so cache lookups work
                        media_id = await asyncio.to_thread(db.add_media, platform, url, file_id, msg.message_id, user_id, title, duration, meta)
                        await asyncio.to_thread(db.add_download, user_id, media_id, "completed")
                        await asyncio.to_thread(db.increment_download_count, user_id)
                    except Exception as e:
                        logger.warning(f"db write failed after spotify audio send: {e}", platform=platform)

                except Exception as e:
                    logger.error("Failed to send audio from path", platform=platform)
                    try:
                        await update.message.reply_text(f"❌ Failed to send audio: {e}")
                    except Exception:
                        logger.warning("Also failed to notify user about audio send failure.")

                finally:
                    try:
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                            logger.debug("Removed temporary audio file", platform=platform)
                    except Exception as e:
                            logger.warning("Could not remove temp audio file", platform=platform)
                    
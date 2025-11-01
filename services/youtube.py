import os , re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update , InputMediaVideo
from telegram.ext import ContextTypes
from core.utils import run_in_background
from core.utils import logger
from telegram.constants import ChatAction
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, quote
from core.utils import EXTRACTER
from extracters.youtube import  youtube_short_extracter , TaskManager
from extracters.youtube_metadata_fecther import fetch_youtube_metadata
from core.uploader_from_user import upload_to_telegram_youtube
import asyncio
from core.utils import db
from core.rate_limiter import check_and_record_user_activity

# ------------------- CONFIG -------------------
API_KEY = os.getenv("API_KEY", "all-7f04e0d887372e3769b200d990ae7868")

progress_map = {}
shorts_progress_map = {}

platform = "youtube"

COMMON_DOMAINS = ("youtube.com", "youtu.be")  # Add other generic sites here

def make_progress_bar(progress: int, length: int = 15) -> str:
    """Create a nice progress bar for Telegram."""
    progress = max(0, min(100, int(progress)))
    filled_len = int(length * progress / 100)
    filled = "█" * filled_len
    empty = "░" * (length - filled_len)
    return f"{filled}{empty} {progress}%"

def parse_youtube_id(url: str) -> str | None:
    match = re.search(
        r'(?:v=|be/|shorts/|embed/)([A-Za-z0-9_-]{11})', url
    )
    return match.group(1) if match else None

def is_valid_url(url):
    return url.startswith(('http://', 'https://'))


def is_youtube_url(url: str) -> bool:
    return any(domain in url for domain in COMMON_DOMAINS)

def safe_url_maker(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    if "title" in query:
        title = query["title"][0]
        # Keep only English letters, numbers, dash, underscore, and spaces
        clean_title = re.sub(r"[^a-zA-Z0-9 _-]", "", title)
        # Encode spaces as %20
        query["title"] = [quote(clean_title, safe="")]

    safe_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return safe_url

def remove_duplicate_formats(formats):
    seen = {}
    for fmt in formats:
        q = fmt["quality"].split()[0]  # e.g. "1080p60 HDR" → "1080p60"
        if q not in seen or fmt["bitrate"] > seen[q]["bitrate"]:
            seen[q] = fmt
    return list(seen.values())

class YOUTUBE_HANDLER:
    
    @staticmethod
    @run_in_background
    async def handle_youtube_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()

        if not is_youtube_url(url):
            return
        
        logger.debug(f"handle_url received url: {url}" , platform)
        
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
        await context.bot.set_message_reaction(update.effective_chat.id, update.message.message_id, ["🗿"])
        status_msg = await update.message.reply_text("👾")

        chat_id = update.message.chat_id
        
        try:

            if "/shorts/" in url:
                video_path , data = youtube_short_extracter(url)
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                title = data.get("title", "Unknown")
                thumbnail_url = data.get("thumbnail")
                caption = f"🎬 {title}\n"
                logger.debug(f"VIDEO PATH: {video_path}" , platform)
                await status_msg.delete()

                youtube_short_id = parse_youtube_id(url)
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎧 Download audio", callback_data=f"shorts:[{youtube_short_id}]")]
                ])

                await update.message.reply_video(
                    video=video_path,
                    thumbnail=thumbnail_url,
                    protect_content=True,
                    caption=caption,
                    has_spoiler=True,
                    reply_markup=keyboard,
                )
            
                try:
                    # attempt DB persist before file removal
                    try:
                        # note: video_path may be a local path; store original url/youtube id as media url
                        msg = await update.message.reply_video(
                            video=video_path,
                            thumbnail=thumbnail_url,
                            protect_content=True,
                            caption=caption,
                            has_spoiler=True,
                            reply_markup=keyboard,
                        )
                        file_id = getattr(getattr(msg, 'video', None), 'file_id', None)
                        meta = {"title": title, "caption": caption}
                        await asyncio.to_thread(db.add_media, platform, url, file_id, msg.message_id, user_id, title, None, meta)
                        media = await asyncio.to_thread(db.get_media_by_url, url)
                        try:
                            m_id = media.get('id') if isinstance(media, dict) else media
                            if m_id:
                                await asyncio.to_thread(db.add_download, user_id, m_id, 'completed')
                                await asyncio.to_thread(db.increment_download_count, user_id)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"db write failed for youtube short: {e}", platform=platform)
                    try:
                        os.remove(video_path)
                        logger.debug(f"{video_path} deleted successfully" , platform)
                    except FileNotFoundError:
                        logger.warning(f"{video_path} not found, can't delete" , platform)

                except Exception as e:
                    logger.error(f"Error deleting {video_path}: {e}" , platform)

                return
            
            else:

                data = fetch_youtube_metadata(url)
                logger.debug(f"Data : {data}" , platform)
                if not data:
                    await status_msg.edit_text("⚠️ Failed to fetch metadata.")
                    return

                videos = remove_duplicate_formats(data.get("video_formats", []))
                audios = remove_duplicate_formats(data.get("audio_formats", []))

                if not videos and not audios:
                    await status_msg.edit_text("⚠️ No downloadable formats found.")
                    return

                youtube_id = parse_youtube_id(url)
                video_buttons = []
                best_audio = max(audios, key=lambda x: x.get("bitrate", 0)) if audios else None
                audio_size = int(best_audio.get("size") or 0) if best_audio and str(best_audio.get("size")).isdigit() else 0

                for v in videos:
                    q = v.get("quality")
                    if not q:
                        continue
                    cb = f"vid[{q}][{youtube_id}]"
                    size_val = v.get("size")
                    video_size = int(v.get("size") or 0) if str(v.get("size")).isdigit() else 0
                    total_size = video_size + audio_size
                    size = TaskManager.sizeof_fmt(total_size) if total_size else "Unknown"
                    video_buttons.append(
                        InlineKeyboardButton(f"🎥 {q} ({size})", callback_data=cb)
                    )

                # audio option
                audio_buttons = []
                if audios:
                    best_audio = max(audios, key=lambda x: x.get("bitrate", 0))
                    size_val = best_audio.get("size")
                    size = TaskManager.sizeof_fmt(int(size_val)) if str(size_val).isdigit() else "Unknown"
                    cb = f"aud[best][{youtube_id}]"
                    audio_buttons.append(InlineKeyboardButton(f"🎧 Audio ({size})", callback_data=cb))

                # layout buttons (2 per row)
                all_buttons, row = [], []
                for btn in video_buttons + audio_buttons:
                    row.append(btn)
                    if len(row) == 2:
                        all_buttons.append(row)
                        row = []
                if row:
                    all_buttons.append(row)

                if not all_buttons:
                    await status_msg.edit_text("⚠️ No valid download options found.")
                    return

                reply_markup = InlineKeyboardMarkup(all_buttons)

                # caption and thumbnail
                title = data.get("title", "Unknown Video")
                dur = data.get("duration")
                if dur:
                    m, s = divmod(dur, 60)
                    h, m = divmod(m, 60)
                    duration_text = f"⏱️ {h:02}:{m:02}:{s:02}" if h else f"⏱️ {m:02}:{s:02}"
                else:
                    duration_text = ""
                caption = f"🎬 {title}\n{duration_text}"
                thumb = data.get("thumbnail")
                if thumb:
                    # Convert WebP URL → JPG version safely
                    if "vi_webp" in thumb:
                        thumb = thumb.replace("vi_webp", "vi").replace(".webp", ".jpg")
                # send message
                await status_msg.delete()
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                msg = await update.message.reply_photo(
                    photo=thumb,
                    caption=caption,
                    reply_markup=reply_markup,
                )
                try:
                    file_id = getattr(msg.photo[-1], 'file_id', None) if getattr(msg, 'photo', None) else None
                    meta = {"title": title, "caption": caption}
                    await asyncio.to_thread(db.add_media, platform, url, file_id, msg.message_id, user_id, title, None, meta)
                    media = await asyncio.to_thread(db.get_media_by_url, url)
                    try:
                        m_id = media.get('id') if isinstance(media, dict) else media
                        if m_id:
                            await asyncio.to_thread(db.add_download, user_id, m_id, 'completed')
                            await asyncio.to_thread(db.increment_download_count, user_id)
                    except Exception:
                        pass
                except Exception:
                    logger.warning("db write failed after youtube thumbnail send", platform=platform)

        except Exception as e:
            logger.error(f"Error in sending thumbnail or caption: {e}" , platform)

    @staticmethod
    @run_in_background
    async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

        query = update.callback_query
        short_id = data = query.data
        chat_id = update.callback_query.message.chat_id

        if short_id.startswith("shorts:"):
            video_id = short_id.split("shorts:")[1].strip("[]")
            youtube_url = f"https://youtube.com/shorts/{video_id}"

            await query.answer("🎧 Extracting audio...")

            audio_path = EXTRACTER.download_audio(youtube_url)
            
            if audio_path and os.path.exists(audio_path):
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                await context.bot.send_audio(chat_id, audio=open(audio_path, "rb"))
                try:
                    os.remove(audio_path)
                    logger.debug(f"Removed temp audio {audio_path}" , platform)
                except Exception as e:
                    logger.warning(f"Failed to remove temp audio {audio_path}: {e}" , platform)
            else:
                await context.bot.send_message(chat_id, "❌ Failed to extract audio.")

        match = re.match(r"vid\[(.+?)\]\[(.+?)\]", data)
        if not match:
            await query.answer("Invalid selection")
            return

        quality, youtube_id = match.groups()
        title = f"YouTube {quality}"
        download_url = f"https://www.youtube.com/watch?v={youtube_id}"

        await query.answer(f"Downloading {quality}...")

        msg = await query.edit_message_caption(
            caption=f"📥 <b>Downloading...</b>\n\n{make_progress_bar(0)}",
            parse_mode="HTML",
        )

        # Stream progress from downloader/uploader
        async for status in await upload_to_telegram_youtube(
            url=download_url,
            quality=quality,
        ):
            total = status.get("total", 0)
            logger.debug(f"upload progress total: {total}", platform=platform)
            try:
                # Downloading phase (0-50%)
                if total < 50:
                    bar = make_progress_bar(total * 2)
                    await msg.edit_caption(
                        caption=f"📥 <b>Downloading...</b>\n\n{bar}",
                        parse_mode="HTML",
                    )
                
                # Uploading phase (50-100%)
                elif total < 100:
                    bar = make_progress_bar((total - 50) * 2)
                    await msg.edit_caption(
                        caption=f"🚀 <b>Uploading...</b>\n\n{bar}",
                        parse_mode="HTML",
                    )
                
                # Completed
                elif "file_id" in status:
                    caption = f"🎬 <b>{title}</b>\n\n"
                
                    await query.edit_message_media(
                        media=InputMediaVideo(
                            media=status["file_id"],
                            caption=caption,
                            parse_mode="HTML",
                        )
                    )
                    # persist media record after upload finished
                    try:
                        user_cb = query.from_user
                        cb_user_id = getattr(user_cb, 'id', None)
                        file_id = status.get('file_id')
                        meta = {"title": title, "caption": caption}
                        await asyncio.to_thread(db.add_media, platform, download_url, file_id, query.message.message_id, cb_user_id, title, None, meta)
                        media = await asyncio.to_thread(db.get_media_by_url, download_url)
                        try:
                            m_id = media.get('id') if isinstance(media, dict) else media
                            if m_id:
                                await asyncio.to_thread(db.add_download, cb_user_id, m_id, 'completed')
                                await asyncio.to_thread(db.increment_download_count, cb_user_id)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"db write failed after youtube upload finish: {e}", platform=platform)

            except Exception as e:
                if "message is not modified" not in str(e).lower():
                    logger.debug(f"UI update error: {e}" , platform)
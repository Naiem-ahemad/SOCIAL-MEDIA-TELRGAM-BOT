from telegram.constants import ChatAction
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup , InputMediaPhoto
from telegram.ext import ContextTypes
from core.utils import run_in_background, logger, db
import asyncio
from core.rate_limiter import check_and_record_user_activity
from urllib.parse import quote
from services.instagram_and_x import description_to_filename
from extracters.linkdin import LINKDIN_EXTRACTER
from core.uploader_from_user import upload_video
from services.facebook import get_content_length

LINKDIN_DOMAINS = ("linkedin.com",)

platform = "linkedin"

def is_linkdin_url(url: str) -> bool:
    return any(domain in url for domain in LINKDIN_DOMAINS)

def build_telegram_caption(data):
    """
    Build a Telegram-ready caption with:
    - Author + posted_before on top
    - Full caption text in the middle
    - Comments + Likes at the bottom
    """
    author_name = data.get("author")
    author_url = data.get("author_url")
    posted_before = data.get("posted_before")
    likes = data.get("likes_count")
    comments = data.get("comment_count") or data.get("comment_Count")
    caption_text = (data.get("caption") or "").strip()

    parts = []
    if author_name:
        if author_url:
                parts.append(f"👤 <a href='{author_url}'>{author_name}</a>")
        else:
                parts.append(f"👤 {author_name}")

        if posted_before:
            parts.append(f"📆 {posted_before}")

        if caption_text:
            parts.append(caption_text[:900])
        
        stats = []
        if comments:
            stats.append(f"💬 {comments} comments")
        if likes:
            stats.append(f"👍 {likes} likes")
        if stats:
            parts.append(" • ".join(stats))

        full_caption = "\n\n".join(parts).strip()
        return full_caption
    
    return full_caption

class LINKEDIN_HANDLER:

    @staticmethod
    @run_in_background
    async def handle_linkdin_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()

        logger.debug("handle_linkdin_url received", platform=platform)

        # ensure user recorded + rate-limit check
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

        if not is_linkdin_url(url):
            return

        status_msg = await update.message.reply_text("👾")
        msg_id = status_msg.message_id
        chat_id = status_msg.chat_id

        if not any(x in url for x in ["/posts/" or "/post/"]):

            await status_msg.delete()

            await update.message.reply_text(

                "❌ Invalid Linkdin link.\n\n"
                "Valid:\n👉 https://www.linkedin.com/posts/xxxxx.."
            )

            return

        try:

            data = LINKDIN_EXTRACTER.linkdin_extracers(url)
            logger.debug("LINKDIN_EXTRACTER returned", platform=platform)
            if not data:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text="⚠️ No reel video found."
                )
                return

            media_type = data.get("media_type")
            media_urls = data.get("media_urls", [])

            caption = build_telegram_caption(data)

            if media_type == "video":
                
                raw_video_url = media_urls[0]
                logger.debug("VIDEO URL : sent", platform=platform)
                thumbnail_url = data.get("thumbnail")
                filename = description_to_filename(caption)
                fast_url = f"https://extracter.zer0spectrum.dpdns.org/download?url={quote(raw_video_url)}&title={filename}"
                size = await get_content_length(raw_video_url)

                if size < 50 * 1024 * 1024:
                    video_url = LINKDIN_EXTRACTER.get_linkedin_video_path(raw_video_url)
                else:
                    video_url = await upload_video(raw_video_url)

                buttons = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚡ Fast video Download", url=fast_url)]
                ])

                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                msg = await update.message.reply_video (
                    video=video_url,
                    reply_markup=buttons,
                    thumbnail=thumbnail_url,
                    caption=caption,
                    has_spoiler=True,
                    parse_mode="HTML"
                )
                try:
                    file_id = getattr(getattr(msg, 'video', None), 'file_id', None)
                    meta = {"media_url": raw_video_url, "title": filename, "caption": caption}
                  
                    media_id = await asyncio.to_thread(db.add_media, platform, url, file_id, msg.message_id, user_id, filename, None, meta)
     
                    try:
                        if media_id:
                            await asyncio.to_thread(db.add_download, user_id, media_id, 'completed')
                            await asyncio.to_thread(db.increment_download_count, user_id)
                    except Exception:
                        pass
                except Exception:
                    logger.warning("db write failed after linkedin video send", platform=platform)

                return

            elif media_type == "photo":

                photo_url = media_urls[0]
                msg = await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                
                sent = await update.message.reply_photo(
                    photo=photo_url,
                    has_spoiler=True,
                    caption=caption,
                    parse_mode="HTML"
                )
                try:
                    file_id = getattr(sent.photo[-1], 'file_id', None) if getattr(sent, 'photo', None) else None
                    meta = {"media_url": photo_url, "title": caption, "caption": caption}
                    media_id = await asyncio.to_thread(db.add_media, platform, url, file_id, sent.message_id, user_id, None, None, meta)
                    try:
                        if media_id:
                            await asyncio.to_thread(db.add_download, user_id, media_id, 'completed')
                            await asyncio.to_thread(db.increment_download_count, user_id)
                    except Exception:
                        pass
                except Exception:
                    logger.warning("db write failed after linkedin photo send", platform=platform)

            elif media_type == "carousel":

                batch_size = 10
                for i in range(0, len(media_urls), batch_size):
                    batch = media_urls[i:i + batch_size]
                    group = []
                    for idx, url in enumerate(batch):
                        if not url:
                            continue
                        if i == 0 and idx == 0:

                            group.append(InputMediaPhoto(media=url))
                        else:
                            group.append(InputMediaPhoto(media=url))

                    if group:

                        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

                        if len(group) > 1:
                            await update.message.reply_media_group(group , caption=caption , parse_mode="HTML")
                        else:
                            await update.message.reply_photo(photo=group[0].media , caption=caption , parse_mode="HTML")

        except Exception as e:

            logger.error(f"handle_linkdin_url error {e}", platform=platform)
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="⚠️ Sorry, unable to download media now. Try again later."
            )

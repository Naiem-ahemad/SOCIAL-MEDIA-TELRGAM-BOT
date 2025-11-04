from telegram.constants import ChatAction
from telegram import Update
from telegram.ext import ContextTypes
from core.utils import run_in_background, logger, db
import asyncio , re
from core.rate_limiter import check_and_record_user_activity
from extracters.pinterest import pinterest_extracter

PINTEREST_DOMAIN = ("pinterest.com", "pin.it")


def is_pinterest_url(url: str) -> bool:
    return any(domain in url for domain in PINTEREST_DOMAIN)


platform = "pinterest"


def build_telegram_caption(data: dict) -> str:
    """Build a Telegram-ready caption from a pinterest extractor result.

    Returns a short HTML-formatted caption.
    """
    try:
        user_info = data.get("user", {}) or {}
        media = data.get("media", {}) or {}

        author_username = user_info.get("username")
        author_name = user_info.get("fullName")
        author_url = user_info.get("profile_url")
        posted_before = data.get("posted_before")
        likes = data.get("reactionCounts") or data.get("likeCount")
        comments = data.get("commentCount") if data.get("commentCount") is not None else data.get("comment_Count")

        caption_text = (data.get("description") or "").strip()
        repin_count = data.get("repinCount")
        view_count = media.get("viewCount") or data.get("viewCount")

        parts = []
        if author_name:
            if not author_url and author_username:
                author_url = f"https://www.pinterest.com/{author_username}"
            if author_url:
                parts.append(f"👤 <a href='{author_url}'>{author_name}</a>")
            else:
                parts.append(f"👤 {author_name}")

        if posted_before:
            parts.append(f"📆 {posted_before}")

        if caption_text:
            parts.append(caption_text)

        stats = []
        if comments:
            stats.append(f"💬 {comments} comments")
        if likes:
            stats.append(f"👍 {likes} likes")
        if view_count:
            stats.append(f"👀 {view_count} views")
        if repin_count:
            stats.append(f"📍 {repin_count} reposts")

        if stats:
            parts.append(" • ".join(stats))

        full_caption = "\n\n".join(parts).strip()
        return full_caption

    except Exception:
        logger.error("build_telegram_caption error", platform=platform)
        return ""

class PINTEREST_HANDLER:

    @staticmethod
    @run_in_background
    async def handle_pinterest_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for Pinterest links: extract and send video or photo posts."""

        text = update.message.text.strip()
        match = re.search(r"https?://(?:www\.)?pinterest\.com/pin/\d+/?", text)
        url = match.group(0) if match else text
        if not is_pinterest_url(url):
            return

        logger.debug(f"handle_pinterest_url received: {url}", platform=platform)
        
        if not is_pinterest_url(url):
            return
        
        url_parser = url

        logger.debug(f"handle_pinterest_url received: {url_parser}", platform=platform)

        # record user + rate-limit
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

        # quick cache check before heavy extraction
        try:
            cached = await asyncio.to_thread(db.get_media_by_url, url)
        except Exception:
            cached = None

        if cached and cached.get("file_id"):
            try:
                # send cached file; prefer video, fallback to photo
                cached_meta = cached.get("metadata") or {}
                cached_caption = cached_meta.get("caption") or cached_meta.get("title") or cached.get("title") or ""
                try:
                    await update.message.reply_video(video=cached.get("file_id"), caption=cached_caption)
                except Exception:
                    await update.message.reply_photo(photo=cached.get("file_id"), caption=cached_caption)

                # record cached download
                try:
                    media_id = cached.get("id")
                    if media_id:
                        await asyncio.to_thread(db.add_download, user_id, media_id, "cached")
                        await asyncio.to_thread(db.increment_download_count, user_id)
                except Exception:
                    logger.warning("Failed to record cached download", platform=platform)

                return
            except Exception:
                logger.warning("Failed to send cached media; continuing to extract", platform=platform)

        status_msg = await update.message.reply_text("👾 Extracting...")
        msg_id = status_msg.message_id
        chat_id = status_msg.chat.id

        try:
            data = pinterest_extracter(url)
            logger.debug("pinterest_extracter returned", platform=platform)

            # Basic URL check for pin-like URLs
            if not ("/pin/" in url or "pin.it" in url or ".com/pin" in url) or not data:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=f"⚠️ Unsupported or unavailable URL:\n{url}"
                )

                logger.debug("Unsupported or empty pinterest url", platform=platform)
                return

            is_video = data.get("isVideo", False)
            caption = build_telegram_caption(data)
            # try to derive a short title for DB storage (may be None)
            title = data.get("title") or (data.get("media") or {}).get("title") or None
            logger.debug("Built caption", platform=platform)
            media = data.get("media", {}) or {}

            # Video flow
            if is_video and media.get("video"):
                video_url = media.get("video")
                await status_msg.delete()
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                msg = await update.message.reply_video(
                    video=video_url,
                    caption=caption,
                    parse_mode="HTML",
                    has_spoiler=True,
                )

                # persist media + download
                try:
                    file_id = getattr(getattr(msg, 'video', None), 'file_id', None)
                    meta = {"media_url": video_url, "title": title, "caption": caption}
                    # store the original post URL as the media.url key so later cache lookups by post URL work
                    media_id = await asyncio.to_thread(db.add_media, platform, url, file_id, msg.message_id, user_id, title, None, meta)
                    await asyncio.to_thread(db.add_download, user_id, media_id, "completed")
                    await asyncio.to_thread(db.increment_download_count, user_id)
                except Exception:
                    logger.warning("db write failed after pinterest video send", platform=platform)

                return

            # Photo flow
            logger.debug("Handling non-video pinterest content", platform=platform)
            if not media:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="⚠️ No photo/post media found."
                )
                return

            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

            image_url = media.get("hd") or media.get("sd") or media.get("original") or media.get("url")
            logger.debug("Sending pinterest image", platform=platform)

            msg = await update.message.reply_photo(
                photo=image_url,
                caption=caption,
                parse_mode="HTML",
                has_spoiler=True,
            )

            try:
                file_id = getattr(msg.photo[-1], 'file_id', None) if getattr(msg, 'photo', None) else None
                meta = {"media_url": image_url, "title": title, "caption": caption}
                # store the original post URL as the media.url key so later cache lookups by post URL work
                media_id = await asyncio.to_thread(db.add_media, platform, url, file_id, msg.message_id, user_id, title, None, meta)
                await asyncio.to_thread(db.add_download, user_id, media_id, "completed")
                await asyncio.to_thread(db.increment_download_count, user_id)
            except Exception:
                logger.warning("db write failed after pinterest photo send", platform=platform)

        except Exception:
            logger.error("Failed to send pinterest content", platform=platform)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="⚠️ Sorry, unable to download media now. Try again later.",
                )
            except Exception:
                logger.warning("Failed to notify user about pinterest failure", platform=platform)

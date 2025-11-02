from telegram.constants import ChatAction
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup , InputMediaPhoto
from telegram.ext import ContextTypes
from core.utils import run_in_background
from core.utils import logger, db, record_media_and_download
import asyncio
from core.rate_limiter import check_and_record_user_activity
from urllib.parse import quote
import unicodedata , re 
from extracters.instagram import INSTAGRAM_EXTRACTER
from extracters.twitter import twitter_media

INSTAGRAM_DOMAINS = ("instagram.com", "instagr.am")
TWITTER_DOMAINS = ("twitter.com", "x.com")
BASE_PATH = "data"  # base folder to store files
API_KEY = "all-7f04e0d887372e3769b200d990ae7868"

platform = "instagram_x"

def description_to_filename(description: str, max_words: int = 6) -> str:
    cleaned = "".join(
        c for c in description 
        if unicodedata.category(c)[0] != "So" and not re.match(r"[\U00010000-\U0010ffff]", c)
    )

    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()
    short = "_".join(words[:max_words]) if words else "video"
    short = short.lower()

    return short

def format_view_count(count):
    if count >= 10**9:
        return f"{count / 10**9:.1f}B"  # Billion
    elif count >= 10**6:
        return f"{count / 10**6:.1f}M"  # Million
    elif count >= 10**3:
        return f"{count / 10**3:.1f}K"  # Thousand
    else:
        return str(count)
    
def is_instagram_url(url: str) -> bool:
    return any(domain in url for domain in INSTAGRAM_DOMAINS)

def is_twitter_url(url: str) -> bool:
    return any(domain in url for domain in TWITTER_DOMAINS)

class INSTAGRAM_HANDLER:

    @staticmethod
    @run_in_background
    async def handle_instagram_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()
        logger.debug("handle_instagram_url received", platform=platform)
        if not is_instagram_url(url):
            return

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

        # cache check before heavy extraction
        try:
            cached = await asyncio.to_thread(db.get_media_by_url, url)
            logger.debug(f"cache lookup result: {cached}", platform=platform)
        except Exception:
            cached = None

        if cached and cached.get("file_id"):
            try:
                # prefer video, fallback to photo
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

        status_msg = await update.message.reply_text("👾")
        msg_id = status_msg.message_id
        chat_id = status_msg.chat_id

        if not any(x in url for x in ["/p/", "/reel/", "/reels/"]):
            await status_msg.delete()
            await update.message.reply_text(
                "❌ Invalid Instagram link.\n\n"
                "Valid:\n👉 https://www.instagram.com/p/xxxx/\n👉 https://www.instagram.com/reel/xxxx/"
            )
            return

        data = await INSTAGRAM_EXTRACTER.extract_instagram_auto(url)
        print("DATA : " , data)
        type = data.get("type")

        title = f"📸 {data.get("title")}"
        desc = data.get("description", "")
        likes = data.get("like_count",0)
        comments = data.get("comment_count",0)
        caption = (
            f"{title}\n\n📃 Description: {(desc or '')[:1024]}"
            f"\n\n👍 : {format_view_count(likes)} likes"
            f"\n\n💬 : {format_view_count(comments)} comments"
        )
        media_list = data.get("media", []) or None
                   
        try:
            # ------------- Extractor
            if type == "reel":
                logger.debug("instagram_reel_extracter returned", platform=platform)
                if not data:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text="⚠️ No reel video found."
                    )
                    return

                # ----------- Reel handling
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)

                title = f"📸 {data.get('title','Unknown')}"
                desc = data.get("description","")
                likes = data.get("like_count",0)
                comments = data.get("comment_count",0)

                caption = f"{title}\n\n📃 Description: {desc[:1024]}\n\n👍 : {format_view_count(likes)} likes \n\n💬 : {format_view_count(comments)} comments"

                audio_urls = data.get("audio_urls" , [])
                audio_url = audio_urls[0]
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎧 audio", url=audio_url)]
                ])
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                # send and persist media
                try:
                    msg = await update.message.reply_video(
                        video=data.get("video"),
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                        has_spoiler=True
                    )
                    file_id = getattr(getattr(msg, 'video', None), 'file_id', None)
                    media_url = data.get("video")
                    meta = {"title": data.get('title'), "caption": caption}
                    await record_media_and_download(user_id, url, media_url, file_id, msg.message_id, platform, title=data.get('title'), metadata=meta)
                except Exception as e:
                    logger.warning(f"Failed to persist instagram reel media: {e}", platform=platform)
                return

            elif type == "post":

                if not media_list:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text="⚠️ No photo/post media found."
                    )
                    return
                
                # prepare caption
                main = media_list[0]
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                # single photo send
                try:
                    msg = await update.message.reply_photo(
                        photo=main.get("url"),
                        caption=caption,
                        parse_mode="HTML",
                        has_spoiler=True
                    )
                    try:
                        file_id = getattr(msg.photo[-1], 'file_id', None) if getattr(msg, 'photo', None) else None
                        media_url = main.get("url")
                        meta = {"title": main.get('title') or None, "caption": caption}
                        await record_media_and_download(user_id, url, media_url, file_id, msg.message_id, platform, title=(main.get('title') or None), metadata=meta)
                    except Exception:
                        logger.warning("Failed to persist instagram single photo metadata", platform=platform)
                except Exception as e:
                    logger.error(f"Failed to send instagram photo: {e}", platform=platform)
                
            else:
                # album posts
                batch_size = 10
                for i in range(0, len(media_list), batch_size):
                    batch = media_list[i:i+batch_size]
                    group = []
                    source_urls = []
                    for idx, m in enumerate(batch):
                        src_url = m.get("url")
                        if not src_url:
                            continue
                        source_urls.append(src_url)
                        if i == 0 and idx == 0:
                            group.append(InputMediaPhoto(media=src_url, caption=caption))
                        else:
                            group.append(InputMediaPhoto(media=src_url))
                    if group:
                        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                        if len(group) > 1:
                            msgs = await update.message.reply_media_group(group)
                            # persist each media in group
                            try:
                                for src, sent_msg in zip(source_urls, msgs):
                                    fid = None
                                    if getattr(sent_msg, 'photo', None):
                                        fid = getattr(sent_msg.photo[-1], 'file_id', None)
                                    meta = {"title": None, "caption": caption}
                                    await record_media_and_download(user_id, url, src, fid, sent_msg.message_id, platform, title=None, metadata=meta)
                            except Exception:
                                logger.warning("Failed to persist one or more group media entries", platform=platform)
                        else:
                            sent = await update.message.reply_photo(photo=group[0].media, caption=caption)
                            try:
                                fid = getattr(sent.photo[-1], 'file_id', None) if getattr(sent, 'photo', None) else None
                                meta = {"title": None, "caption": caption}
                                await record_media_and_download(user_id, url, group[0].media, fid, sent.message_id, platform, title=None, metadata=meta)
                            except Exception:
                                logger.warning("Failed to persist single photo from album send", platform=platform)

        except Exception as e :
            logger.error(f"handle_instagram_url caught error {e}", platform=platform)
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="⚠️ Sorry, unable to download media now. Try again later."
            )

class TWITTER_HANDLER:

    @staticmethod
    @run_in_background
    async def handle_twitter_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()

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

        # cache check
        try:
            cached = await asyncio.to_thread(db.get_media_by_url, url)
            logger.debug(f"cache lookup result: {cached}", platform=platform)
        except Exception:
            cached = None

        if cached and cached.get("file_id"):
            try:
                cached_meta = cached.get("metadata") or {}
                cached_caption = cached_meta.get("caption") or cached_meta.get("title") or cached.get("title") or ""
                try:
                    await update.message.reply_video(video=cached.get("file_id"), caption=cached_caption)
                except Exception:
                    await update.message.reply_photo(photo=cached.get("file_id"), caption=cached_caption)
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

        status_msg = await update.message.reply_text("👾")
        await context.bot.set_message_reaction(
            update.effective_chat.id,
            update.message.message_id,
            ["🗿"]
        )
        chat_id = status_msg.chat_id
        msg_id = status_msg.message_id

        API_KEY = "all-7f04e0d887372e3769b200d990ae7868"
    
        try:
            data = twitter_media(url)
            logger.debug("twitter_media returned", platform=platform)
            if not data:
                await status_msg.edit_text("⚠️ Unable to fetch data.")
                return

            media_list = data.get("media", [])
            if not media_list:
                await status_msg.edit_text("⚠️ No media found.")
                return

            first = media_list[0]
            title = f"📸 Twitter by {first.get('username','Unknown')}"
            description = first.get("content", "")
            view_count_str = format_view_count(first.get("view_count"))
            caption = f"{title}\n\n📃: {description}\n\n👀 Views: {view_count_str}"[:1024]

            media_type = first.get("type")
            video_url = first.get("media_url")
            filename = first.get("filename")

            # ---- VIDEO ----
            if media_type == "video" and video_url:
                download_audio_url = f"https://extracter.zer0spectrum.dpdns.org/extract-audio?url={quote(video_url)}&title={filename}&key={API_KEY}"
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎧 Download audio", url=download_audio_url)]
                ])
                # debug: video_url sent to user (removed print)
                await status_msg.delete()
                await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
                try:
                    msg = await update.message.reply_video(
                        video=video_url,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                        has_spoiler=True,
                    )
                    file_id = getattr(getattr(msg, 'video', None), 'file_id', None)
                    try:
                        await record_media_and_download(user_id, url, video_url, file_id, msg.message_id, platform, title=filename)
                    except Exception:
                        logger.warning("Failed to persist twitter video send", platform=platform)
                except Exception as e:
                    logger.error(f"Failed to send twitter video: {e}", platform=platform)

            # ---- SINGLE PHOTO ----
            elif media_type == "photo" and len(media_list) == 1:
                await status_msg.delete()
                await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
                try:
                    msg = await update.message.reply_photo(
                        photo=first.get("media_url"),
                        caption=caption,
                        parse_mode="HTML",
                        has_spoiler=True,
                    )
                    try:
                        file_id = getattr(msg.photo[-1], 'file_id', None) if getattr(msg, 'photo', None) else None
                        await record_media_and_download(user_id, url, first.get("media_url"), file_id, msg.message_id, platform, title=first.get('filename'))
                    except Exception:
                        logger.warning("Failed to persist twitter single photo send", platform=platform)
                except Exception as e:
                    logger.error(f"Failed to send twitter photo: {e}", platform=platform)

            # ---- MULTIPLE PHOTOS ----
            else:
                await context.bot.delete_message(chat_id, msg_id)
                remaining_caption = "\n\n📷 Remaining photo"
                batch_size = 10

                for i in range(0, len(media_list), batch_size):
                    batch = media_list[i:i + batch_size]
                    media_group = []

                    source_urls = []
                    for idx, m in enumerate(batch):
                        img = m.get("media_url")
                        if not img:
                            continue
                        source_urls.append(img)
                        if i == 0 and idx == 0:
                            media_group.append(InputMediaPhoto(media=img, caption=caption))
                        else:
                            media_group.append(InputMediaPhoto(media=img))

                    if not media_group:
                        continue

                    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)

                    if len(media_group) > 1:
                        msgs = await update.message.reply_media_group(media_group)
                        try:
                            for src, sent_msg in zip(source_urls, msgs):
                                fid = None
                                if getattr(sent_msg, 'photo', None):
                                    fid = getattr(sent_msg.photo[-1], 'file_id', None)
                                await record_media_and_download(user_id, url, src, fid, sent_msg.message_id, platform, title=None)
                        except Exception:
                            logger.warning("Failed to persist one or more twitter group media entries", platform=platform)
                    else:
                        sent = await update.message.reply_photo(
                            photo=media_group[0].media,
                            caption=remaining_caption,
                        )
                        try:
                            fid = getattr(sent.photo[-1], 'file_id', None) if getattr(sent, 'photo', None) else None
                            await record_media_and_download(user_id, url, media_group[0].media, fid, sent.message_id, platform, title=None)
                        except Exception:
                            logger.warning("Failed to persist single twitter photo from group send", platform=platform)

        except Exception as e:
            await update.message.reply_text(f"⚠️ Something went wrong. Error {e}")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup , InputMediaPhoto
from telegram.ext import ContextTypes
from core.utils import run_in_background
from core.utils import logger , db
from urllib.parse import quote
from telegram.constants import ChatAction
from extracters.facebook import facebook_post_extracter , facebook_reel_extracter
from extracters.linkdin import LINKDIN_EXTRACTER
import aiohttp , asyncio
from core.uploader_from_user import upload_video

INSTAGRAM_DOMAINS = ("facebook.com", "fb.watch")

platform = "facebook"

def is_facebook_url(url: str) -> bool:
    return any(domain in url for domain in INSTAGRAM_DOMAINS)

async def get_content_length(url: str) -> int:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=10) as resp:
                return int(resp.headers.get("Content-Length", 0))
    except Exception:
        return 0
        
class FACEBOOK_HANDLER:
    
    @staticmethod
    def filter_real_media(media_dict):
        """
        Keep only real media items based on resolution (width & height).
        Discards small images like thumbnails/stickers.
        """
        filtered = {}
        for key, m in media_dict.items():
            width = m.get("width", 0)
            height = m.get("height", 0)
            if width >= 400 and height >= 400:  # adjust threshold as needed
                filtered[key] = m
                
        return filtered

    @staticmethod
    @run_in_background
    async def handle_facebook_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()
        
        if not is_facebook_url(url):
            return
        
        chat_id = update.message.chat_id
        user_id  = update.effective_user.id
        status_msg = await update.message.reply_text("🔮")
        # quick cache check: if media already in DB for this URL, reuse Telegram file_id
        try:
            cached = await asyncio.to_thread(db.get_media_by_url, url)
        except Exception:
            cached = None

        if cached and cached.get("file_id"):
            try:
                await safe_delete(status_msg)
                file_id = cached.get("file_id")
                # compute caption from stored metadata
                cached_meta = cached.get("metadata") or {}
                cached_caption = cached_meta.get("caption") or cached_meta.get("title") or cached.get("title") or ""
                # try video first
                try:
                    await update.message.reply_video(video=file_id, caption=cached_caption)
                except Exception:
                    # fallback to photo
                    try:
                        await update.message.reply_photo(photo=file_id, caption=cached_caption)
                    except Exception:
                        logger.warning("Cached file_id exists but failed to send via Telegram", platform=platform)

                # link download record
                try:
                    media_id = cached.get("id") if isinstance(cached.get("id"), int) else (cached.get("id") if cached.get("id") else None)
                    if media_id:
                        await asyncio.to_thread(db.add_download, user_id, media_id, "cached")
                        await asyncio.to_thread(db.increment_download_count, user_id)
                except Exception:
                    logger.warning("Failed to record cached download", platform=platform)

                return
            except Exception:
                logger.warning("cache send failed, continuing to extract", platform=platform)
        await context.bot.set_message_reaction(update.effective_chat.id, update.message.message_id, ["😎"])

        API_KEY = "all-7f04e0d887372e3769b200d990ae7868"

        async def safe_delete(msg):
            try:
                await msg.delete()
            except Exception:
                pass  # message already deleted or can't delete
            
        try:
            
            if "reel" in url or "videos" in url or "/r/" in url or "/v/" in url:
                logger.debug("Detected reel/video URL", platform=platform)
                # VIDEO
                data = facebook_reel_extracter(url)
                if not data or not data.get("data"):
                    await status_msg.edit_text("⚠️ No available formats. The reel may be private or restricted.")
                    return

                media_data = data.get("data")
                if isinstance(media_data, dict) and "hd" in media_data:

                    hd_url = media_data["hd"]
                    filename = url.split("/")[-1] + ".mp4"
                    size = await get_content_length(hd_url)

                    if size < 50 * 1024 * 1024:
                        download_url = LINKDIN_EXTRACTER.get_linkedin_video_path(hd_url)
                    else:
                        download_url = await upload_video(hd_url)
                    
                    download_audio_url = f"https://extracter.zer0spectrum.dpdns.org/download-audio?url={quote(hd_url)}&title={filename}&key={API_KEY}"
                    fast_download_url = f"https://extracter.zer0spectrum.dpdns.org/download?url={quote(hd_url)}&title={filename}&key={API_KEY}"
                    
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🎧 Download audio", url=download_audio_url),
                        InlineKeyboardButton("⚡ Fast Download", url=fast_download_url)]
                    ])

                    title = media_data.get("title", None)
                    description = media_data.get("description", None)

                    caption = (
                        f"{title}\n\n"
                        f"📜 Description (original):\n{description[:1024]}\n\n\n"
                        "⚠️ Note: The above description and any links were written by the original post author — "
                        "we do not endorse or add any external content."
                    )

                    thumbnail = data.get("thumbnail")

                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                    await safe_delete(status_msg)

                    # check content length
                    print(size)
                    try:
                        await update.message.reply_video(
                                video=download_url,
                                caption=caption,
                                reply_markup=keyboard,
                                write_timeout=300,
                                read_timeout=300,
                            )

                    except Exception as e:

                        logger.error("Upload error", platform=platform)
                        caption += "\n\n Download the Video from below."
                        await update.message.reply_photo(
                            photo=thumbnail,
                            caption=caption,
                            reply_markup=keyboard,
                        )
                        
            else:

                data_json = await facebook_post_extracter(url)

                # Extract media
                media = data_json.get("media", {})

                # Ensure media is in dict format
                if isinstance(media, dict) and "uri" in media:
                    media_dict = {"media_1": media}
                elif isinstance(media, dict):
                    media_dict = media
                else:
                    media_dict = {}

                # Optional: filter out very small images (not real)
                media_dict = {
                    k: v for k, v in media_dict.items()
                    if isinstance(v, dict) and v.get("width", 0) >= 400 and v.get("height", 0) >= 400
                }

                metadata = data_json.get("metadata", {})

                # Collect only valid scontent URLs
                photos = []
                for item in media_dict.values():
                    if isinstance(item, dict):
                        uri = item.get("uri", "")
                        if uri and "scontent" in uri:
                            photos.append({
                                "url": uri,
                                "title": metadata.get("title", ""),
                            })

                if not photos:
                    await update.message.reply_text("⚠️ Sorry, no photo could be retrieved from this URL.")
                    return
                
                desc = metadata.get("description")
                likes_count = metadata.get("likes_count_in_formated")
                share_count = metadata.get("share_count_in_formated")
                title = metadata.get("title")

                caption = (
                    f"{title}\n\n"
                    f"📜 Description (original):\n{desc[:1024]}\n\n"
                    f"👍 Likes: {likes_count}\n"
                    f"💬 Shares: {share_count}\n\n"
                    "⚠️ Note: The above description and any links were written by the original post author — "
                    "we do not endorse or add any external content."
                )

                await safe_delete(status_msg)

                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                # Send one or multiple photos
                if len(photos) == 1:
                        
                    await update.message.reply_photo(
                        photos[0]["url"],
                        caption=caption,
                    )

                else:
                    desc = metadata.get("description")
                    likes_count = metadata.get("likes_count_in_formated")
                    share_count = metadata.get("share_count_in_formated")
                    title = metadata.get("title")
                    
                    caption = (
                        f"{title}\n\n"
                        f"📜 Description (original):\n{desc[:1024]}\n\n"
                        f"👍 Likes: {likes_count}\n"
                        f"💬 Shares: {share_count}\n\n"
                        "⚠️ Note: The above description and any links were written by the original post author — "
                        "we do not endorse or add any external content."
                    )

                    await safe_delete(status_msg)

                    for i in range(0, len(photos), 10):
                        batch = photos[i:i+10]
                        media_group = [
                            InputMediaPhoto(media=p["url"], caption=caption if idx == 0 else None)
                            for idx, p in enumerate(batch)
                        ]
                        await update.message.reply_media_group(media_group)

        except Exception as e:

            logger.error(f"handle_facebook_url failed {e}", platform=platform)
            await status_msg.reply_text(f"⚠️ Error: {str(e)}")

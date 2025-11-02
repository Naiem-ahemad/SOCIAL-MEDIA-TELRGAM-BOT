import os , asyncio , re , threading
import aiohttp
import subprocess
from pyrogram import Client
from tempfile import NamedTemporaryFile
from core.utils import EXTRACTER, logger , CookieManager
from dotenv import load_dotenv
from yt_dlp import YoutubeDL
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL = "@data_base1001"
cookies_manager = CookieManager()
platform = "BOT_API"

def get_video_dimensions(path):

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", path],
            capture_output=True, text=True
        )
        w, h = map(int, result.stdout.strip().split(","))
        return w, h
    except Exception as e:
        logger.warning(f"Failed to get video dimensions for {path}, using default 1280x720: {e}", platform=platform)
        return 1280, 720

async def upload_to_telegram(url, width=None, height=None):

    async with Client("session", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH) as app:

        logger.debug(f"Streaming from URL: {url}", platform=platform)

        if ".m3u8" in url or "https" in url:

            tmp_path = EXTRACTER.download_video_m3u8(url)
            
        elif not "http" in url:

            tmp_path = url

        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    with NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                        async for chunk in resp.content.iter_chunked(4 * 1024 * 1024):
                            tmp.write(chunk)
                    tmp_path = tmp.name

        if not width or not height:
            width, height = get_video_dimensions(tmp_path)

        await app.get_chat(CHANNEL)

        logger.debug(f"Uploading {tmp_path} ({width}x{height})", platform=platform)
        
        msg = await app.send_video(
            chat_id=CHANNEL,
            video=tmp_path,
            width=width,
            height=height,
            supports_streaming=True,

        )

        os.remove(tmp_path)

        logger.debug(f"Sent successfully. File ID: {msg.video.file_id}", platform=platform)

        return msg.video.file_id


async def upload_video(url):
        
    file_id = await upload_to_telegram(url=url)

    return file_id

app = Client("bot_session", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

async def start_bot():
    if not app.is_connected:
        await app.start()

async def stop_bot():
    if app.is_connected:
        await app.stop()

ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

progress_lock = threading.Lock()

async def upload_to_telegram_youtube(url, quality=None):
    """Download YouTube video → upload to Telegram with live progress."""
    await start_bot()
    progress = {"download": 0, "upload": 0}
    progress_lock = asyncio.Lock()
    upload_done = asyncio.Event()
    tmp_path_holder = {"path": None}
    loop = asyncio.get_running_loop()


    # --- Progress Update Helper ---
    async def _update_progress(key, value):
        async with progress_lock:
            progress[key] = min(100, round(value, 2))

    # --- Download Hook ---
    def download_hook(d):
        """
        Progress hook for yt-dlp to update an asyncio coroutine.
        """
        if d["status"] == "downloading":
            raw_percent = ansi_escape.sub("", d.get("_percent_str", "0%")).strip()
            try:
                percent = float(raw_percent.strip("%"))
            except ValueError:
                percent = 0.0

            scaled = round(percent / 2, 2)  # map 0–100 → 0–50
            asyncio.run_coroutine_threadsafe(
                _update_progress("download", scaled), loop
            )

        elif d["status"] == "finished":
            asyncio.run_coroutine_threadsafe(
                _update_progress("download", 50.0), loop
            )
            logger.info("✅ Download complete")

        elif d["status"] == "error":
            logger.error(f"❌ An error occurred: {d.get('error')}")

    # --- Download Function (threaded) ---
    def _download():
        q = quality.replace("p", "")

        ydl_opts = {
            "format": f"bestvideo[height={q}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height={q}]+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": "./temp/%(id)s.%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [download_hook],
            "cookiefile":cookies_manager.get_youtube_cookie()
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                tmp_path_holder["path"] = ydl.prepare_filename(info)
        except Exception as e:
            logger.error(f"❌ Download error: {e}")
            tmp_path_holder["path"] = None

    # --- Upload Progress ---
    def upload_progress(current, total):
        if total > 0:
            percent = int(current * 100 / total)
            scaled = 50 + (percent / 4)  # 50–100 range
            asyncio.run_coroutine_threadsafe(
                _update_progress("upload", scaled), loop
            )
            if percent >= 100:
                loop.call_soon_threadsafe(upload_done.set)

    # --- Progress Generator ---
    async def progress_stream():
        last_total = -1

        # Start download in background
        download_task = loop.run_in_executor(None, _download)

        # Track while downloading
        while not download_task.done():
            async with progress_lock:
                total = progress["download"] + progress["upload"]
            if total != last_total:
                yield {"total": total, **progress}
                last_total = total
            await asyncio.sleep(0.5)

        await download_task
        tmp_path = tmp_path_holder["path"]

        if not tmp_path or not os.path.exists(tmp_path):
            raise Exception("❌ Download failed")

        logger.debug(f"✅ Temp Path: {tmp_path}")


        width, height = get_video_dimensions(tmp_path)
        print(width , height)
        if height < 360 or width < 640:
            aspect_ratio = width / height if height != 0 else 1
            new_width = 720
            new_height = int(new_width / aspect_ratio)
            width, height = new_width, new_height
        
        try:
            await app.get_chat(CHANNEL)
        except Exception as e:
            logger.warning(f"Cannot meet channel: {e}")
        
        # --- Upload phase ---
        send_task = asyncio.create_task(
            app.send_video(
                chat_id=CHANNEL,
                video=tmp_path,
                width=width,
                height=height,
                supports_streaming=True,
                progress=upload_progress,
            )
        )

        # Track while uploading
        while not upload_done.is_set():
            async with progress_lock:
                total = progress["download"] + progress["upload"]
            if total != last_total:
                yield {"total": total, **progress}
                last_total = total
            await asyncio.sleep(0.5)

        sent = await send_task
        yield {"total": 100, "download": 50, "upload": 100, "file_id": sent.video.file_id}

        # Cleanup
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                logger.debug(f"🗑️ Cleaned up: {tmp_path}")
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")

        await stop_bot()

    return progress_stream()

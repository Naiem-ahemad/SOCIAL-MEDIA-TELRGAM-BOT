import os
import re
import io
import sys
import zlib
import time
import json
import base64
import yt_dlp
import urllib
import random
import sqlite3
import asyncio
import logging
import requests
import argparse
import tempfile
import itertools
import subprocess
import gallery_dl
from pathlib import Path
from functools import wraps
from typing import Any, Optional
from cryptography.fernet import Fernet
from datetime import datetime , timedelta

ENCRYPTION_KEY = b'wxk9V_lppKFwN1LzRroxrXOxKxhhRD2GhhxVhwLxflw='

fernet = Fernet(ENCRYPTION_KEY)

platform = "UTILS"

USER_AGENTS = [
        # Windows Chrome
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",

        # Windows Firefox
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Mozilla/5.0 (Windows NT 11.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",

        # MacOS Safari
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",

        # MacOS Chrome
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

    
REFERERS = [
        "https://www.google.com/",
        "https://www.bing.com/",
        "https://www.facebook.com/",
        "https://www.instagram.com/"
]

def run_in_background(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
       
        asyncio.create_task(func(*args, **kwargs))
    return wrapper


class Database:
    _instance = None

    def __new__(cls, path=".data/bot.db"):
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_db(path)
        return cls._instance

    def _init_db(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.cur = self.conn.cursor()
        self._setup()

    def _setup(self):
        self.cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            plan TEXT DEFAULT 'Free',
            joined_at TEXT,
            last_used TEXT,
            total_downloads INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            reason TEXT,
            banned_until TEXT
        );

        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            url TEXT,
            file_id TEXT,
            msg_id TEXT,
            user_id INTEGER,
            title TEXT,
            metadata TEXT,
            duration TEXT,
            timestamp TEXT
        );

        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            media_id INTEGER,
            status TEXT,
            timestamp TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        self.conn.commit()

    def _column_exists(self, table, column):
        self.cur.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in self.cur.fetchall()]
        return column in columns

    # -------- USERS -------- #
    def add_user(self, user_id, username, first_name):
        now = datetime.now().isoformat()
        self.cur.execute("""
            INSERT OR IGNORE INTO users (id, username, first_name, joined_at, last_used)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, first_name, now, now))
        self.conn.commit()

    def update_user_activity(self, user_id):
        self.cur.execute("UPDATE users SET last_used=? WHERE id=?", (datetime.now().isoformat(), user_id))
        self.conn.commit()

    def increment_download_count(self, user_id):
        self.cur.execute(
            "UPDATE users SET total_downloads = total_downloads + 1 WHERE id=?",
            (user_id,),
        )
        self.conn.commit()

    # -------- USERS -------- #
    def get_user(self, user_id):
        self.cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = self.cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in self.cur.description]
        return dict(zip(cols, row))

    def get_all_users(self):
        """Return all users as list of dicts"""
        self.cur.execute("SELECT * FROM users ORDER BY id DESC")
        rows = self.cur.fetchall()
        cols = [col[0] for col in self.cur.description]
        return [dict(zip(cols, row)) for row in rows]

    # -------- MEDIA -------- #
    def add_media(self, platform, url, file_id, msg_id, user_id, title=None, duration=None, metadata=None):
        now = datetime.now().isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        self.cur.execute("""
            INSERT INTO media (platform, url, file_id, msg_id, user_id, title, duration, metadata, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (platform, url, file_id, msg_id, user_id, title, duration, meta_json, now))
        self.conn.commit()
        return self.cur.lastrowid

    def get_media_by_id(self, media_id):
        self.cur.execute("SELECT * FROM media WHERE id=?", (media_id,))
        row = self.cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in self.cur.description]
        data = dict(zip(cols, row))
        if data.get("metadata"):
            try:
                data["metadata"] = json.loads(data["metadata"])
            except Exception:
                data["metadata"] = {}
        return data
    
    def get_media_by_url(self, url):
        """Fetch media by URL"""
        self.cur.execute("SELECT * FROM media WHERE url=?", (url,))
        row = self.cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in self.cur.description]
        data = dict(zip(cols, row))
        if data.get("metadata"):
            try:
                data["metadata"] = json.loads(data["metadata"])
            except Exception:
                data["metadata"] = {}
        return data

    def get_all_media(self):
        """Return all media as list of dicts"""
        self.cur.execute("SELECT * FROM media ORDER BY id DESC")
        rows = self.cur.fetchall()
        cols = [col[0] for col in self.cur.description]
        result = []
        for row in rows:
            item = dict(zip(cols, row))
            if item.get("metadata"):
                try:
                    item["metadata"] = json.loads(item["metadata"])
                except Exception:
                    item["metadata"] = {}
            result.append(item)
        return result
    
    # -------- DOWNLOADS -------- #
    def add_download(self, user_id, media_id, status="completed"):
        self.cur.execute("""
            INSERT INTO downloads (user_id, media_id, status, timestamp)
            VALUES (?, ?, ?, ?)
        """, (user_id, media_id, status, datetime.now().isoformat()))
        self.conn.commit()
    
    def get_user_downloads(self, user_id, limit=10):
        self.cur.execute("""
            SELECT d.id, m.platform, m.url, d.status, d.timestamp
            FROM downloads d
            JOIN media m ON d.media_id = m.id
            WHERE d.user_id=?
            ORDER BY d.id DESC
            LIMIT ?
        """, (user_id, limit))
        rows = self.cur.fetchall()
        cols = [c[0] for c in self.cur.description]
        return [dict(zip(cols, row)) for row in rows]
    
    def get_total_users(self):
        self.cur.execute("SELECT COUNT(*) FROM users")
        return self.cur.fetchone()[0]

    def get_total_downloads(self):
        self.cur.execute("SELECT COUNT(*) FROM downloads")
        return self.cur.fetchone()[0]

    def get_top_users(self, limit=5):
        self.cur.execute("SELECT username, total_downloads FROM users ORDER BY total_downloads DESC LIMIT ?", (limit,))
        return self.cur.fetchall()
    
    def ban_user(self, user_id, reason="", duration=None):
        """
        Ban a user.
        duration: int (hours) or None for permanent
        """
        if not self._column_exists("users", "banned_until"):
            self.cur.execute("ALTER TABLE users ADD COLUMN banned_until TEXT")

        if not self._column_exists("users", "banned"):
            self.cur.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")

        if not self._column_exists("users", "reason"):
            self.cur.execute("ALTER TABLE users ADD COLUMN reason TEXT")

        banned_until = None
        if duration:
            banned_until = (datetime.now() + timedelta(hours=duration)).isoformat()

        self.cur.execute("""
            UPDATE users
            SET banned=1, reason=?, banned_until=?
            WHERE id=?
        """, (reason, banned_until, user_id))
        self.conn.commit()

    def unban_user(self, user_id):
        self.cur.execute("""
            UPDATE users
            SET banned=0, reason=NULL, banned_until=NULL
            WHERE id=?
        """, (user_id,))
        self.conn.commit()

    def is_banned(self, user_id):
        self.cur.execute("SELECT banned, banned_until FROM users WHERE id=?", (user_id,))
        res = self.cur.fetchone()

        if not res:
            return False

        banned, banned_until = res

        # Auto unban if temporary ban expired
        if banned_until:
            if datetime.fromisoformat(banned_until) < datetime.now():
                self.unban_user(user_id)
                return False

        return banned == 1
    
db = Database()


async def record_media_and_download(user_id: int, post_url: str, media_url: str, file_id: str, msg_id: int, platform: str, title: str | None = None, duration: str | None = None, metadata: dict | None = None, status: str = "completed") -> int | None:
    """Helper to persist media and download records safely from async code.

    This centralizes DB writes and ensures they run in a thread so the event loop is not blocked.
    Returns the media_id (int) on success or None on failure.
    """
    try:
        media_id = await asyncio.to_thread(db.add_media, platform, post_url, file_id, msg_id, user_id, title, duration, metadata or {})
    except Exception as e:
        logger.warning(f"record_media_and_download: add_media failed: {e}", platform=platform)
        return None

    try:
        await asyncio.to_thread(db.add_download, user_id, media_id, status)
        await asyncio.to_thread(db.increment_download_count, user_id)
    except Exception as e:
        logger.warning(f"record_media_and_download: add_download/increment failed: {e}", platform=platform)

    return media_id

class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[94m",    # Blue
        "INFO": "\033[92m",     # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",    # Red
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        time = datetime.now().strftime("%H:%M:%S")
        platform = f"[{record.platform}]" if hasattr(record, "platform") and record.platform else ""
        msg = f"{time} | {record.levelname} {platform} {record.getMessage()}"
        return f"{color}{msg}{self.RESET}"

# --- Main Logger class ---
class Logger:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._setup()
        return cls._instance

    def _setup(self):
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("-d", "--debug", action="store_true", help="Enable debug mode")
        args, _ = parser.parse_known_args()
    
        self.logger = logging.getLogger("BotLogger")
        self.logger.setLevel(logging.DEBUG if args.debug else logging.INFO)

        handler = logging.StreamHandler()
        handler.setFormatter(ColorFormatter())

        if not self.logger.handlers:
            self.logger.addHandler(handler)

        if args.debug:
            self.logger.debug("Debug mode enabled ✅")

    # ---- Custom log methods ----
    def _log(self, level, msg, platform=None):
        extra = {"platform": platform.upper() if platform else ""}
        self.logger.log(level, msg, extra=extra)

    def debug(self, msg, platform=None): self._log(logging.DEBUG, msg, platform)
    def info(self, msg, platform=None): self._log(logging.INFO, msg, platform)
    def warning(self, msg, platform=None): self._log(logging.WARNING, msg, platform)
    def error(self, msg, platform=None): self._log(logging.ERROR, msg, platform)

# --- Global instance ---
logger = Logger()

class CookieManager:

    def __init__(self):
        # Detect base path depending on system
        if os.name == "nt":  # Windows
            
            base_path = "./cookies"
        else:  # Linux/Ubuntu
            base_path = "/home/ubuntu/tg-bot/bot/cookies"

        # Define all cookie paths you expect
        self.cookie_files = {
            "facebook": os.path.join(base_path, "fb.txt"),
            "instagram": [
                os.path.join(base_path, "insta1.txt"),
                os.path.join(base_path, "insta2.txt"),
            ],
            "x": os.path.join(base_path, "x.txt"),
            "youtube": os.path.join(base_path, "yt1.txt"),
        }

        # Validate and create cycles for multi-cookie platforms
        self.available_instagram_cookies = [
            p for p in self.cookie_files["instagram"] if Path(p).exists()
        ]

        if not self.available_instagram_cookies:
            raise FileNotFoundError("⚠️ No Instagram cookie files found!")

        self.insta_cycle = itertools.cycle(self.available_instagram_cookies)

    # ---- Methods ----
    def get_facebook_cookie(self) -> str:
        path = self.cookie_files["facebook"]
        if Path(path).exists():
            return path
        raise FileNotFoundError("⚠️ Facebook cookie missing!")

    def get_next_instagram_cookie(self) -> str:
        return next(self.insta_cycle)

    def get_x_cookie(self) -> str:
        path = self.cookie_files["x"]
        if Path(path).exists():
            return path
        raise FileNotFoundError("⚠️ X cookie missing!")

    def get_youtube_cookie(self) -> str:
        path = self.cookie_files["youtube"]
        if Path(path).exists():
            return path
        raise FileNotFoundError("⚠️ YouTube cookie missing!")
    
class Cache:

    def __init__(self):
        self.cache_file = "./video_cache.json"
        self.cache_ttl = 1 * 3600
        self.cache = {}

        # Load cache at startup
        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r", encoding="utf-8") as f:
                try:
                    self.cache = json.load(f)
                except json.JSONDecodeError:
                    self.cache = {}
        else:
            self.cache = {}

    def get_cached_info(self , key: str) -> Optional[Any]:
        
        entry = self.cache.get(key)
        if not entry:
            return None

        timestamp = entry.get("timestamp", 0)
        if time.time() - timestamp > self.cache_ttl:
            # Expired
            self.cache.pop(key, None)
            self.save_cache()
            return None

        return entry.get("data")

    def set_cached_info(self , key: str, data: Any) -> None:
        
        self.cache[key] = {
            "data": data,
            "timestamp": time.time()
        }
        self.save_cache()

    def save_cache(self) -> None:
    
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[Cache] Failed to save cache: {e}", platform="CACHE")

    def clear_cache(self) -> None:
        self.cache = {}
        self.save_cache()

class TaskManager:
    
    @staticmethod
    def get_random_headers():
        headers = {
            "user-agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": random.choice(REFERERS),
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }
        return headers

    @staticmethod
    def encrypt_task_data(data: dict) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(
            fernet.encrypt(zlib.compress(raw))
        ).decode()

    @staticmethod
    def decrypt_task_data(token: str) -> dict:
        raw = zlib.decompress(
            fernet.decrypt(base64.urlsafe_b64decode(token))
        )
        return json.loads(raw.decode())
    
    @staticmethod
    def sanitize_filename(name:str) -> str:
        name = re.sub(r'[\\/*?:"<>|]', "_", name)
        return name.encode("ascii", errors="ignore").decode()

    @staticmethod
    def sizeof_fmt(num, suffix="B"):
        for unit in ["", "K", "M", "G", "T", "P"]:
            if abs(num) < 1024:
                return f"{num:.2f} {unit}{suffix}"
            num /= 1024
        return f"{num:.2f} P{suffix}"
    
    @staticmethod
    def resolve_url(url: str, timeout: int = 10) -> str:
        
        try:

            session = requests.Session()  # reuse session for efficiency
            resp = session.head(url, allow_redirects=True, timeout=timeout)
            return resp.url
        except requests.RequestException:
            return url

class EXTRACTER:

    @staticmethod
    def Yt_dlp_extract(url: str, cookies: str | None = None):
        try:
            ydl_opts = {
                "quiet": True,
                "nocheckcertificate": True,
                "ignoreerrors": True,
                "verbose": False,
                "skip_download": True,
                "format": "bestvideo+bestaudio/best",
                "socket_timeout": 10,
                "source_address": "0.0.0.0",  # force IPv4
                "retries": 3,
            }

            if cookies:
                ydl_opts["cookiefile"] = cookies

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            return info
        except Exception as e:
            logger.error(f"❌ yt-dlp error: {e}", platform="YTDLP")
            return None

    @staticmethod
    def Gallery_dl_extracter(url: str, cookies_path: str):

        if cookies_path and os.path.exists(cookies_path):
            gallery_dl.config.set(("extractor", ), "cookies", cookies_path)
        else:
            raise FileNotFoundError(f"Cookies file not found: {cookies_path}")
        
        gallery_dl.config.set(("core",), "quiet", True)
        gallery_dl.config.set(("output",), "mode", "none")
    
        f = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = f, f

        try:
            job = gallery_dl.job.DataJob(url)
            job.run()

        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
        if not job.data:
            raise RuntimeError("gallery-dl returned no data. Check cookies or URL.")
        return job.data, url

    def twitter_json_mapper(data):
        """Transform gallery-dl raw output to clean JSON."""
        media_list = []

        for entry in data:
            if isinstance(entry, tuple):
                type_id = entry[0]
                if type_id == 2:
                    content = entry[1]
                    media_url = content.get("url") or content.get("filename")
                elif type_id == 3:
                    media_url = entry[1]
                    content = entry[2]
                else:
                    continue
            elif isinstance(entry, dict):
                content = entry
                media_url = content.get("url") or content.get("filename")
            else:
                continue

            if not media_url:
                continue
            extension = content.get("extension") or media_url.split(".")[-1]
            type = content.get("type") or ("video" if media_url.endswith(".mp4") else "image")
            if type == "photo" or extension == "jpg" or extension == "png":
                audio_url = None
            else:
                audio_url = "https://extracter.zer0spectrum.dpdns.org/extract-audio?video_url=" + urllib.parse.quote(media_url , safe="")
                
            media_list.append({
                "tweet_id": content.get("tweet_id"),
                "username": content.get("user", {}).get("nick") or content.get("author", {}).get("nick"),
                "author_id": content.get("author", {}).get("id"),
                "content": content.get("content"),
                "media_url": media_url,
                "filename": content.get("filename"),
                "type": type,
                "extension": extension,
                "audio_url" : audio_url,
                "width": content.get("width"),
                "height": content.get("height"),
                "followers_count": content.get("user", {}).get("followers_count"),
                "view_count": content.get("view_count"),
                "date": content.get("date").strftime("%Y-%m-%d %H:%M:%S") if isinstance(content.get("date"), datetime) else None
            })

        return media_list
    
    def pinterest_json_mapper(data):
        """
        Maps Pinterest data to a standardized format.
        Handles various input structures including tuples, lists, and dicts.
        """
        media_list = []
        
        # Handle if data is a tuple with list as second element
        if isinstance(data, tuple):
            # Check if there's a list in the tuple
            for item in data:
                if isinstance(item, list):
                    data = item
                    break
        
        # Ensure data is iterable
        if not isinstance(data, (list, tuple)):
            data = [data]
        
        for entry in data:
            media_url = None
            content = {}
            
            # Parse different entry structures
            if isinstance(entry, tuple):
                type_id = entry[0]
                
                if type_id == 2:
                    # Format: (2, {...})
                    content = entry[1] if len(entry) > 1 else {}
                    media_url = content.get("url") or content.get("filename")
                    
                elif type_id == 3:
                    # Format: (3, url, {...})
                    if len(entry) > 1:
                        media_url = entry[1]
                    if len(entry) > 2:
                        content = entry[2]
                else:
                    # Try to find dict in tuple
                    for item in entry:
                        if isinstance(item, dict):
                            content = item
                            break
                        elif isinstance(item, str) and ('http' in item or '.' in item):
                            media_url = item
                            
            elif isinstance(entry, dict):
                content = entry
                media_url = content.get("url") or content.get("filename")
                
            elif isinstance(entry, str):
                # Direct URL string
                media_url = entry
                
            else:
                continue
            
            # Skip if no valid media URL found
            if not media_url:
                # Try to extract from images dict
                images = content.get("images", {})
                if images and isinstance(images, dict):
                    orig = images.get("orig", {})
                    media_url = orig.get("url") if orig else None
            
            if not media_url:
                continue
            
            # Extract board info
            board = content.get("board", {})
            board_owner = board.get("owner", {}) if isinstance(board, dict) else {}
            
            # Extract pinner info
            pinner = content.get("pinner", {})
            origin_pinner = content.get("origin_pinner", {})
            
            # Determine file extension
            extension = content.get("extension")
            if not extension and media_url:
                extension = media_url.split(".")[-1].split("?")[0]
            
            # Determine media type
            media_type = content.get("type")
            if not media_type or media_type == "pin":
                if content.get("is_video") or (media_url and media_url.endswith(".mp4")):
                    media_type = "video"
                else:
                    media_type = "image"
            
            # Extract dimensions
            images = content.get("images", {})
            orig_image = images.get("orig", {}) if isinstance(images, dict) else {}
            width = content.get("width") or orig_image.get("width")
            height = content.get("height") or orig_image.get("height")
            
            # Extract author ID (try multiple fields)
            author_id = (
                content.get("author", {}).get("id") or
                pinner.get("id") or
                origin_pinner.get("id")
            )
            
            # Build media object
            media_obj = {
                "title": content.get("seo_title") or content.get("grid_title") or content.get("title"),
                "author_id": author_id,
                "seo_description": content.get("seo_description") or content.get("description"),
                "media_url": media_url,
                "filename": content.get("filename") or media_url.split("/")[-1].split("?")[0],
                "type": media_type,
                "extension": extension,
                "repin_count": content.get("repin_count", 0),
                "reaction_count": content.get("reaction_counts", {}).get("1", 0),
                "width": width,
                "height": height,
                "followers_count": board_owner.get("follower_count", 0),
                "share_count": content.get("share_count", 0),
                "pin_id": content.get("id"),
                "created_at": content.get("created_at"),
                "dominant_color": content.get("dominant_color"),
            }
            
            media_list.append(media_obj)
        
        return media_list

    @staticmethod
    def download_video_m3u8(video_url: str, cookies: str | None = None):
        temp_path = tempfile.mktemp(suffix=".mp4")

        cmd = [
            "yt-dlp",
            video_url,
            "--no-playlist",
            "--merge-output-format", "mp4",
            "--output", temp_path,
            "--quiet",
            "--concurrent-fragments", "10",  # download 10 segments in parallel
            "--downloader", "aria2c",        # use ffmpeg (fast + stable)
            "--http-chunk-size", "10M",      # chunked fast download
            "--retries", "10",               # auto retry failed chunks
            "--fragment-retries", "20",
            "--no-cache-dir",
            "--geo-bypass",
            "--no-warnings",
        ]

        if cookies:
            cmd.extend(["--cookies", cookies])
            
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
          
            process.wait()

            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, cmd)

            if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                logger.error("❌ No video file created." , platform)
                return None

            return temp_path

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ yt-dlp failed with code {e.returncode}" , platform)
            return None
        except Exception as e:
            logger.error(f"⚠️ Unexpected error: {e}" , platform)
            return None
        
    @staticmethod
    def download_audio(video_url: str, cookies: str | None = None):
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a")
        temp_path = temp_file.name
        temp_file.close()

        if os.path.exists(temp_path):
            os.remove(temp_path)
        cmd = [
            "yt-dlp",
            video_url,
            "--extract-audio",
            "--no-playlist",
            "--quiet",
            "--audio-format", "m4a",
            "--output", temp_path,
        ]

        if cookies and "youtube.com" in video_url:
            cmd += ["--cookies", cookies]


        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            
            process.wait()

            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, cmd)

            if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                logger.error("❌ No audio file created." ,platform)
                return None

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ yt-dlp failed: {e}")
            return None
        except Exception as e:
            logger.error(f"⚠️ Unexpected error: {e}", platform=platform)
            return None
    
        return temp_path

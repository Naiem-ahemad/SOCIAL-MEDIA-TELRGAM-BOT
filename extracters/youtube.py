from core.utils import TaskManager , Cache , CookieManager, logger
import yt_dlp , urllib , random , re , time , os , tempfile , subprocess , json

cookie_manager = CookieManager()
key = os.getenv("API_KEY")

platform = "youtube"

USER_AGENTS = [
    # Windows Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",

    # Windows Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    
    # MacOS Chrome
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

MAX_FREE_SIZE = 1 * 1024 * 1024 * 1024 * 1024
url_tasks: dict[str, dict] = {}


def list_qualities(url):

    cache_manager = Cache()
    # Check cache first
    cached = cache_manager.get_cached_info(url)
    if cached:
        logger.debug("Cache hit", platform=platform)
        return cached

    def extract_and_cache(url: str):
        try:
            
            # Optimized yt-dlp options for speed
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "socket_timeout": 15,
                "retries": 1,
                "ignoreerrors": True,
                "writeautomaticsub": False,
                "writethumbnail": False,
                "http_headers": {
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept-Language": "en-US,en;q=0.9",
                },
                # "cookiefile": cookies_file
            }

            # --- Extract info quickly ---
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            formats = info.get("formats", [])

            # --- AUDIO ---
            audio_formats = [
                f for f in formats
                if f.get("acodec") != "none" and f.get("vcodec") == "none"
            ]

            unique_audios = {}
            for f in sorted(audio_formats, key=lambda x: x.get("abr", 0), reverse=True):
                ext = f.get("ext")
                if ext not in unique_audios and len(unique_audios) < 3:
                    unique_audios[ext] = f

            audios = []
            for f in unique_audios.values():
                filesize = f.get("filesize") or f.get("filesize_approx") or 0
                audio_url_encoded = urllib.parse.quote_plus(f["url"])
                safe_audio_title = re.sub(r'[\\/*?:"<>]', "-", info["title"]).replace(" ", "-")

                audios.append({
                    "quality": f"Audio {f.get('abr', 0)}kbps",
                    "ext": f["ext"],
                    "size": TaskManager.sizeof_fmt(filesize),
                    "streaming_url": f["url"],
                    "downloading_url": f"/download-audio?url={audio_url_encoded}&title={safe_audio_title}&key={key}",
                    "raw_size": filesize,
                    "abr": f.get("abr", 0)
                })

            # --- VIDEO ---
            grouped = {}
            for f in formats:
                if not f.get("height") or f.get("vcodec") == "none":
                    continue

                h = f["height"]
                acodec = f.get("acodec")
                audio_channels = int(f.get("audio_channels") or 0)
                has_audio = acodec and acodec != "none" and audio_channels > 0

                # ✅ Prefer merged HLS first
                if f.get("protocol") == "m3u8_native" and has_audio:
                    grouped[h] = {"format": f, "has_audio": True}
                    continue

                # ✅ Fallback: HTTPS merged (progressive MP4)
                if f.get("protocol") == "https" and has_audio and h not in grouped:
                    grouped[h] = {"format": f, "has_audio": True}
                    continue
                    
                # ❌ Video-only fallback (for merging later)
                if h not in grouped:
                    grouped[h] = {"format": f, "has_audio": False}

            sorted_heights = sorted(grouped.keys())

            qualities = []
            # print("FORMATS : " , formats)
            for h in sorted_heights:
                # print("GROUP : " , grouped)
                f = grouped[h]["format"]
                acodec = f.get("acodec")
                has_audio = acodec != "none"
                logger.debug("Format has_audio", platform=platform)
                video_size = (f.get("tbr", 0) * 1000 / 8) * (info.get("duration", 0))
                video_url = f.get("url")
                if not video_url:
                    continue
                
                safe_title = re.sub(r'[\\/*?:"<>|]', "-", info["title"]).replace(" ", "-")
                
                if has_audio:
                    logger.debug("Has audio", platform=platform)
                    encoded_url = urllib.parse.quote_plus(video_url)
                    qualities.append({
                        "quality": f"{h}p",
                        "size": TaskManager.sizeof_fmt(video_size),
                        "streaming_url": video_url,
                        "download_url": f"/download?url={encoded_url}&title={safe_title}&key={key}",
                        "premium": video_size > MAX_FREE_SIZE
                    })
                else:
                    valid_audios = [
                        a for a in formats if a.get("acodec") != "none" and a.get("abr")
                    ]
                    best_audio = max(valid_audios, key=lambda a: a.get("abr", 0), default=None)
                    if not best_audio:
                        continue

                    total_size = video_size + (best_audio.get("filesize") or 0)
                    task_data = {
                        "url": url,
                        "quality": f.get("format_note"),
                        "format_id": f.get("format_id"),
                        "title": safe_title,
                        "timestamp": time.time(),
                    }
                    task_id = TaskManager.encrypt_task_data(task_data)

                    qualities.append({
                        "quality": f"{h}p",
                        "size": TaskManager.sizeof_fmt(total_size),
                        "progress_url": f"/progress/{task_id}?key={key}"
                    })

            result = {
                "title": info.get("title", "Unknown"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"),
                "audio_only": False,
                "qualities": qualities,
                "audios": audios
            }

            # Cache the result
            cache_manager.set_cached_info(url, result)
            logger.info("Extracted data", platform=platform)
            return result

        except Exception:
            logger.error("Error extracting data", platform=platform)
            return None
        
    return extract_and_cache(url)

def youtube_short_extracter(video_url):
     
    # Create temp path (without extension; yt-dlp will add .mp4)
    temp_file = tempfile.NamedTemporaryFile(delete=False , suffix="")
    temp_path = temp_file.name
    temp_file.close()

    # Get cookies
    cookies_file = cookie_manager.get_youtube_cookie()

    # yt-dlp command
    cmd = [
        "yt-dlp",
        video_url,
        "--cookies", cookies_file,
        "--output", f"{temp_path}.%(ext)s",
        "-f", "bestvideo[height>=720][height<=1080]+bestaudio/best[height>=720][height<=1080]",
        "--merge-output-format", "mp4",
        "--print-json"
    ]

    logger.info("youtube_short_extracter called", platform=platform)
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
                
        if result.returncode != 0:
            logger.error("Error downloading video", platform=platform)
            return None

        # Parse JSON output from yt-dlp
        data = json.loads(result.stdout.strip())

        final_path = temp_path + ".mp4"
        return final_path, data

    except Exception:
        logger.error("Exception in youtube_short_extracter", platform=platform)
        return None

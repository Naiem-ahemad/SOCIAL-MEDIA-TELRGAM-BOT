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

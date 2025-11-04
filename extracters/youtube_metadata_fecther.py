""" YouTube Metadata Fetcher - Simplified """
import requests
from core.utils import logger , CookieManager
import re , json
from http.cookiejar import MozillaCookieJar
import time
import hashlib

cookies_manager = CookieManager()
cookies_path = cookies_manager.get_youtube_cookie()

if cookies_path.name == "yt1.txt":
    cookies_path = cookies_path.with_name("yt.txt")

if not cookies_path.exists():
    cookies_path = cookies_manager.get_youtube_cookie()

def fetch_youtube_metadata(video_url_or_id, cookies_path=cookies_path):

    """
    Fetch YouTube video metadata with formats
    
    Args:
        video_url_or_id: YouTube video URL or video ID
        cookies_path: Path to Netscape format cookies file (optional)
    
    Returns:
        dict: Metadata including formats, or error dict if failed
    """
    
    # Extract video ID from URL if needed
    video_id = _extract_video_id(video_url_or_id)

    if not video_id:
        return {'success': False, 'error': 'Invalid video URL or ID'}
    
    # Setup session
    session = requests.Session()
    session.headers.update({'Accept-Encoding': 'identity'})
    
    # Load cookies if provided
    if cookies_path:
        _load_cookies(session, cookies_path)

    else:
        logger.warning("No cookies Found..." , platform="Youtube")

    # Fetch metadata
    metadata = _fetch_metadata(session, video_id)
    print("METADATA : " , metadata)
    return metadata

def _extract_video_id(url_or_id):
    """Extract video ID from URL or return as-is if already an ID"""
    if len(url_or_id) == 11 and url_or_id.isalnum():
        return url_or_id
    
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:watch\?v=)([0-9A-Za-z_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    
    return None


def _load_cookies(session, cookie_file):
    """Load cookies from Netscape format file"""
    try:
        cookie_jar = MozillaCookieJar(cookie_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        for cookie in cookie_jar:
            session.cookies.set_cookie(cookie)
    except Exception as e:

        logger.warning(f"_load_cookies: cookiejar load failed: {e}", platform="YTFETCH")
        # Try manual parsing
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        session.cookies.set(parts[5], parts[6])
        except Exception as e:
            logger.warning(f"_load_cookies: manual cookie parse failed: {e}", platform="YTFETCH")


def _generate_sapisidhash(session):
    """Generate SAPISIDHASH header for authentication"""
    try:
        sapisid = session.cookies.get('SAPISID') or session.cookies.get('__Secure-3PAPISID')
        if not sapisid:
            return None
        
        timestamp = str(int(time.time()))
        origin = "https://www.youtube.com"
        hash_input = f"{timestamp} {sapisid} {origin}"
        hash_digest = hashlib.sha1(hash_input.encode()).hexdigest()
        
        return f"SAPISIDHASH {timestamp}_{hash_digest}"
    except Exception as e:
        logger.debug(f"_generate_sapisidhash failed: {e}", platform="YTFETCH")
        return None


def _fetch_metadata(session, video_id):
    """Fetch video metadata via YouTube API"""
    api_url = "https://www.youtube.com/youtubei/v1/player"
    api_key = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
    
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20241027.01.00",
                "hl": "en",
                "gl": "US"
            }
        },
        "videoId": video_id,
        "contentCheckOk": True,
        "racyCheckOk": True
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Origin": "https://www.youtube.com",
        "Referer": f"https://www.youtube.com/watch?v={video_id}",
        "X-Youtube-Client-Name": "1",
        "X-Youtube-Client-Version": "2.20241027.01.00",
        "X-Origin": "https://www.youtube.com",
    }
    
    # Add SAPISIDHASH if we have auth cookies
    sapisidhash = _generate_sapisidhash(session)
    if sapisidhash:
        headers["Authorization"] = sapisidhash
    
    try:
        response = session.post(
            f"{api_url}?key={api_key}",
            json=payload,
            headers=headers,
            timeout=15
        )
        
        if response.status_code != 200:
            return {
                'success': False,
                'error': f'HTTP {response.status_code}',
                'response': response.text[:500]
            }
        

        with open("test.json" , "w" , encoding="utf-8") as f:
            json.dump(response.json() , f , indent=2 , ensure_ascii=False)

        return _parse_response(response.json())
        
    except Exception as e:
        return {'success': False, 'error': str(e)}


def _parse_response(data):
    playability = data.get("playabilityStatus", {})
    if playability.get("status") != "OK":
        return {
            "success": False,
            "error": playability.get("reason", "Unknown"),
            "status": playability.get("status"),
        }

    video_details = data.get("videoDetails", {})
    microformat = data.get("microformat", {}).get("playerMicroformatRenderer", {})
    duration = int(video_details.get("lengthSeconds", 0)) or 1

    streaming = data.get("streamingData", {})
    formats = streaming.get("formats", []) + streaming.get("adaptiveFormats", [])

    processed_formats = []
    for fmt in formats:
        itag = fmt.get("itag")
        quality = fmt.get("qualityLabel") or fmt.get("quality")
        url = fmt.get("url")
        has_audio = "audio" in fmt.get("mimeType", "").lower()
        has_video = "video" in fmt.get("mimeType", "").lower()
        size = fmt.get("contentLength")

        if not itag or not quality:
            continue

        processed_formats.append({
            "itag": itag,
            "quality": quality,
            "bitrate": fmt.get("bitrate"),
            "size": int(size) if size else None,
            "has_audio": has_audio,
            "has_video": has_video,
            "url": url,
        })

    # --- Deduplicate videos: prefer one with size, else highest bitrate ---
    video_formats = [f for f in processed_formats if f["has_video"]]
    audio_formats = [f for f in processed_formats if f["has_audio"]]

    unique_videos = {}
    for v in video_formats:
        q = v["quality"]
        if q not in unique_videos:
            unique_videos[q] = v
        else:
            old = unique_videos[q]
            # ✅ prefer the one that has a size, else higher bitrate
            if not old["size"] and v["size"]:
                unique_videos[q] = v
            elif (v["bitrate"] or 0) > (old["bitrate"] or 0):
                unique_videos[q] = v

    video_formats = list(unique_videos.values())

    # --- Best audio ---
    if audio_formats:
        audio_formats = [max(audio_formats, key=lambda x: x["bitrate"] or 0)]

    metadata = {
        "success": True,
        "video_id": video_details.get("videoId"),
        "title": video_details.get("title"),
        "author": video_details.get("author"),
        "channel_id": video_details.get("channelId"),
        "duration": duration,
        "view_count": int(video_details.get("viewCount", 0)),
        "rating": video_details.get("averageRating"),
        "description": video_details.get("shortDescription", ""),
        "keywords": video_details.get("keywords", []),
        "category": microformat.get("category"),
        "publish_date": microformat.get("publishDate"),
        "upload_date": microformat.get("uploadDate"),
        "is_live": video_details.get("isLiveContent", False),
        "is_private": video_details.get("isPrivate", False),
        "thumbnail": video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url"),
        "video_formats": sorted(video_formats, key=lambda x: int(x["bitrate"] or 0), reverse=True),
        "audio_formats": audio_formats,
    }

    return metadata

# print(json.dumps(fetch_youtube_metadata("XXRLUHC6lBg" , cookies_path="./cookies/yt1.txt")))

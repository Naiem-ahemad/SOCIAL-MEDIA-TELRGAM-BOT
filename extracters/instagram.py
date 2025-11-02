import re
import json
from jsonpath_ng import parse
from bs4 import BeautifulSoup
from glom import glom, PathAccessError
from playwright.async_api import async_playwright
from core.utils import TaskManager
import asyncio

platform = "instagram"

async def open_facebook_url_async(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(extra_http_headers=TaskManager.get_random_headers())
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        html = await page.content()
        await browser.close()
        return html

def deep_find(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                return v
            found = deep_find(v, key)
            if found:
                return found
    elif isinstance(obj, list):
        for i in obj:
            found = deep_find(i, key)
            if found:
                return found
    return None

def deep_find_all(obj, key):
    """Find ALL occurrences of a key recursively (returns list of all matches)."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                results.append(v)
            results.extend(deep_find_all(v, key))
    elif isinstance(obj, list):
        for i in obj:
            results.extend(deep_find_all(i, key))
    return results

def extract_instagram_post_json_from_file(html):

    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", {"type": "application/json"})

    # --- Try to find all valid JSON blocks
    all_candidates = []
    for idx, s in enumerate(scripts, 1):
        raw = s.string
        if not raw:
            continue

        raw = raw.strip()
        raw = re.sub(r",\s*([\]}])", r"\1", raw)  # fix trailing commas

        try:
            data = json.loads(raw)
        except Exception:
            continue

        # find all possible media roots
        all_items = (
            deep_find_all(data, "xdt_api__v1__media__shortcode__web_info")
            + deep_find_all(data, "items")
            + deep_find_all(data, "graphql")
        )

        for item in all_items:
            if isinstance(item, dict) and ("media_type" in str(item) or "carousel_media" in str(item)):
                all_candidates.append(item)

    if not all_candidates:
        raise Exception("❌ Could not find valid post JSON block in HTML file")

    # return the largest one (most complete data)
    best = max(all_candidates, key=lambda x: len(json.dumps(x)))

    with open("test.json" , "w" , encoding="utf-8") as f:
        json.dump(best , f , indent=2 , ensure_ascii=False)

    return best

def extract_instagram_reel_json_from_file(html):
 
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", {"type": "application/json"})

    all_reels = []

    def collect_reel_data(obj):
        """Recursively find Instagram Reels data blocks"""
        if isinstance(obj, dict):
            # Detect Reels
            if (
                "all_video_dash_prefetch_representations" in str(obj)
                or "clips_metadata" in obj
                or "video_dash_manifest" in obj
                or "playback_duration_secs" in obj
                or ("representation_id" in str(obj) and "mime_type" in str(obj))
            ):
                all_reels.append(obj)

            for v in obj.values():
                collect_reel_data(v)

        elif isinstance(obj, list):
            for i in obj:
                collect_reel_data(i)

    for idx, s in enumerate(scripts, 1):
        raw = s.string
        if not raw:
            continue

        raw = raw.strip()
        raw = re.sub(r",\s*([\]}])", r"\1", raw)

        try:
            data = json.loads(raw)
        except Exception:
            continue

        collect_reel_data(data)

    if not all_reels:
        raise Exception("❌ Could not find any valid Reel JSON block")


    best = max(all_reels, key=lambda x: len(json.dumps(x)))

    return best


def deep_find_all_media(obj, caption=None):
    """Recursively find all image/video URLs in any JSON structure."""
    results = []

    if isinstance(obj, dict):
        # extract caption text
        if not caption and "caption" in obj:
            cap = obj["caption"]
            if isinstance(cap, dict) and "text" in cap:
                caption = cap["text"]

        # find image
        if "image_versions2" in obj and "candidates" in obj["image_versions2"]:
            url = obj["image_versions2"]["candidates"][0].get("url")
            if url:
                results.append({"type": "image", "url": url, "caption": caption})

        # find video
        if "video_versions" in obj and isinstance(obj["video_versions"], list):
            for vid in obj["video_versions"]:
                if "url" in vid:
                    results.append({"type": "video", "url": vid["url"], "caption": caption})

        # recursive search
        for v in obj.values():
            results.extend(deep_find_all_media(v, caption))

    elif isinstance(obj, list):
        for i in obj:
            results.extend(deep_find_all_media(i, caption))

    return results


def extract_instagram_post_data(data):
    """
    ⚡ Fully robust Instagram post extractor.
    Uses:
      - glom for safe structured lookups
      - jsonpath-ng for auto fallback scans
    """
    results = []

    # 1️⃣ Try main known structures safely
    try:
        # handle xdt or graphql or items
        media_data = (
            glom(data, "xdt_api__v1__media__shortcode__web_info.items", default=None)
            or glom(data, "graphql.shortcode_media", default=None)
            or glom(data, "items", default=None)
            or data
        )
    except PathAccessError:
        media_data = data

    # 2️⃣ Deep recursive parsing (base safety)
    results = deep_find_all_media(media_data)

    # 3️⃣ If still empty, fallback to jsonpath-ng auto scan
    if not results:
        paths = [
            "$..carousel_media[*]",
            "$..image_versions2.candidates[*].url",
            "$..video_versions[*].url",
            "$..caption.text",
        ]
        urls = set()
        caption = None

        for path in paths:
            matches = [m.value for m in parse(path).find(data)]
            if "caption.text" in path and matches:
                caption = matches[0]
            for m in matches:
                if isinstance(m, str) and m.startswith("http") and m not in urls:
                    urls.add(m)

        # classify video/image based on URL pattern
        for u in urls:
            results.append({
                "type": "video" if ".mp4" in u or "video" in u else "image",
                "url": u,
                "caption": caption
            })

    # 4️⃣ Deduplicate
    unique = []
    seen = set()
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return unique

def parse_reel_metadata(data: dict):
    """🎬 Extracts Instagram Reel metadata with only HD(1080p) and SD(720p) URLs."""

    # --- 1️⃣ Extract main info ---
    root = deep_find(data, "xdt_api__v1__media__shortcode__web_info") or deep_find(data, "items")
    if not root:
        raise Exception("❌ No Reel media info found")

    reel = root.get("items", [{}])[0] if isinstance(root.get("items"), list) else root

    caption_data = deep_find(reel, "caption") or {}
    caption_text = caption_data.get("text") if isinstance(caption_data, dict) else caption_data
    like_count = deep_find(reel, "like_count") or 0
    comment_count = deep_find(reel, "comment_count") or 0

    # 🎵 Audio metadata
    music_info = deep_find(reel, "music_asset_info") or {}
    audio_meta = {
        "title": music_info.get("title"),
        "artist": music_info.get("display_artist"),
        "audio_id": music_info.get("audio_cluster_id"),
        "is_explicit": music_info.get("is_explicit", False),
    }

    # --- 2️⃣ Extract video/audio URLs ---
    extensions = deep_find(data, "extensions") or {}
    video_urls, audio_urls = [], []

    dash_data = deep_find(extensions, "all_video_dash_prefetch_representations")
    if isinstance(dash_data, list):
        for item in dash_data:
            for rep in item.get("representations", []):
                base_url = rep.get("base_url")
                mime = rep.get("mime_type", "")
                if not base_url:
                    continue
                if "video" in mime:
                    video_urls.append(base_url)
                elif "audio" in mime:
                    audio_urls.append(base_url)

    # --- 3️⃣ Pick only 1080p and 720p URLs ---
    selected_videos = {"1080p": None, "720p": None}
    for url in video_urls:
        if "q9" in url or "1080" in url:
            selected_videos["1080p"] = url
        elif "q7" in url or "720" in url:
            selected_videos["720p"] = url

    # --- 4️⃣ Final clean output ---
    return {
        "caption": caption_text,
        "like_count": like_count,
        "comment_count": comment_count,
        "audio_meta": audio_meta,
        "audio_urls": audio_urls[:1] if audio_urls else []
    }

class INSTAGRAM_EXTRACTER:

    @staticmethod
    async def extract_instagram_auto(url):
        """
        🧩 Automatically detect and parse Instagram Reel / Post / Carousel data.
        """
        if "reels" in url:
            url = url.replace("reels" , "reel")
            
        html = await open_facebook_url_async(url)
        # Try reel first
        data = extract_instagram_reel_json_from_file(html)
        is_reel = bool(data and "xdt_api__v1__media__shortcode__web_info" in str(data))
        user = deep_find(data, "user")

        # Fallback if not reel
        if is_reel:
            info = parse_reel_metadata(data)
            parsed_media = extract_instagram_post_data(data)

            video = next((m for m in parsed_media if m["type"] == "video"), None)
            thumbnail = next((m for m in parsed_media if m["type"] == "image"), None)

            # invalid reel → fallback to post
            if not video and not info.get("audio_urls"):
                is_reel = False

        if not is_reel:
            data = extract_instagram_post_json_from_file(html)
            media = extract_instagram_post_data(data)

            # handle carousel
            if len(media) > 1:
                post_type = "carousel"
            else:
                post_type = "post"

            # extract caption only once
            first_caption = None
            for m in media:
                if not first_caption and "caption" in m:
                    first_caption = m["caption"]
                m.pop("caption", None)

            title = f"Post by {user.get("username")}"
            final = {
                "type": post_type,
                "title": title,
                "description":first_caption,
                "like_count": info.get("like_count"),
                "comment_count": info.get("comment_count"),
                "media": media
            }
            return final

        # clean data
        if video:
            video.pop("caption", None)
        if thumbnail:
            thumbnail.pop("caption", None)

        audio_meta = info.get("audio_meta")
        if not audio_meta or not any(audio_meta.values()):
            audio_meta = None
        title = f"Video by {user.get("username")}"
        final = {
            "type": "reel",
            "title": title,
            "description":info.get("caption"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "thumbnail": thumbnail.get("url") if thumbnail else None,
            "video": video.get("url") if video else None,
        }

        if audio_meta:
            final["audio_meta"] = audio_meta
        if info.get("audio_urls"):
            final["audio_urls"] = info["audio_urls"]

        return final

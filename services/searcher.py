import requests, re, json, urllib.parse, random, time
from telegram import InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, InlineQueryHandler
from uuid import uuid4
from core.utils import logger

platform = "searcher"

def youtube_search_stable(query, limit=10):
    logger.debug(f"[SEARCH] Query received: {query}", platform=platform)
    q = urllib.parse.quote(query)
    url = f"https://www.youtube.com/results?search_query={q}"

    headers = {
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        ])
    }

    try:
        html = requests.get(url, headers=headers, timeout=10).text
    except Exception as e:
        logger.error(f"❌ Network error: {e}", platform=platform)
        return []

    data = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html, re.S)
    if not data:
        logger.warning("⚠️ No ytInitialData found in HTML", platform=platform)
        return []

    try:
        json_data = json.loads(data.group(1))
    except Exception as e:
        logger.error(f"❌ JSON parse error: {e}", platform=platform)
        return []

    results = []
    try:
        sections = json_data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"]
        for section in sections:
            contents = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in contents:
                video = item.get("videoRenderer")
                if not video:
                    continue

                title = video["title"]["runs"][0]["text"]
                vid = video["videoId"]
                thumb = video["thumbnail"]["thumbnails"][-1]["url"]

                # Extra details
                channel = video.get("ownerText", {}).get("runs", [{}])[0].get("text", "Unknown")
                views = video.get("viewCountText", {}).get("simpleText", "N/A")
                published = video.get("publishedTimeText", {}).get("simpleText", "N/A")

                results.append({
                    "title": title,
                    "video_id": vid,
                    "thumb": thumb,
                    "channel": channel,
                    "views": views,
                    "published": published
                })

                if len(results) >= limit:
                    logger.info(f"✅ Found {len(results)} results", platform=platform)
                    logger.debug(f"Search results: {results}", platform=platform)
                    return results
                
    except Exception as e:
        logger.error(f"❌ Parse error: {e}", platform=platform)

    logger.warning(f"⚠️ No results parsed for query: {query}", platform=platform)
    return results

def short_number(num):
    if isinstance(num, str):
        num = num.replace(",", "").split()[0]  # remove commas and "views"
        try:
            num = int(num)
        except ValueError:
            return num  # if it fails, just return original string

    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)

async def inline_search(update, context):
    query = update.inline_query.query.strip()
    logger.debug(f"[INLINE] Inline query triggered: '{query}'", platform=platform)  # 🧠 log incoming query

    if not query:
        logger.warning("⚠️ Empty query", platform=platform)
        return

    videos = youtube_search_stable(query, limit=50)
    if not videos:
        logger.warning("⚠️ No results to send back to Telegram", platform=platform)
        return

    results = [
        InlineQueryResultArticle(
            id=str(uuid4()),
            title=v["title"],
            description=f"{v['channel']} • {short_number(v['views'])} • {v['published']}",
            thumbnail_url=v["thumb"],
            input_message_content=InputTextMessageContent(
                f"🎬 <b>{v['title']}</b>\n👤 {v['channel']}\n👁 {v['views']} • {v['published']}\n🔗 https://youtu.be/{v['video_id']}",
                parse_mode="HTML"
            ),
        )
        for v in videos
    ]

    await update.inline_query.answer(results, cache_time=0, is_personal=True)
    logger.info(f"✅ Sent {len(results)} results to Telegram", platform=platform)
    
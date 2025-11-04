import requests, re, json, urllib.parse, random , time
from telegram import InlineQueryResultArticle, InputTextMessageContent
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

                channel = video.get("ownerText", {}).get("runs", [{}])[0].get("text", "Unknown")
                views = video.get("viewCountText", {}).get("simpleText", "N/A")
                published = video.get("publishedTimeText", {}).get("simpleText", "N/A")

                # ✅ Accurate Shorts detection using commandMetadata
                web_type = (
                    video.get("navigationEndpoint", {})
                         .get("commandMetadata", {})
                         .get("webCommandMetadata", {})
                         .get("webPageType", "")
                )
                is_shorts = web_type == "WEB_PAGE_TYPE_SHORTS"

                if is_shorts:
                    url = f"https://www.youtube.com/shorts/{vid}"
                else:
                    url = f"https://www.youtube.com/watch?v={vid}"

                results.append({
                    "title": title,
                    "video_url": url,
                    "thumb": thumb,
                    "channel": channel,
                    "views": views,
                    "published": published,
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
    
    if "pin" in query.lower():
        logger.debug("Skipping YouTube search for Pinterest query", platform=platform)
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
                f"🎬 <b>{v['title']}</b>\n👤 {v['channel']}\n👁 {v['views']} • {v['published']}\n🔗 {v['video_url']}",
                parse_mode="HTML"
            ),
        )
        for v in videos
    ]

    await update.inline_query.answer(results, cache_time=0, is_personal=True)
    logger.info(f"✅ Sent {len(results)} results to Telegram", platform=platform)
    

def pinterest_guest_session():
    r = requests.get("https://www.pinterest.com/", headers={"User-Agent": "Mozilla/5.0"})
    cookies = r.cookies.get_dict()
    return cookies, cookies.get("csrftoken")

def pinterest_api_search(query, pages=1, page_size=50):
    cookies, csrf = pinterest_guest_session()
    url = "https://www.pinterest.com/resource/BaseSearchResource/get/"
    headers = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.pinterest.com",
        "referer": "https://www.pinterest.com/",
        "user-agent": "Mozilla/5.0",
        "x-csrftoken": csrf,
    }
    sess_cookies = {"_pinterest_sess": cookies.get("_pinterest_sess"), "csrftoken": csrf}

    results, bookmark = [], None
    for _ in range(pages):
        payload = {
            "source_url": f"/search/pins/?q={query}&rs=typed",
            "data": json.dumps({
                "options": {
                    "query": query,
                    "scope": "pins",
                    "page_size": page_size,
                    "bookmarks": [bookmark] if bookmark else [],
                },
                "context": {},
            }),
        }
        r = requests.post(url, headers=headers, cookies=sess_cookies, data=payload)
        r.raise_for_status()
        j = r.json()

        for pin in j["resource_response"]["data"]["results"]:
            pinner = pin.get("pinner", {})
            board = pin.get("board", {})
            owner = board.get("owner", {})
            pin_count = board.get("pin_count")
            follower_count = owner.get("follower_count")
            reactions = pin.get("reaction_counts", {})
            img = pin["images"]["orig"]["url"]
            link = f"https://www.pinterest.com/pin/{pin['id']}/"
            title = pin.get("auto_alt_text") or pin.get("seo_alt_text")
            results.append({"title": title, "image": img, "link": link , "pinner": pinner.get("full_name"), "board": board.get("name"), "pin_count": pin_count, "follower_count": follower_count, "reactions": reactions.get("1")})

        bookmark = j["resource_response"]["data"].get("bookmark")
        if not bookmark:
            break
        time.sleep(1)

    return results

async def inline_query_pin(update, context):

    query = update.inline_query.query.strip()
    logger.debug(f"[INLINE] Pinterest query received: '{query}'", platform=platform)

    if not query:
        await update.inline_query.answer([], switch_pm_text="Search Pinterest 🔍", switch_pm_parameter="start")
        return

    pins = pinterest_api_search(query)
    if not pins:
        await update.inline_query.answer([], switch_pm_text="No results found ❌", switch_pm_parameter="none")
        return

    results = [
        InlineQueryResultArticle(
            id=str(uuid4()),
            title=p["title"] or "Untitled Pin",
            description=f"❤️ {p.get('reactions')} | 📌 {p.get('pin_count', 0)} | 👥 {p.get('follower_count', 0)}",
            thumbnail_url=p.get("image"),
            input_message_content=InputTextMessageContent(
                f"📌 <b>{p['title'] or 'Pinterest Pin'}</b>\n"
                f"❤️ {p.get('reactions')} 📌 {p.get('pin_count', 0)}   👥 {p.get('follower_count', 0)}\n"
                f"🔗 {p['link']}",
                parse_mode="HTML"
            ),
        )
        for p in pins
    ]

    await update.inline_query.answer(results, cache_time=0)
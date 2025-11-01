import requests , re , json
from datetime import datetime , timezone
from core.utils import logger

platform = "pinterest"

def pinterest_extracter(url: str):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }

    try:
        r = requests.get(url, headers=headers, timeout=15)
        logger.debug("Requesting Webpage...", platform=platform)
    except Exception:
        logger.error("Failed to request Pinterest page", platform=platform)
        return None

    def format_time_ago(date_str: str) -> str:

        try:
            date_str = date_str.strip()
            published = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %z")
            now = datetime.now(timezone.utc)
            delta = now - published
            days = delta.days
            if days < 1:
                hours = delta.seconds // 3600
                if hours < 1:
                    minutes = delta.seconds // 60
                    return f"{minutes} minutes ago"
                return f"{hours} hours ago"
            elif days < 30:
                return f"{days} days ago"
            elif days < 365:
                months = days // 30
                rem_days = days % 30
                return f"{months} month{'s' if months>1 else ''} {rem_days} day{'s' if rem_days>1 else ''} ago"
            else:
                years = days // 365
                rem_days = days % 365
                return f"{years} year{'s' if years>1 else ''} {rem_days} day{'s' if rem_days>1 else ''} ago"
            
        except Exception:
            logger.error("Error parsing date", platform=platform)
            return None

    def format_likes(num: int) -> str:
        if num >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num/1_000:.1f}k"
        return str(num)

    def merge_pins_from_html(html_content):

        patterns = [
            r'<script[^>]*data-relay-response=["\']true["\'][^>]*>\s*(\{.*?\})\s*</script>',
            r'window\.__PWS_RELAY_REGISTER_COMPLETED_REQUEST__\s*\(\s*[\'"]\{.*?\}[\'"]\s*,\s*(\{.*?\})\s*\)',
        ]

        all_matches = []
        for pattern in patterns:
            all_matches += re.findall(pattern, html_content, re.DOTALL)

        if not all_matches:
            return None

        merged_pin = {
            "pinId": None,
            "title": "",
            "description": "",
            "posted_before": "",
            "isVideo": False,
            "media": {},
            "user": {},
            "repinCount": 0,
            "commentCount": 0,
            "likes_count": 0,
            "tracking_data":{},
            "suggestion":None,
        }

        for match in all_matches:
            try:
                match = match.strip()

                # 🔧 Fix escaped JSON (e.g. "{\"data\":...}")
                if match.startswith('"') or match.startswith("'"):
                    match = match.strip('"\'')
                if match.startswith('{\\'):
                    match = bytes(match, "utf-8").decode("unicode_escape")

                data = json.loads(match)

                logger.debug("json debug sample", platform=platform)

                # if after load it's still stringified JSON → decode again
                if isinstance(data, str):
                    data = json.loads(data)

                response = data.get("response")

                if response:
                    pin_data = response.get("data", {})
                else:
                    pin_data = data.get("data", {})

                if pin_data.get("v3GetPinQuery", {}).get("data", {}):
                    pin_data = pin_data.get("v3GetPinQuery", {}).get("data", {})
                
                if not pin_data:
                    continue
                    
                # Pin ID
                if not merged_pin["pinId"] and pin_data.get("id"):
                    merged_pin["pinId"] = pin_data["id"]

                # Title / Description
                merged_pin["title"] = merged_pin["title"] or pin_data.get("seoTitle") or pin_data.get("title") or ""
                merged_pin["description"] = merged_pin["description"] or pin_data.get("description") or ""
                
                # Date
                raw_date = pin_data.get("createdAt")
                if raw_date:
                    merged_pin["posted_before"] = format_time_ago(raw_date)

                # Video / Story
                videos = pin_data.get("videos")
                story_data = {}
                story_pin = pin_data.get("storyPinData")
                if story_pin:
                    pages = story_pin.get("pages") or []
                    if pages:
                        first_page = pages[0] or {}
                        blocks = first_page.get("blocks") or []
                        if blocks:
                            story_data = blocks[0].get("videoDataV2") or {}

                video_sources = videos or story_data
                is_video_pin = bool(video_sources)

                if is_video_pin:
                    merged_pin["isVideo"] = True
                    video_list = (
                        video_sources.get("videoList") or
                        video_sources.get("videoList720P") or
                        video_sources.get("videoListMobile") or {}
                    )
                    candidates = [
                        "v720P", "vHLSV4", "vEXP3", "vEXP4", "vEXP5", "vEXP6", "vEXP7", "vHLSV3MOBILE"
                    ]
                    video_url, thumbnail, vdata = None, None, {}
                    for key in candidates:
                        vdata = video_list.get(key, {})
                        url = vdata.get("url")
                        if url and url.endswith(".mp4") and not video_url:
                            video_url = url
                        if not thumbnail and vdata.get("thumbnail"):
                            thumbnail = vdata["thumbnail"]

                    duration = video_sources.get("duration") or vdata.get("duration")
                    view_count = video_sources.get("seoViewCount")

                    if video_url:
                        merged_pin["media"]["video"] = video_url
                    if thumbnail:
                        merged_pin["media"]["thumbnail"] = thumbnail
                    if duration:
                        merged_pin["media"]["duration"] = duration
                    if view_count:
                        merged_pin["media"]["viewCount"] = format_likes(int(view_count))

                else:
                    merged_pin["isVideo"] = False
                    hd = pin_data.get("imageSpec_736x", {}).get("url") or pin_data.get("imageSpec_orig", {}).get("url")
                    sd = pin_data.get("imageSpec_236x", {}).get("url")
                    if hd:
                        merged_pin["media"]["hd"] = hd
                    if sd:
                        merged_pin["media"]["sd"] = sd

                # User info (originPinner > pinner)
                user_data = pin_data.get("originPinner") or pin_data.get("pinner") or {}
                if user_data:
                    username = user_data.get("username") or ""
                    full_name = user_data.get("fullName") or ""
                    profile_url = user_data.get("imageMediumUrl") or ""
                    if profile_url:
                        profile_url = profile_url.replace("75x75", "280x280")
                    followers_count = user_data.get("followerCount") or 0

                    merged_pin["user"]["username"] = username
                    merged_pin["user"]["fullName"] = full_name
                    merged_pin["user"]["profileImage"] = profile_url
                    merged_pin["user"]["followerCount"] = format_likes(int(followers_count)) if followers_count else 0
                    if username:
                        merged_pin["user"]["profile_url"] = f"https://pinterest.com/{username.lower()}"

                # Repins (saves)
                repin_count = pin_data.get("aggregatedPinData", {}).get("aggregatedStats", {}).get("saves") or 0
                if repin_count > merged_pin["repinCount"]:
                    merged_pin["repinCount"] = repin_count

                # Comment count
                comment_count = pin_data.get("aggregatedPinData", {}).get("commentCount") or 0
                if comment_count > merged_pin["commentCount"]:
                    merged_pin["commentCount"] = comment_count

                # Likes
                reactions = pin_data.get("reactionCountsData", [])
                likes_count = pin_data.get("totalReactionCount") or 0
                reaction_count_total = sum(r.get("reactionCount", 0) for r in reactions)
                effective_likes = likes_count or reaction_count_total
                if effective_likes > merged_pin["likes_count"]:
                    merged_pin["likes_count"] = effective_likes

                tracking_links  = pin_data.get("link") or pin_data.get("trackedLink")
                domain_data = pin_data.get("linkDomain") or None
                if domain_data:
                    raw_domain_data  = domain_data.get("officialUser")
                    if raw_domain_data:
                        domain = raw_domain_data.get("username") or raw_domain_data.get("fullName") or None
                        if domain:
                            merged_pin["tracking_data"]["domain"] = (domain).upper()
                if tracking_links:
                    merged_pin["tracking_data"]["tracking_url"] = tracking_links

                suggestion_data = pin_data.get("pinJoin" , {}).get("visualAnnotation") or None
                if suggestion_data:
                    if any("wallpaper" in s.lower() or "backgrounds" in s.lower() for s in suggestion_data):
                        merged_pin["suggestion"] = "Wallpaper"
            
            except json.JSONDecodeError:
                logger.error("Error in json parsing", platform=platform)
                continue

        # Final formatting
        merged_pin["repinCount"] = format_likes(merged_pin["repinCount"])
        merged_pin["commentCount"] = format_likes(merged_pin["commentCount"])
        merged_pin["likes_count"] = format_likes(merged_pin["likes_count"]) if merged_pin["likes_count"] else 0

        if not merged_pin:
            logger.error("Merged Pins Not Found", platform=platform)

        return merged_pin


    result = merge_pins_from_html(r.text)

    if not result:
        logger.error("Result Not Found", platform=platform)

    return result


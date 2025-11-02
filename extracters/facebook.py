from core.utils import CookieManager , EXTRACTER , TaskManager , logger
from bs4 import BeautifulSoup
import json  , re , asyncio , requests
from playwright.async_api import async_playwright

platform = "facebook"

async def facebook_post_extracter(url):

    def extract_facebook_media_scripts(html):
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script", {"type": "application/json"})
        matches = []

        targets = [
            "adp_CometPhotoAlbumQueryRelayPreloader_",
            "adp_CometSinglePostDialogContentQueryRelayPreloader_",
            "StoryAttachmentAnimatedImageShareStyleRenderer",
            "GenericAttachmentMedia",
            "CometUFICommentBodyTextWithEntities_textWithEntities"
        ]

        for script in scripts:
            content = script.text.strip()
            if any(t in content for t in targets) and '"media":' in content:
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    continue
                matches.append({
                    "length": script.get("data-content-len"),
                    "json": data
                })

        for i, m in enumerate(matches, 1):
            logger.debug("match found in media scripts", platform=platform)

        if not matches:
            logger.warning("No matching media preloaders found.", platform=platform)
        
        return matches

    async def open_facebook_url_async(page, url):

        logger.debug("Requesting webpage", platform=platform)

        await page.goto(url, wait_until="domcontentloaded")
        html = await page.content()
        images = extract_facebook_media_scripts(html)
        return images, html

    def extract_all_medias(json):
        """
        Load JSON file and return all media blocks found recursively.
        """

        all_medias = []

        def find_media_nodes(data):
            found = []

            def _search(obj):
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if key == "media" and isinstance(value, dict):
                            found.append(value)
                        _search(value)
                elif isinstance(obj, list):
                    for item in obj:
                        _search(item)

            _search(data)
            return found

        # Iterate over each script entry
        for entry in json:
            medias = find_media_nodes(entry["json"])
            all_medias.extend(medias)
            
        if not all_medias:
            logger.error("No media Found..." , platform=platform)

        return all_medias

    def extract_likes_shares_from_matches(matches):
        """
        Recursively search the JSON in matches for reaction and share counts.
        Returns only the first valid likes/shares block.
        """
        result = None

        def _search(obj):
            nonlocal result
            if result:
                return  # stop if we already found one

            if isinstance(obj, dict):
                like_count = obj.get("reaction_count", {}).get("count") if isinstance(obj.get("reaction_count"), dict) else None
                like_i18n = obj.get("i18n_reaction_count")
                share_count = obj.get("share_count", {}).get("count") if isinstance(obj.get("share_count"), dict) else None
                share_i18n = obj.get("i18n_share_count")

                if like_count is not None or share_count  is not None:
                    result = {
                        "likes": like_count,
                        "likes_i18n": like_i18n,
                        "shares": share_count,
                        "shares_i18n": share_i18n,
                    }
                    return

                for v in obj.values():
                    _search(v)

            elif isinstance(obj, list):
                for item in obj:
                    _search(item)

        for match in matches:
            _search(match.get("json", {}))
            if result:
                break
                
        return result

    def extract_media_uris(all_medias):

        uris = []

        for m in all_medias:
            if isinstance(m, dict):
                # Priority: photo_image > viewer_image > image > sticker_image > animated_image
                for key in ["photo_image", "viewer_image", "image", "sticker_image", "animated_image"]:
                    media = m.get(key, {})
                    url = media.get("uri")
                    if url:
                        uris.append({
                            "uri": url,
                            "width": media.get("width"),
                            "height": media.get("height")
                        })
                        break  # stop at first valid URL per media item

                # edges style
                edges = m.get("edges", [])
                for edge in edges:
                    node = edge.get("node", {})
                    for key in ["photo_image", "viewer_image", "image", "sticker_image", "animated_image"]:
                        media = node.get(key, {})
                        url = media.get("uri")
                        if url:
                            uris.append({
                                "uri": url,
                                "width": media.get("width"),
                                "height": media.get("height")
                            })
                            break

            elif isinstance(m, list):
                for item in m:
                    if isinstance(item, dict):
                        for key in ["photo_image", "viewer_image", "image", "sticker_image", "animated_image"]:
                            media = item.get(key, {})
                            url = media.get("uri")
                            if url:
                                uris.append({
                                    "uri": url,
                                    "width": media.get("width"),
                                    "height": media.get("height")
                                })
                                break
        
        return uris
    
    def extract_title_description(html):
        """
        Extracts title and description from Facebook page HTML.
        Returns a dict: {'title': ..., 'description': ...}
        """
        soup = BeautifulSoup(html, "html.parser")

        # Try Open Graph first
        og_title = soup.find("meta", property="og:title")
        og_description = soup.find("meta", property="og:description")

        # Fallback to normal meta tags
        title = og_title["content"] if og_title else None
        description = og_description["content"] if og_description else None

        if not title:
            title_tag = soup.find("title")
            title = title_tag.text.strip() if title_tag else None

        if not description:
            desc_tag = soup.find("meta", attrs={"name": "description"})
            description = desc_tag["content"] if desc_tag else None

        return title , description
    
    async def dual_facebook_scrape(url):

        matchs = [
        r"[?&]story_fbid=(pfbid\w+|\d+)",   # permalink.php?story_fbid=...
        r"/posts/(pfbid\w+|\d+)",            # /posts/...
        r"/photo\.php\?fbid=(pfbid\w+|\d+)", # /photo.php?fbid=...
        r"/share/pfbid\w+",                  # /share/pfbid...
        ]

        if not matchs:
            raise ValueError("Invalid FB post URL")
        
        print(url)
        
        for pattern in matchs:
            match = re.search(pattern, url)
            if match:
                pfbid = match.group(1)
        
        album_url = f"https://www.facebook.com/media/set/?set=pcb.{pfbid}"

        logger.debug("Album url found", platform=platform)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            headers = TaskManager.get_random_headers()  # your existing function
            context = await browser.new_context(extra_http_headers=headers)

            # create two pages
            page1 = await context.new_page()
            page2 = await context.new_page()

            # run both pages concurrently
            results = await asyncio.gather(
                open_facebook_url_async(page1, url),
                open_facebook_url_async(page2, album_url)
            )

            await browser.close()

        post_images, post_html = results[0]
        album_images, album_html = results[1]

        return {
            "post_images": post_images,
            "album_images": album_images,
            "html_post": post_html,
            "html_album": album_html
        }
    
    resolved_url = TaskManager.resolve_url(url)
    logger.debug("Extracting url", platform=platform)
    result = await (dual_facebook_scrape(resolved_url))
    
    html = result.get("html_post")
    album_data = result.get("album_images")
    post_data = result.get("post_images")
    
    title, description = extract_title_description(html)
    all_medias = extract_all_medias(album_data)
    stats = extract_likes_shares_from_matches(post_data)
    uris = extract_media_uris(all_medias)

    valid_uris = [
        u for u in uris
        if "scontent" in u.get("uri", "") and "fbcdn.net" in u.get("uri", "")
        and u.get("width", 0) >= 400 and u.get("height", 0) >= 400  # remove thumbnails
    ]

    seen = {}
    for u in valid_uris:
        uri = u.get("uri")
        if uri:
            # Keep the largest version if URI repeats
            if uri not in seen or (u.get("width",0)*u.get("height",0) > seen[uri].get("width",0)*seen[uri].get("height",0)):
                seen[uri] = u
    
    # Convert to list keeping order
    unique_uris = list(seen.values())

    # Handle single/multiple
    if len(unique_uris) == 0:
        media = {}
    elif len(unique_uris) == 1:
        media = unique_uris[0]
    else:
        media = {f"media_{i+1}": u for i, u in enumerate(unique_uris)}


    likes_count_in_formated = stats.get("likes") if stats else None
    likes_count_in_not_formated = stats.get("likes_i18n") if stats else None
    share_count_in_non_formated = stats.get("shares_i18n") if stats else None
    share_count_in_formated = stats.get("shares") if stats else None

    metadata = {
        "title": title or None,
        "description": description or None,
        "likes_count_in_non_formated": likes_count_in_not_formated,
        "likes_count_in_formated": likes_count_in_formated,
        "share_count_in_non_formated": share_count_in_non_formated,
        "share_count_in_formated": share_count_in_formated,
    }
    
    return {"media": media, "metadata": metadata}

def facebook_reel_extracter(url):
    
    cookie_manager = CookieManager()
    cookies = cookie_manager.get_facebook_cookie()

    if (
        "/share/v/" in url 
        or "watch?v=" in url 
        or "/reel/" in url 
        or "/share/r/" in url
        ):

        logger.debug(f"Requesting webpage {url}..." , platform=platform)

        data = EXTRACTER.Yt_dlp_extract(url , cookies)

        re_extract_with_cookies = None

        if not (data or re_extract_with_cookies):

            logger.error("Cannot extract video info..." , platform=platform)

            return {"url": url, "formats": [], "error": "Cannot extract video info"}
    
        def extract_urls(data):

            audio_urls = []

            sd_url = None
            hd_url = None

            for f in data.get("formats", []):
                # Collect audio URLs (first 2)
                if f.get("acodec") != "none" and len(audio_urls) < 2:
                    audio_urls.append(f.get("url"))
                # SD & HD video
                if f.get("format_id") == "sd":
                    sd_url = f.get("url")
                elif f.get("format_id") == "hd":
                    hd_url = f.get("url")
            return {
                "title": data.get("title"),
                "description": data.get("description"),
                "audio": audio_urls,
                "sd": sd_url,
                "hd": hd_url
            }
        
        thumbnail_url = data.get("thumbnail")
        return {"facebook_url": url, "thumbnail": thumbnail_url ,  "data":extract_urls(data)}

    else:

        logger.error("Data not found..." , platform=platform)
        return {"Error" : "Data not found"}

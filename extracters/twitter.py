from core.utils import EXTRACTER, TaskManager, logger , CookieManager
import asyncio

platform = "twitter"
cookie_manager = CookieManager()

async def twitter_media(url):
    loop = asyncio.get_event_loop()
    cookies = cookie_manager.get_x_cookie()
    data = await loop.run_in_executor(None, EXTRACTER.Gallery_dl_extracter, url, cookies)
    if not data:
        logger.error("gallery-dl returned no data")
        return None
    entries = data[0] if isinstance(data, tuple) else data
    media_list = EXTRACTER.twitter_json_mapper(entries)
    if not media_list:
        logger.warning("No media found after mapping")
        return None
    return {"twitter_url": url, "media": media_list}


import asyncio
import inspect
import json

from extracters.instagram import INSTAGRAM_EXTRACTER
from extracters.facebook import facebook_post_extracter, facebook_reel_extracter
from extracters.linkdin import LINKDIN_EXTRACTER
from extracters.pinterest import pinterest_extracter
from extracters.twitter import twitter_media

TEST_URLS = [
    # Instagram
    "https://www.instagram.com/p/DQPKDWogDRE/?utm_source=ig_web_copy_link",
    "https://www.instagram.com/p/DQBdVm3jRQ1/?utm_source=ig_web_copy_link",
    "https://www.instagram.com/reels/DQej50rkVBD/?utm_source=ig_web_copy_link",

    # Facebook
    "https://www.facebook.com/share/p/1CkGt4wdAv/",
    "https://www.facebook.com/share/p/1Biw1vckYC/",
    "https://www.facebook.com/share/r/16rB1bcwso/",

    # Pinterest
    "https://www.pinterest.com/pin/2533343538281828/",
    "https://www.pinterest.com/pin/712413234845193374/",

    # Twitter / X
    "https://x.com/Arsenal_Chizzyy/status/1984169303659442633",
    "https://x.com/theMadridZone/status/1984316314609946670",
    "https://x.com/narendramodi/status/1984155710268305766",

    # LinkedIn
    "https://www.linkedin.com/posts/warikoo_9-mindset-shifts-that-will-boost-your-career-activity-7390228598961500161-JLya",
    "https://www.linkedin.com/posts/art-basel_artbaselparis-ugcPost-7387064150000107522-iGi5",
    "https://www.linkedin.com/posts/hhshklatifa_globalcitiessummit2025-globalcitiessummit-activity-7388837936038973440-1n9O",
]


async def maybe_await(func, *args, **kwargs):
    """Run function, await if coroutine"""
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result

async def run_single_extractor(url: str):
    try:
        if "instagram.com" in url:
            print(f"🟣 Instagram → {url}")
            result = await maybe_await(INSTAGRAM_EXTRACTER.extract_instagram_auto, url)

        elif "facebook.com" in url:
            if "/r/" in url:
                print(f"🔵 Facebook Reel → {url}")
                result = await maybe_await(facebook_reel_extracter, url)
            else:
                print(f"🔵 Facebook Post → {url}")
                result = await maybe_await(facebook_post_extracter, url)

        elif "pinterest.com" in url:
            print(f"🔴 Pinterest → {url}")
            result = await maybe_await(pinterest_extracter, url)

        elif "twitter.com" in url or "x.com" in url:
            print(f"🐦 Twitter → {url}")
            result = await maybe_await(twitter_media, url)

        elif "linkedin.com" in url:
            print(f"💼 LinkedIn → {url}")
            result = await maybe_await(LINKDIN_EXTRACTER.linkdin_extracers, url)

        else:
            print(f"⚪ Unknown site: {url}")
            return {"status": "❌ Unknown domain"}

        return {"status": "✅ Working" if result else "⚠️ No result"}

    except Exception as e:
        return {"status": f"❌ Error: {e}"}

async def main():
    final_report = {}
    for url in TEST_URLS:
        res = await run_single_extractor(url)
        final_report[url] = res

    print("\n\n=== 🧾 FINAL REPORT ===\n")
    print(json.dumps(final_report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

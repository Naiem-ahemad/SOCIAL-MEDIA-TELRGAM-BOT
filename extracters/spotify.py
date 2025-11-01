import re 
import yt_dlp
import asyncio 
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from difflib import SequenceMatcher
from extracters.youtube import cookie_manager
from core.utils import logger , TaskManager
import traceback

platform = "spotify"


async def spotify_extracter(url):

    logger.info("spotify_extracter called", platform=platform)

    def get_spotify_title(url):

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        logger.debug("Requesting webpage", platform=platform)

        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
        except Exception:
            logger.error("Failed to fetch spotify page", platform=platform)
            raise

        soup = BeautifulSoup(r.text, "html.parser")

        meta_img = soup.find("meta", property="og:image")

        thumbnail_url = None
        if meta_img and meta_img.get("content"):
            thumbnail_url = meta_img["content"]

        title_tag = soup.find("title")
        if not title_tag:
            logger.error("No title tag found on Spotify page", platform=platform)
            raise ValueError("No title found")

        full_title = title_tag.text.strip()
        # Safely split to avoid index errors
        parts = full_title.split(" - ")
        second_part = parts[1] if len(parts) > 1 else ""
        if "Spotify" in second_part:
            clean_title = parts[0].strip()
        else:
            clean_title = parts[0].strip() + (" " + second_part if second_part else "")

        if "slowed" in clean_title.lower():
            clean_title += " normal"

        logger.debug("Parsed titles", platform=platform)
        return clean_title, full_title, thumbnail_url

    def normalize(text):
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", "", text)  # remove punctuation
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def extract_core_title(full_title):
        # Take only the first part before "-" or "by" or "|"
        core = full_title.split("-")[0]
        core = core.split("by")[0]
        core = core.split("|")[0]
        return normalize(core)
    
    async def get_best_audio_metadata_async(query, full_title=None, thumbnail_url=None):
        """
        Search YouTube for query → validate title → download best audio → return metadata with local path.
        """
        similarity_threshold = 0.3

        logger.info("Searching YouTube for query", platform=platform)

        core_title = extract_core_title(full_title or query)

        try:
            cookiefile = cookie_manager.get_youtube_cookie()
            logger.debug("Using youtube cookie", platform=platform)
        except Exception:
            cookiefile = None
            logger.debug("No youtube cookie available; proceeding without cookie", platform=platform)

        temp_path = "temp_downloads/%(title)s.%(ext)s"

        def extract_and_download():
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "cookiefile": cookiefile,
                "format": "bestaudio/best",
                "noplaylist": True,
                "default_search": "ytsearch1",
                "outtmpl": temp_path,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "192",
                }],
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=True)
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]

                title = info.get("title", "")
                similarity = SequenceMatcher(None, core_title, title.lower()).ratio()
                logger.debug("Matched title for query", platform=platform)
                if similarity < similarity_threshold:
                    raise ValueError(f"❌ Song not available (found '{title}')")

                # Build final file path (after FFmpeg conversion)
                downloaded_path = ydl.prepare_filename(info).rsplit(".", 1)[0] + ".m4a"
                logger.debug("Downloaded path", platform=platform)

                return {
                    "title": title,
                    "uploader": info.get("uploader"),
                    "duration": info.get("duration"),
                    "thumbnail": thumbnail_url or info.get("thumbnail"),
                    "webpage_url": info.get("webpage_url"),
                    "audio_path": downloaded_path,
                }

        try:
            return await asyncio.to_thread(extract_and_download)
        except Exception as e:
            logger.error("Error fetching audio", platform=platform)
            return None
            
    def get_spotify_tracks(url: str):

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Failed to fetch spotify tracks page", platform=platform)
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        # helpful debug output for development
        try:
            with open('tests.html', 'w', encoding='utf-8') as f:
                f.write(str(soup))
        except Exception:
            logger.debug("Failed to write tests.html (non-fatal)", platform=platform)

        tracks = []

        for div in soup.find_all("div", class_="Areas__InteractiveArea-sc-8gfrea-0"):
            a_tag = div.find("a", href=True)
            title_tag = div.find("span", class_="ListRowTitle__LineClamp-sc-1xe2if1-0")
            artist_tags = div.select("p[data-encore-id='listRowDetails'] a")

            if not a_tag or not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            artists = [a.get_text(strip=True) for a in artist_tags]
            full_url = urljoin("https://open.spotify.com", a_tag["href"])
            artist_str = ", ".join(artists)

            tracks.append({
                "title": title,
                "artists": artist_str,
                "url": full_url
            })

        logger.info("Found tracks on spotify page", platform=platform)
        return tracks

    async def download_album_concurrently(tracks, concurrency=5):
        """Search all tracks on YouTube concurrently and get audio metadata."""
        semaphore = asyncio.Semaphore(concurrency)
        results = []

        async def worker(track):
            async with semaphore:

                query = f"{track['title']} {track['artists']}"

                logger.debug("Queueing search for", platform=platform)

                data = await get_best_audio_metadata_async(query, full_title=query)

                return data

        tasks = [worker(t) for t in tracks]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r]
    
    try:
        spotify_url = TaskManager.resolve_url(url)
        logger.debug("Resolved spotify url", platform=platform)
    except Exception as e:
        logger.warning("Failed to resolve spotify url; using original", platform=platform)
        spotify_url = url

    if "/track/" in spotify_url or "episode" in spotify_url:
        # single track or episode
        try:
            song_title, full_title , thumbnail_url = get_spotify_title(spotify_url)
        except Exception:
            return None

        meta = await get_best_audio_metadata_async(song_title, full_title=full_title , thumbnail_url=thumbnail_url)

        if not meta:
            logger.warning("No metadata found for single track.", platform=platform)
        else:
            logger.info("Successfully retrieved metadata for song", platform=platform)

        return meta
    
    else:

        data = []

        tracks = get_spotify_tracks(spotify_url)
        if not tracks:
            logger.warning("No tracks found on spotify page or failed to parse.", platform=platform)
            return []

        results = await (download_album_concurrently(tracks, concurrency=5))
        for r in results:
            data.append(r)

        logger.info("Completed album extraction", platform=platform)
        return data



from bs4 import BeautifulSoup
import requests , json , tempfile , os
from datetime import datetime , timezone
from core.utils import TaskManager , logger

platform = "linkedin"

class LINKDIN_EXTRACTER:
    
    @staticmethod
    def get_linkedin_video_path(url: str) -> str:
        """
        If the LinkedIn URL is directly downloadable (contains .mp4), return it.
        Otherwise, download it to a temp file and return local path.
        """
        # Direct .mp4 available -> return as-is
        if ".mp4" in url.split("?")[0]:
            return url

        try:
            # Download to temp folder
            response = requests.get(url, stream=True, timeout=20)
            response.raise_for_status()

            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, "linkedin_video_temp.mp4")

            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            return temp_path

        except Exception:
            logger.error("Download error", platform=platform)
            return None
    
    @staticmethod
    def linkdin_extracers(url: str):  
        
        headers = TaskManager.get_random_headers()
        try:
            r = requests.get(url, headers=headers, timeout=15)
        except Exception:
            logger.error("Failed to fetch LinkedIn page", platform=platform)
            return None

        logger.debug("Requesting LinkedIn page", platform=platform)

        soup = BeautifulSoup(r.text, "html.parser")
        
        post_data = {
            "url": url,
            "type": None,
            "headline": None,
            "caption": None,
            "date": None,
            "posted_before": None,
            "likes_count": None,
            "media_type": None,
            "media_urls": [],
        }

        def format_time_ago(date_str: str) -> str:
            try:
                published = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
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
                return None

        def format_likes(num: int) -> str:
            if num >= 1_000_000:
                return f"{num/1_000_000:.1f}M"
            elif num >= 1_000:
                return f"{num/1_000:.1f}k"
            return str(num)

        # 🧩 1️⃣ Parse JSON-LD (LinkedIn structured data)
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string)
                if not data:
                    logger.error("Data not found....." , platform="linkedin")

                if data.get("@type") in ("VideoObject", "SocialMediaPosting"):
                    
                    post_data["type"] = data.get("@type")
                    post_data["headline"] = data.get("headline")
                    
                    # Extract author info
                    author_info = data.get("author")
                    creator_info = data.get("creator")

                    if author_info:
                        if isinstance(author_info, dict):
                            post_data["author"] = author_info.get("name")
                            post_data["author_url"] = author_info.get("url")
                        else:
                            post_data["author"] = str(author_info)
                            post_data["author_url"] = None
                    elif creator_info:
                        # fallback to creator if author is missing
                        post_data["author"] = creator_info.get("name")
                        post_data["author_url"] = creator_info.get("url")
                    else:
                        post_data["author"] = None
                        post_data["author_url"] = None

                    post_data["caption"] = data.get("articleBody") or data.get("description")
                    post_data["date"] = data.get("datePublished")
                    post_data["comment_Count"] = data.get("commentCount")
                    
                    # 🕒 Calculate time ago after date is set
                    if post_data["date"]:
                        try:
                            post_data["posted_before"] =  format_time_ago(post_data["date"])
                        except Exception:
                            post_data["posted_before"] = None

                    video_div = soup.find("div", {"data-test-id": "feed-native-video-content"})
                    fallback_url = data.get("contentUrl")
                    thumbnail = data.get("thumbnailUrl")
                    selected_url = fallback_url 

                    if video_div:
                        video_tag = video_div.find("video", attrs={"data-sources": True})
                        if video_tag:
                            sources_json = video_tag["data-sources"]
                            sources = json.loads(sources_json)

 
                            sources_sorted = sorted(sources, key=lambda x: x.get("data-bitrate", 0), reverse=True)


                            selected_url = None
                            for src in sources_sorted:
                                if src.get("data-bitrate", 0) >= 1200000:  
                                    selected_url = src.get("src")
                                    break

                            if not selected_url and sources_sorted:
                                selected_url = sources_sorted[0].get("src")

                            thumbnail = video_tag.get("data-poster-url", thumbnail)

                    if selected_url:
                        post_data["media_type"] = "video"
                        post_data["media_urls"] = [selected_url]
                        post_data["thumbnail"] = thumbnail
                        
                    elif data.get("@type") == "SocialMediaPosting":
                        img = data.get("image")
                        if isinstance(img, dict):
                            post_data["media_type"] = "image"
                            post_data["media_urls"] = [img.get("url")]

            except Exception:
                continue

        carousel_imgs = []
        for ul_tag in soup.select('ul[data-test-id="feed-images-content"]'):
            # skip if parent section is "related-posts"
            parent_section = ul_tag.find_parent("section", class_="related-posts")
            if parent_section:
                continue

            for img_tag in ul_tag.select('img[data-delayed-url]'):
                img_url = img_tag.get("data-delayed-url")
                if img_url and img_url not in carousel_imgs:
                    carousel_imgs.append(img_url)

        if carousel_imgs:
            post_data["media_urls"] = carousel_imgs

            if len(carousel_imgs) == 1:

                post_data["media_type"] = "photo"
            else:
                post_data["media_type"] = "carousel"

        like_tag = soup.find("a", {"data-test-id": "social-actions__reactions"})
        
        if like_tag:
            num_react = like_tag.get("data-num-reactions")
            if num_react and num_react.isdigit():
                post_data["likes_count"] = format_likes(int(num_react))

        return post_data
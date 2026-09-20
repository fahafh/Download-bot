import os
import re
import json
import glob
import shutil
import asyncio
import mimetypes
import tempfile
from urllib.parse import urlparse, urljoin, unquote

import requests
import yt_dlp
import imageio_ffmpeg

from bs4 import BeautifulSoup
from PIL import Image, ImageOps

from pyrogram import Client, filters
from pyrogram.types import InputMediaPhoto, InputMediaVideo


# =========================================================
# CONFIG
# =========================================================

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "instagram-post-reels-stories-downloader-api.p.rapidapi.com"

DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# =========================================================
# PYROGRAM
# =========================================================

app = Client(
    "telegram_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# =========================================================
# HTTP
# =========================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}


# =========================================================
# URL HELPERS
# =========================================================

def clean_url(url: str) -> str:
    if not url:
        return ""

    url = url.strip()

    # Remove surrounding brackets
    url = url.strip("<>[](){}")

    return url


def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_instagram_url(url: str) -> bool:
    domain = get_domain(url)
    return "instagram.com" in domain or "instagr.am" in domain


def is_tiktok_url(url: str) -> bool:
    domain = get_domain(url)
    return "tiktok.com" in domain or "vm.tiktok.com" in domain


def is_facebook_url(url: str) -> bool:
    domain = get_domain(url)
    return (
        "facebook.com" in domain
        or "fb.watch" in domain
        or "m.facebook.com" in domain
    )


def is_twitter_url(url: str) -> bool:
    domain = get_domain(url)
    return (
        "twitter.com" in domain
        or "x.com" in domain
        or "mobile.twitter.com" in domain
    )


def is_video_file(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()

    return ext in {
        ".mp4",
        ".mkv",
        ".webm",
        ".mov",
        ".avi",
        ".m4v",
        ".ts",
        ".flv",
    }


def is_image_file(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()

    return ext in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".gif",
        ".tif",
        ".tiff",
        ".avif",
        ".heic",
        ".heif",
    }


# =========================================================
# FILE HELPERS
# =========================================================

def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]+', "_", name)
    name = name.strip()
    return name[:180] or "media"


def unique_job_dir() -> str:
    path = tempfile.mkdtemp(prefix="job_", dir=DOWNLOAD_DIR)
    return path


def cleanup_directory(path: str):
    try:
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# =========================================================
# IMAGE CONVERSION
# =========================================================

def convert_image_to_jpg(path: str):
    """
    Telegram may reject some image extensions such as WEBP/AVIF
    when sent through send_photo.

    Convert them to a real JPEG first.
    """

    if not os.path.exists(path):
        return path

    ext = os.path.splitext(path)[1].lower()

    if ext in (".jpg", ".jpeg"):
        return path

    output = os.path.splitext(path)[0] + "_telegram.jpg"

    try:
        with Image.open(path) as img:

            # Fix rotated images using EXIF data
            img = ImageOps.exif_transpose(img)

            # Convert animated images using first frame
            try:
                img.seek(0)
            except Exception:
                pass

            if img.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", img.size, "white")

                if img.mode == "P":
                    img = img.convert("RGBA")

                if img.mode in ("RGBA", "LA"):
                    background.paste(
                        img,
                        mask=img.getchannel("A")
                    )
                else:
                    background.paste(img)

                img = background
            else:
                img = img.convert("RGB")

            # Telegram photo dimensions should be reasonable.
            # Resize extremely huge images.
            max_dimension = 10000

            if max(img.size) > max_dimension:
                ratio = max_dimension / max(img.size)

                new_size = (
                    int(img.width * ratio),
                    int(img.height * ratio)
                )

                img = img.resize(
                    new_size,
                    Image.Resampling.LANCZOS
                )

            img.save(
                output,
                "JPEG",
                quality=95,
                optimize=True
            )

        if os.path.exists(output):
            return output

    except Exception as e:
        print(f"[IMAGE] JPG conversion failed: {e}")

    return path


# =========================================================
# DOWNLOAD DIRECT MEDIA URL
# =========================================================

def download_direct_file(
    url: str,
    job_dir: str,
    referer: str = ""
):
    """
    Download an image/video directly from a CDN URL.
    """

    try:

        headers = dict(DEFAULT_HEADERS)

        if referer:
            headers["Referer"] = referer

        response = requests.get(
            url,
            headers=headers,
            timeout=40,
            stream=True
        )

        response.raise_for_status()

        content_type = (
            response.headers.get("Content-Type", "")
            .lower()
            .split(";")[0]
        )

        # Reject HTML pages accidentally returned instead of media
        if content_type.startswith("text/html"):
            return None

        extension = mimetypes.guess_extension(content_type)

        if not extension:

            parsed = urlparse(url)
            original = os.path.basename(parsed.path)

            original = unquote(original)

            ext = os.path.splitext(original)[1]

            if ext:
                extension = ext
            else:
                extension = ".bin"

        if extension == ".jpe":
            extension = ".jpg"

        filename = safe_filename(
            f"direct_{abs(hash(url))}"
        )

        path = os.path.join(
            job_dir,
            filename + extension
        )

        total = 0

        with open(path, "wb") as f:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):
                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_FILE_SIZE:
                    print("[DOWNLOAD] File too large")
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    return None

                f.write(chunk)

        if not os.path.exists(path):
            return None

        if os.path.getsize(path) == 0:
            os.remove(path)
            return None

        return path

    except Exception as e:
        print(f"[DIRECT] Download failed: {e}")
        return None


# =========================================================
# HTML SOCIAL MEDIA EXTRACTION
# =========================================================

def extract_meta_urls(html: str, page_url: str):
    """
    Extract image/video URLs from:
      og:image
      og:image:url
      og:image:secure_url
      twitter:image
      twitter:image:src
      og:video
      og:video:url
      og:video:secure_url
      twitter:player:stream
    """

    soup = BeautifulSoup(html, "html.parser")

    image_urls = []
    video_urls = []

    image_keys = {
        "og:image",
        "og:image:url",
        "og:image:secure_url",
        "twitter:image",
        "twitter:image:src",
    }

    video_keys = {
        "og:video",
        "og:video:url",
        "og:video:secure_url",
        "twitter:player:stream",
    }

    for meta in soup.find_all("meta"):

        key = (
            meta.get("property")
            or meta.get("name")
            or ""
        ).lower()

        value = (
            meta.get("content")
            or ""
        ).strip()

        if not value:
            continue

        value = urljoin(page_url, value)

        if key in image_keys:
            if value not in image_urls:
                image_urls.append(value)

        elif key in video_keys:
            if value not in video_urls:
                video_urls.append(value)

    return image_urls, video_urls


def extract_json_media_urls(html: str, page_url: str):
    """
    Extra fallback for Facebook/X pages.
    Searches JSON-like HTML for common image/video CDN URLs.
    """

    image_urls = []
    video_urls = []

    # Facebook CDN
    image_patterns = [
        r'https?:\\?/\\?/[^"\'\\ ]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\\ ]*)?',
        r'https?:\\?/\\?/scontent[^"\'\\ ]+',
        r'https?:\\?/\\?/lookaside[^"\'\\ ]+',
        r'https?:\\?/\\?/pbs\.twimg\.com[^"\'\\ ]+',
    ]

    video_patterns = [
        r'https?:\\?/\\?/video[^"\'\\ ]+\.(?:mp4|m3u8)(?:\?[^"\'\\ ]*)?',
        r'https?:\\?/\\?/video\.twimg\.com[^"\'\\ ]+',
    ]

    for pattern in image_patterns:

        try:
            matches = re.findall(
                pattern,
                html,
                flags=re.IGNORECASE
            )

            for item in matches:

                item = item.replace("\\/", "/")
                item = item.replace("\\u0026", "&")

                item = urljoin(page_url, item)

                if item not in image_urls:
                    image_urls.append(item)

        except Exception:
            pass

    for pattern in video_patterns:

        try:
            matches = re.findall(
                pattern,
                html,
                flags=re.IGNORECASE
            )

            for item in matches:

                item = item.replace("\\/", "/")
                item = item.replace("\\u0026", "&")

                item = urljoin(page_url, item)

                if item not in video_urls:
                    video_urls.append(item)

        except Exception:
            pass

    return image_urls, video_urls


def fetch_social_html_media(url: str, job_dir: str):
    """
    Facebook / X image extraction.

    Returns:
        [(path, is_video), ...]
    """

    try:

        headers = dict(DEFAULT_HEADERS)
        headers["Referer"] = url

        response = requests.get(
            url,
            headers=headers,
            timeout=30,
            allow_redirects=True
        )

        print(
            f"[SOCIAL HTML] {response.status_code} "
            f"{response.url}"
        )

        if response.status_code != 200:
            return []

        html = response.text

        image_urls, video_urls = extract_meta_urls(
            html,
            response.url
        )

        json_images, json_videos = extract_json_media_urls(
            html,
            response.url
        )

        for item in json_images:
            if item not in image_urls:
                image_urls.append(item)

        for item in json_videos:
            if item not in video_urls:
                video_urls.append(item)

        results = []

        # Limit to avoid accidentally downloading unrelated images
        image_urls = image_urls[:10]
        video_urls = video_urls[:5]

        for media_url in image_urls:

            path = download_direct_file(
                media_url,
                job_dir,
                referer=response.url
            )

            if path:
                results.append(
                    (path, False)
                )

        for media_url in video_urls:

            path = download_direct_file(
                media_url,
                job_dir,
                referer=response.url
            )

            if path:

                if is_video_file(path):
                    results.append(
                        (path, True)
                    )

        return results

    except Exception as e:
        print(f"[SOCIAL HTML] Failed: {e}")
        return []


# =========================================================
# INSTAGRAM RAPIDAPI
# =========================================================

def collect_urls_from_json(obj, results=None):
    """
    Recursively collect possible media URLs from JSON.
    """

    if results is None:
        results = []

    if isinstance(obj, dict):

        for key, value in obj.items():

            key_lower = str(key).lower()

            if isinstance(value, str):

                value_str = value.strip()

                if (
                    value_str.startswith("http://")
                    or value_str.startswith("https://")
                ):

                    if any(
                        word in key_lower
                        for word in [
                            "image",
                            "photo",
                            "video",
                            "url",
                            "src",
                            "media",
                            "display",
                            "thumbnail",
                            "download"
                        ]
                    ):
                        if value_str not in results:
                            results.append(value_str)

            else:
                collect_urls_from_json(
                    value,
                    results
                )

    elif isinstance(obj, list):

        for item in obj:
            collect_urls_from_json(
                item,
                results
            )

    return results


def instagram_rapidapi(url: str, job_dir: str):
    if not RAPIDAPI_KEY:
        print("[INSTAGRAM] RAPIDAPI_KEY missing")
        return []

    try:

        endpoint = (
            f"https://{RAPIDAPI_HOST}/instagram/"
        )

        headers = {
            "x-rapidapi-key": RAPIDAPI_KEY,
            "x-rapidapi-host": RAPIDAPI_HOST,
        }

        params = {
            "url": url
        }

        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=40
        )

        print(
            f"[INSTAGRAM RAPIDAPI] "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            return []

        try:
            data = response.json()
        except Exception:
            return []

        candidates = collect_urls_from_json(data)

        print(
            f"[INSTAGRAM] Candidates: "
            f"{len(candidates)}"
        )

        results = []

        seen = set()

        for media_url in candidates:

            if media_url in seen:
                continue

            seen.add(media_url)

            lower = media_url.lower()

            # Ignore obvious avatars / tiny thumbnails
            if any(
                bad in lower
                for bad in [
                    "profile_pic",
                    "avatar",
                    "favicon"
                ]
            ):
                continue

            path = download_direct_file(
                media_url,
                job_dir,
                referer=url
            )

            if not path:
                continue

            if is_video_file(path):
                results.append(
                    (path, True)
                )
            elif is_image_file(path):
                results.append(
                    (path, False)
                )

            if len(results) >= 10:
                break

        return results

    except Exception as e:
        print(f"[INSTAGRAM RAPIDAPI] Error: {e}")
        return []


# =========================================================
# INSTAGRAM HTML FALLBACK
# =========================================================

def instagram_html_fallback(url: str, job_dir: str):

    try:

        headers = dict(DEFAULT_HEADERS)
        headers["Referer"] = "https://www.instagram.com/"

        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        print(
            f"[INSTAGRAM HTML] "
            f"{response.status_code} "
            f"length={len(response.text)}"
        )

        if response.status_code != 200:
            return []

        html = response.text

        image_urls, video_urls = extract_meta_urls(
            html,
            response.url
        )

        json_images, json_videos = extract_json_media_urls(
            html,
            response.url
        )

        for x in json_images:
            if x not in image_urls:
                image_urls.append(x)

        for x in json_videos:
            if x not in video_urls:
                video_urls.append(x)

        results = []

        for media_url in image_urls[:10]:

            path = download_direct_file(
                media_url,
                job_dir,
                referer=response.url
            )

            if path:
                results.append(
                    (path, False)
                )

        for media_url in video_urls[:5]:

            path = download_direct_file(
                media_url,
                job_dir,
                referer=response.url
            )

            if path and is_video_file(path):
                results.append(
                    (path, True)
                )

        return results

    except Exception as e:
        print(f"[INSTAGRAM HTML] Error: {e}")
        return []


# =========================================================
# TIKTOK API
# =========================================================

def tiktok_tikwm(url: str, job_dir: str):

    try:

        response = requests.get(
            "https://www.tikwm.com/api/",
            params={
                "url": url,
                "hd": 1
            },
            headers=DEFAULT_HEADERS,
            timeout=40
        )

        print(
            f"[TIKTOK API] {response.status_code}"
        )

        if response.status_code != 200:
            return []

        data = response.json()

        if data.get("code") not in (0, "0", None):
            print(
                f"[TIKTOK API] "
                f"code={data.get('code')}"
            )

        payload = data.get("data") or {}

        results = []

        # Video
        video_urls = []

        for key in [
            "hdplay",
            "play",
            "wmplay",
            "download"
        ]:

            value = payload.get(key)

            if isinstance(value, str) and value.startswith("http"):
                video_urls.append(value)

        for media_url in video_urls[:2]:

            path = download_direct_file(
                media_url,
                job_dir,
                referer="https://www.tiktok.com/"
            )

            if path and is_video_file(path):
                results.append(
                    (path, True)
                )
                break

        # Photos
        images = payload.get("images")

        if isinstance(images, list):

            for image_url in images[:10]:

                if not isinstance(image_url, str):
                    continue

                path = download_direct_file(
                    image_url,
                    job_dir,
                    referer="https://www.tiktok.com/"
                )

                if path:
                    results.append(
                        (path, False)
                    )

        return results

    except Exception as e:
        print(f"[TIKTOK API] Error: {e}")
        return []


# =========================================================
# YT-DLP
# =========================================================

def download_with_ytdlp(url: str, job_dir: str):

    print(
        f"[YT-DLP] downloading: {url}"
    )

    before = set(
        glob.glob(
            os.path.join(
                job_dir,
                "**",
                "*"
            ),
            recursive=True
        )
    )

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    cookie_file = "cookies.txt"

    ydl_opts = {
        "outtmpl": os.path.join(
            job_dir,
            "%(title).100s_%(id)s.%(ext)s"
        ),

        "format": (
            "bestvideo*+bestaudio/"
            "best"
        ),

        "merge_output_format": "mp4",

        "ffmpeg_location": ffmpeg_path,

        "noplaylist": True,

        "quiet": True,

        "no_warnings": False,

        "retries": 3,

        "fragment_retries": 3,

        "http_headers": DEFAULT_HEADERS,

        "restrictfilenames": True,

        "socket_timeout": 30,
    }

    if os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file
        print("[YT-DLP] Using cookies.txt")

    try:

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            if not info:
                return []

        after = set(
            glob.glob(
                os.path.join(
                    job_dir,
                    "**",
                    "*"
                ),
                recursive=True
            )
        )

        new_files = [
            p for p in (after - before)
            if os.path.isfile(p)
        ]

        # Sometimes the file already existed in the directory
        if not new_files:

            new_files = [
                p for p in after
                if os.path.isfile(p)
            ]

        # Remove temporary partial files
        new_files = [
            p for p in new_files
            if not p.endswith(
                (".part", ".ytdl")
            )
        ]

        results = []

        for path in new_files:

            try:
                size = os.path.getsize(path)

                if size == 0:
                    continue

                if size > MAX_FILE_SIZE:
                    print(
                        f"[YT-DLP] Too large: {path}"
                    )
                    continue

            except Exception:
                continue

            if is_video_file(path):
                results.append(
                    (path, True)
                )

            elif is_image_file(path):
                results.append(
                    (path, False)
                )

        return results

    except Exception as e:

        print(
            f"[YT-DLP] Failed: {type(e).__name__}: {e}"
        )

        return []


# =========================================================
# GENERIC MEDIA EXTRACTION
# =========================================================

async def extract_media(url: str):

    job_dir = unique_job_dir()

    try:

        results = []

        # -------------------------------------------------
        # INSTAGRAM
        # -------------------------------------------------

        if is_instagram_url(url):

            print(
                "[BOT] Instagram detected"
            )

            results = await asyncio.to_thread(
                instagram_rapidapi,
                url,
                job_dir
            )

            if not results:

                print(
                    "[BOT] Instagram RapidAPI failed. "
                    "Trying HTML..."
                )

                results = await asyncio.to_thread(
                    instagram_html_fallback,
                    url,
                    job_dir
                )

            if not results:

                print(
                    "[BOT] Instagram HTML failed. "
                    "Trying yt-dlp..."
                )

                results = await asyncio.to_thread(
                    download_with_ytdlp,
                    url,
                    job_dir
                )

        # -------------------------------------------------
        # FACEBOOK
        # -------------------------------------------------

        elif is_facebook_url(url):

            print(
                "[BOT] Facebook detected"
            )

            # First try HTML because this is useful
            # for photo posts.
            results = await asyncio.to_thread(
                fetch_social_html_media,
                url,
                job_dir
            )

            if not results:

                print(
                    "[BOT] Facebook HTML failed. "
                    "Trying yt-dlp..."
                )

                results = await asyncio.to_thread(
                    download_with_ytdlp,
                    url,
                    job_dir
                )

        # -------------------------------------------------
        # X / TWITTER
        # -------------------------------------------------

        elif is_twitter_url(url):

            print(
                "[BOT] X/Twitter detected"
            )

            # First try HTML for tweet images.
            results = await asyncio.to_thread(
                fetch_social_html_media,
                url,
                job_dir
            )

            if not results:

                print(
                    "[BOT] X HTML failed. "
                    "Trying yt-dlp..."
                )

                results = await asyncio.to_thread(
                    download_with_ytdlp,
                    url,
                    job_dir
                )

        # -------------------------------------------------
        # TIKTOK
        # -------------------------------------------------

        elif is_tiktok_url(url):

            print(
                "[BOT] TikTok detected"
            )

            results = await asyncio.to_thread(
                tiktok_tikwm,
                url,
                job_dir
            )

            if not results:

                print(
                    "[BOT] TikWM failed. "
                    "Trying yt-dlp..."
                )

                results = await asyncio.to_thread(
                    download_with_ytdlp,
                    url,
                    job_dir
                )

        # -------------------------------------------------
        # OTHER SITES
        # -------------------------------------------------

        else:

            print(
                "[BOT] Generic URL. "
                "Trying yt-dlp..."
            )

            results = await asyncio.to_thread(
                download_with_ytdlp,
                url,
                job_dir
            )

        # -------------------------------------------------
        # REMOVE DUPLICATES
        # -------------------------------------------------

        unique = []

        seen = set()

        for path, is_video in results:

            if not os.path.exists(path):
                continue

            real = os.path.realpath(path)

            if real in seen:
                continue

            seen.add(real)

            unique.append(
                (path, is_video)
            )

        return job_dir, unique

    except Exception as e:

        print(
            f"[EXTRACT] Error: {e}"
        )

        return job_dir, []


# =========================================================
# SEND MEDIA TO TELEGRAM
# =========================================================

async def send_one_media(
    message,
    path: str,
    is_video: bool
):

    if not os.path.exists(path):
        return False

    try:

        size = os.path.getsize(path)

        if size > MAX_FILE_SIZE:
            await message.reply_text(
                "❌ الملف أكبر من الحد المسموح."
            )
            return False

        # -------------------------------------------------
        # VIDEO
        # -------------------------------------------------

        if is_video or is_video_file(path):

            try:

                await message.reply_video(
                    path,
                    supports_streaming=True
                )

                print(
                    f"[TELEGRAM] Video sent: {path}"
                )

                return True

            except Exception as e:

                print(
                    f"[TELEGRAM] Video upload failed: {e}"
                )

                # Fallback as document
                try:

                    await message.reply_document(
                        path
                    )

                    print(
                        f"[TELEGRAM] Video sent as document: "
                        f"{path}"
                    )

                    return True

                except Exception as fallback_error:

                    print(
                        "[TELEGRAM] "
                        f"Video fallback failed: "
                        f"{fallback_error}"
                    )

                    return False

        # -------------------------------------------------
        # IMAGE
        # -------------------------------------------------

        converted = await asyncio.to_thread(
            convert_image_to_jpg,
            path
        )

        if converted != path:
            print(
                f"[TELEGRAM] Converted image: "
                f"{path} -> {converted}"
            )

        # Telegram photo upload
        try:

            await message.reply_photo(
                converted
            )

            print(
                f"[TELEGRAM] Photo sent: "
                f"{converted}"
            )

            return True

        except Exception as photo_error:

            print(
                "[TELEGRAM] Photo upload failed: "
                f"{photo_error}"
            )

            # Important fallback:
            # send as generic document
            try:

                await message.reply_document(
                    converted
                )

                print(
                    "[TELEGRAM] "
                    f"Image sent as document: "
                    f"{converted}"
                )

                return True

            except Exception as fallback_error:

                print(
                    "[TELEGRAM] "
                    f"Fallback upload failed: "
                    f"{fallback_error}"
                )

                return False

    except Exception as e:

        print(
            f"[TELEGRAM] Send error: {e}"
        )

        return False


# =========================================================
# SEND ALL MEDIA
# =========================================================

async def send_media_items(
    message,
    media_items
):

    sent = 0

    # Limit to 10 media items per request cycle
    # to avoid huge batches.
    media_items = media_items[:10]

    for path, is_video in media_items:

        if not os.path.exists(path):
            continue

        success = await send_one_media(
            message,
            path,
            is_video
        )

        if success:
            sent += 1

        # Small delay prevents Telegram flood problems
        await asyncio.sleep(0.5)

    return sent


# =========================================================
# URL EXTRACTION FROM MESSAGE
# =========================================================

URL_REGEX = re.compile(
    r"https?://[^\s<>]+",
    re.IGNORECASE
)


def extract_url(text: str):

    if not text:
        return None

    matches = URL_REGEX.findall(text)

    if not matches:
        return None

    url = matches[0]

    # Remove common punctuation after URL
    url = url.rstrip(
        ".,!?;:)]}>\"'"
    )

    return clean_url(url)


# =========================================================
# MAIN MESSAGE HANDLER
# =========================================================

@app.on_message(
    filters.private & filters.text
)
async def handle_message(client, message):

    url = extract_url(
        message.text
    )

    if not url:

        await message.reply_text(
            "📎 دزلي رابط Instagram أو TikTok "
            "أو Facebook أو X/Twitter أو أي موقع مدعوم."
        )

        return

    print(
        f"[BOT] URL received: {url}"
    )

    status = await message.reply_text(
        "⏳ جاري استخراج المحتوى..."
    )

    job_dir = None

    try:

        # Platform status
        if is_instagram_url(url):

            await status.edit_text(
                "📸 جاري استخراج محتوى Instagram..."
            )

        elif is_tiktok_url(url):

            await status.edit_text(
                "🎵 جاري استخراج محتوى TikTok..."
            )

        elif is_facebook_url(url):

            await status.edit_text(
                "📘 جاري استخراج محتوى Facebook..."
            )

        elif is_twitter_url(url):

            await status.edit_text(
                "𝕏 جاري استخراج محتوى X/Twitter..."
            )

        else:

            await status.edit_text(
                "⬇️ جاري تحميل المحتوى..."
            )

        job_dir, media_items = await extract_media(
            url
        )

        if not media_items:

            await status.edit_text(
                "❌ ما گدرت أستخرج محتوى من هذا الرابط.\n\n"
                "ممكن الرابط خاص، أو الموقع منع الوصول، "
                "أو صيغة الرابط غير مدعومة حاليًا."
            )

            return

        await status.edit_text(
            f"📦 تم العثور على {len(media_items)} ملف.\n"
            "📤 جاري الإرسال إلى Telegram..."
        )

        sent = await send_media_items(
            message,
            media_items
        )

        if sent == 0:

            await status.edit_text(
                "❌ حصلت الملفات لكن Telegram "
                "رفض إرسالها."
            )

        else:

            await status.edit_text(
                f"✅ تم إرسال {sent} ملف."
            )

    except Exception as e:

        print(
            f"[BOT] Handler error: {e}"
        )

        try:

            await status.edit_text(
                "❌ حدث خطأ أثناء التحميل."
            )

        except Exception:
            pass

    finally:

        if job_dir:
            cleanup_directory(
                job_dir
            )

            print(
                f"[BOT] Cleaned: {job_dir}"
            )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print(
        "========================================"
    )

    print(
        "Telegram Downloader Bot Starting..."
    )

    print(
        "========================================"
    )

    app.run()

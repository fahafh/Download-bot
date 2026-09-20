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


# =========================================================
# CONFIG
# =========================================================

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = os.getenv(
    "RAPIDAPI_HOST",
    "instagram-post-reels-stories-downloader-api.p.rapidapi.com"
)

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
MAX_MEDIA_PER_LINK = 10

REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Mobile Safari/537.36"
)


# =========================================================
# LOGGING
# =========================================================

def log(message):
    print(f"[BOT] {message}", flush=True)


# =========================================================
# CLIENT
# =========================================================

app = Client(
    "telegram_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# =========================================================
# COMMON HELPERS
# =========================================================

def clean_url(url):
    url = url.strip()
    url = url.rstrip(".,!?)]}>\"'")
    return url


def detect_platform(url):
    host = urlparse(url).netloc.lower()

    if "instagram.com" in host:
        return "instagram"

    if "tiktok.com" in host or "vm.tiktok.com" in host:
        return "tiktok"

    if "facebook.com" in host or "fb.watch" in host:
        return "facebook"

    if "twitter.com" in host or "x.com" in host:
        return "twitter"

    return "unknown"


def is_valid_url(url):
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def safe_filename(name):
    name = unquote(str(name))
    name = re.sub(r"[^\w\-. ]+", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:180] or "media"


def file_size(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def is_too_large(path):
    return file_size(path) > MAX_FILE_SIZE


def remove_file(path):
    try:
        if os.path.exists(path):
            os.remove(path)
            log(f"Deleted: {path}")
    except Exception as e:
        log(f"Delete failed: {path} -> {e}")


def unique_path(folder, base_name, ext):
    base_name = safe_filename(base_name)
    ext = ext.lower()

    path = os.path.join(folder, base_name + ext)

    counter = 1

    while os.path.exists(path):
        path = os.path.join(
            folder,
            f"{base_name}_{counter}{ext}"
        )
        counter += 1

    return path


# =========================================================
# IMAGE HELPERS
# =========================================================

BAD_WORDS = [
    "favicon",
    "sprite",
    "avatar",
    "profile_pic",
    "profilepic",
    "default_avatar",
    "placeholder",
    "instagram-logo",
    "instagram_logo",
    "instagramlogo",
    "logo-instagram",
    "logo_instagram",
    "logo",
    "icon",
    "emoji",
    "app-icon",
    "app_icon",
    "apple-touch-icon",
]


def is_bad_asset(url):
    low = unquote(url).lower()

    for word in BAD_WORDS:
        if word in low:
            return True

    return False


def is_image_file(path):
    try:
        with Image.open(path) as img:
            return img.format is not None
    except Exception:
        return False


def convert_to_jpg(src, folder):
    """
    Convert any image to a normal JPG.
    This avoids Telegram PHOTO_EXT_INVALID.
    """

    try:
        with Image.open(src) as original:

            img = ImageOps.exif_transpose(original)

            # If animated, take first frame
            try:
                if getattr(img, "is_animated", False):
                    img.seek(0)
                    img = img.convert("RGBA")
                else:
                    img = img.convert("RGBA")
            except Exception:
                img = img.convert("RGBA")

            # Limit gigantic images
            max_dimension = 10000

            if max(img.width, img.height) > max_dimension:
                ratio = max_dimension / max(img.width, img.height)

                new_size = (
                    int(img.width * ratio),
                    int(img.height * ratio)
                )

                img = img.resize(new_size, Image.Resampling.LANCZOS)

            # White background for transparent images
            background = Image.new(
                "RGB",
                img.size,
                "white"
            )

            background.paste(
                img,
                mask=img.getchannel("A")
            )

            base = os.path.splitext(
                os.path.basename(src)
            )[0]

            out = unique_path(
                folder,
                base,
                ".jpg"
            )

            background.save(
                out,
                "JPEG",
                quality=95,
                optimize=True
            )

            return out

    except Exception as e:
        log(f"JPG conversion failed: {src} -> {e}")
        return None


# =========================================================
# HTTP DOWNLOAD
# =========================================================

def download_direct(url, folder, index=0, expected_type=None):
    """
    Download a direct media URL.
    """

    try:

        if not is_valid_url(url):
            return None

        if is_bad_asset(url):
            log(f"Rejected bad asset: {url[:160]}")
            return None

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Referer": "https://www.instagram.com/"
        }

        response = requests.get(
            url,
            headers=headers,
            stream=True,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        if response.status_code != 200:
            log(
                f"Download HTTP {response.status_code}: "
                f"{url[:160]}"
            )
            return None

        content_type = (
            response.headers.get("Content-Type", "")
            .lower()
        )

        if "text/html" in content_type:
            log("Rejected HTML instead of media")
            return None

        final_url = response.url

        parsed = urlparse(final_url)

        ext = os.path.splitext(
            parsed.path
        )[1].lower()

        if not ext:
            guessed = mimetypes.guess_extension(
                content_type.split(";")[0]
            )

            if guessed:
                ext = guessed

        if ext in (".jpe",):
            ext = ".jpg"

        if not ext:
            ext = ".bin"

        if expected_type == "video":
            if ext not in (
                ".mp4",
                ".mov",
                ".m4v",
                ".webm",
                ".mkv"
            ):
                ext = ".mp4"

        filename = unique_path(
            folder,
            f"media_{index + 1}",
            ext
        )

        total = 0

        with open(filename, "wb") as f:

            for chunk in response.iter_content(
                chunk_size=1024 * 256
            ):

                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_FILE_SIZE:
                    f.close()
                    remove_file(filename)
                    log("File exceeded maximum size")
                    return None

                f.write(chunk)

        if file_size(filename) < 1000:
            remove_file(filename)
            return None

        # Validate image
        if content_type.startswith("image/") or ext in (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp"
        ):

            if not is_image_file(filename):
                remove_file(filename)
                return None

        log(f"Downloaded: {filename}")

        return filename

    except Exception as e:
        log(f"Direct download error: {e}")
        return None


# =========================================================
# URL EXTRACTION
# =========================================================

MEDIA_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".m4v",
    ".webm"
)


def looks_like_media_url(url):
    if not is_valid_url(url):
        return False

    low = unquote(url).lower()

    if is_bad_asset(low):
        return False

    # Strong Instagram CDN indicators
    strong = (
        "scontent." in low,
        "cdninstagram.com" in low,
        "fbcdn.net" in low,
        "lookaside.fbsbx.com" in low,
        "video.twimg.com" in low,
        "pbs.twimg.com" in low,
    )

    if any(strong):
        return True

    path = urlparse(url).path.lower()

    if path.endswith(MEDIA_EXTENSIONS):
        return True

    return False


def normalize_extracted_url(url, base_url=None):

    if not url:
        return None

    url = unquote(str(url)).strip()

    url = url.replace("\\/", "/")

    if base_url:
        url = urljoin(base_url, url)

    # Remove JSON escaping
    url = url.replace("\\u0026", "&")
    url = url.replace("\\u003D", "=")

    return url


def extract_urls_from_text(text):
    """
    Extract only URL-looking strings from scripts/HTML.
    """

    if not text:
        return []

    found = []

    patterns = [
        r'https?://[^"\'<>\s\\]+',
        r'https?:\\/\\/[^"\'<>\s\\]+',
    ]

    for pattern in patterns:

        try:
            matches = re.findall(pattern, text)

            for value in matches:

                value = normalize_extracted_url(value)

                if not value:
                    continue

                if looks_like_media_url(value):
                    found.append(value)

        except Exception:
            pass

    return found


# =========================================================
# INSTAGRAM RAPIDAPI
# =========================================================

def extract_urls_recursive(obj, results=None, key_hint=""):
    """
    Recursively scan JSON for media URLs.
    """

    if results is None:
        results = []

    if isinstance(obj, dict):

        for key, value in obj.items():

            key_low = str(key).lower()

            if isinstance(value, str):

                if (
                    value.startswith("http://")
                    or value.startswith("https://")
                ):

                    url = normalize_extracted_url(value)

                    if not url:
                        continue

                    # Strong keys
                    strong_key = any(
                        x in key_low
                        for x in (
                            "display_url",
                            "image_url",
                            "media_url",
                            "video_url",
                            "download_url",
                            "source_url",
                            "original_url",
                            "photo_url",
                            "play_url",
                            "hdplay",
                        )
                    )

                    if strong_key or looks_like_media_url(url):
                        results.append(
                            (
                                100 if strong_key else 60,
                                url,
                                key_low
                            )
                        )

            elif isinstance(value, (dict, list)):
                extract_urls_recursive(
                    value,
                    results,
                    key_low
                )

    elif isinstance(obj, list):

        for item in obj:
            extract_urls_recursive(
                item,
                results,
                key_hint
            )

    return results


def rapidapi_instagram(url, folder):

    if not RAPIDAPI_KEY:
        log("RapidAPI key not configured")
        return []

    endpoint = (
        f"https://{RAPIDAPI_HOST}/instagram/"
    )

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "User-Agent": USER_AGENT,
        "Accept": "application/json"
    }

    params = {
        "url": url
    }

    try:

        log("Instagram RapidAPI request...")

        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        body = response.text

        log(
            f"Instagram RapidAPI: "
            f"{response.status_code} "
            f"length={len(body)}"
        )

        # Do not print API key or complete response
        if len(body) <= 500:
            safe_body = body.replace(
                RAPIDAPI_KEY,
                "***"
            )

            log(
                f"RapidAPI response: "
                f"{safe_body[:500]}"
            )

        if response.status_code != 200:
            return []

        try:
            data = response.json()
        except Exception as e:
            log(f"RapidAPI JSON error: {e}")
            return []

        candidates = extract_urls_recursive(data)

        log(
            f"Instagram RapidAPI candidates: "
            f"{len(candidates)}"
        )

        # Remove duplicates
        unique = []
        seen = set()

        for score, media_url, key in sorted(
            candidates,
            key=lambda x: x[0],
            reverse=True
        ):

            media_url = normalize_extracted_url(media_url)

            if not media_url:
                continue

            if media_url in seen:
                continue

            seen.add(media_url)

            unique.append(
                (score, media_url, key)
            )

            if len(unique) >= MAX_MEDIA_PER_LINK:
                break

        files = []

        for index, (_, media_url, key) in enumerate(
            unique
        ):

            expected = (
                "video"
                if "video" in key
                or "play" in key
                else None
            )

            path = download_direct(
                media_url,
                folder,
                index,
                expected
            )

            if path:
                files.append(path)

        return files

    except Exception as e:
        log(f"RapidAPI error: {e}")
        return []


# =========================================================
# INSTAGRAM EMBED
# =========================================================

def instagram_embed_urls(url):
    """
    Try Instagram's embed page.
    """

    results = []

    parsed = urlparse(url)

    clean_path = parsed.path.rstrip("/")

    if not clean_path:
        return results

    embed_url = (
        f"https://www.instagram.com"
        f"{clean_path}/embed/"
    )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9"
    }

    try:

        log(
            f"Instagram EMBED: {embed_url}"
        )

        response = requests.get(
            embed_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        log(
            f"Instagram EMBED: "
            f"{response.status_code} "
            f"length={len(response.text)}"
        )

        if response.status_code != 200:
            return results

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Meta tags first
        for tag in soup.find_all(
            "meta"
        ):

            content = tag.get("content")

            if not content:
                continue

            if looks_like_media_url(content):
                results.append(content)

        # Scripts
        for script in soup.find_all(
            "script"
        ):

            text = script.string or script.get_text(
                strip=False
            )

            if not text:
                continue

            results.extend(
                extract_urls_from_text(text)
            )

        # Direct HTML URLs
        results.extend(
            extract_urls_from_text(
                response.text
            )
        )

        # Deduplicate
        final = []
        seen = set()

        for item in results:

            item = normalize_extracted_url(
                item,
                embed_url
            )

            if not item:
                continue

            if not looks_like_media_url(item):
                continue

            if item in seen:
                continue

            seen.add(item)
            final.append(item)

        return final[:MAX_MEDIA_PER_LINK]

    except Exception as e:
        log(f"Instagram embed error: {e}")
        return []


# =========================================================
# INSTAGRAM NORMAL HTML
# =========================================================

def instagram_html_urls(url):

    results = []

    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/"
    }

    try:

        log("Instagram HTML request...")

        response = requests.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        log(
            f"Instagram HTML: "
            f"{response.status_code} "
            f"length={len(response.text)}"
        )

        if response.status_code != 200:
            return results

        html = response.text

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # -------------------------------------------------
        # META
        # -------------------------------------------------

        for tag in soup.find_all("meta"):

            prop = (
                tag.get("property")
                or tag.get("name")
                or ""
            ).lower()

            content = tag.get("content")

            if not content:
                continue

            if prop in (
                "og:image",
                "og:video",
                "og:video:url",
                "og:video:secure_url",
                "twitter:image"
            ):

                if looks_like_media_url(content):
                    results.append(content)

        # -------------------------------------------------
        # JSON-LD
        # -------------------------------------------------

        for script in soup.find_all(
            "script",
            attrs={"type": "application/ld+json"}
        ):

            text = script.string or script.get_text(
                strip=False
            )

            if not text:
                continue

            try:

                data = json.loads(text)

                candidates = extract_urls_recursive(
                    data
                )

                for _, media_url, _ in candidates:
                    results.append(media_url)

            except Exception:
                pass

        # -------------------------------------------------
        # SCRIPT URLS
        # -------------------------------------------------

        for script in soup.find_all("script"):

            text = script.string or script.get_text(
                strip=False
            )

            if not text:
                continue

            results.extend(
                extract_urls_from_text(text)
            )

        # -------------------------------------------------
        # RAW HTML
        # -------------------------------------------------

        results.extend(
            extract_urls_from_text(html)
        )

        final = []
        seen = set()

        for item in results:

            item = normalize_extracted_url(
                item,
                url
            )

            if not item:
                continue

            if not looks_like_media_url(item):
                continue

            if item in seen:
                continue

            seen.add(item)
            final.append(item)

        log(
            f"Instagram HTML media candidates: "
            f"{len(final)}"
        )

        return final[:MAX_MEDIA_PER_LINK]

    except Exception as e:
        log(f"Instagram HTML error: {e}")
        return []


# =========================================================
# INSTAGRAM DOWNLOAD
# =========================================================

def instagram_from_urls(urls, folder):

    files = []

    for index, media_url in enumerate(urls):

        if len(files) >= MAX_MEDIA_PER_LINK:
            break

        expected = (
            "video"
            if any(
                x in media_url.lower()
                for x in (
                    ".mp4",
                    ".mov",
                    ".m4v",
                    ".webm",
                    "video"
                )
            )
            else None
        )

        path = download_direct(
            media_url,
            folder,
            index,
            expected
        )

        if not path:
            continue

        # Never send weird binary files
        if path.lower().endswith(
            (
                ".bin",
                ".html",
                ".txt"
            )
        ):
            remove_file(path)
            continue

        files.append(path)

    return files


# =========================================================
# YT-DLP
# =========================================================

def yt_dlp_download(url, folder):

    before = set(
        glob.glob(
            os.path.join(folder, "*")
        )
    )

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    output_template = os.path.join(
        folder,
        "%(title).80s_%(id)s.%(ext)s"
    )

    ydl_opts = {
        "outtmpl": output_template,

        "format": (
            "bestvideo*+bestaudio/"
            "best"
        ),

        "merge_output_format": "mp4",

        "noplaylist": True,

        "quiet": False,

        "no_warnings": False,

        "retries": 2,

        "fragment_retries": 2,

        "socket_timeout": 30,

        "ffmpeg_location": ffmpeg_path,

        "http_headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9"
        },

        "extractor_args": {
            "instagram": {
                "include_stories": "false"
            }
        }
    }

    # Public cookies file can optionally be supplied.
    # DO NOT put secrets/tokens directly in this code.
    cookies_path = os.path.join(
        os.getcwd(),
        "cookies.txt"
    )

    if os.path.exists(cookies_path):
        ydl_opts["cookiefile"] = cookies_path
        log("yt-dlp: cookies.txt detected")

    try:

        log(f"YT-DLP START: {url}")

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            ydl.download([url])

        after = set(
            glob.glob(
                os.path.join(folder, "*")
            )
        )

        new_files = list(
            after - before
        )

        valid = []

        for path in new_files:

            if not os.path.isfile(path):
                continue

            if is_too_large(path):
                remove_file(path)
                continue

            if os.path.splitext(
                path
            )[1].lower() in (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                ".gif"
            ):

                if not is_image_file(path):
                    remove_file(path)
                    continue

            valid.append(path)

        log(
            f"YT-DLP files: {len(valid)}"
        )

        return valid

    except Exception as e:

        log(
            f"YT-DLP ERROR: {e}"
        )

        return []


# =========================================================
# INSTAGRAM MAIN
# =========================================================

def download_instagram(url, folder):

    log("================================")
    log("INSTAGRAM")
    log("================================")

    # -----------------------------------------------------
    # 1. RAPIDAPI
    # -----------------------------------------------------

    files = rapidapi_instagram(
        url,
        folder
    )

    if files:
        log(
            f"Instagram SUCCESS via RapidAPI: "
            f"{len(files)} files"
        )

        return files

    log(
        "Instagram: RapidAPI produced no usable media"
    )

    # -----------------------------------------------------
    # 2. EMBED
    # -----------------------------------------------------

    embed_urls = instagram_embed_urls(
        url
    )

    if embed_urls:

        log(
            f"Instagram EMBED candidates: "
            f"{len(embed_urls)}"
        )

        files = instagram_from_urls(
            embed_urls,
            folder
        )

        if files:
            log(
                f"Instagram SUCCESS via EMBED: "
                f"{len(files)} files"
            )

            return files

    # -----------------------------------------------------
    # 3. NORMAL HTML
    # -----------------------------------------------------

    html_urls = instagram_html_urls(
        url
    )

    if html_urls:

        log(
            f"Instagram HTML candidates: "
            f"{len(html_urls)}"
        )

        files = instagram_from_urls(
            html_urls,
            folder
        )

        if files:
            log(
                f"Instagram SUCCESS via HTML: "
                f"{len(files)} files"
            )

            return files

    # -----------------------------------------------------
    # 4. YT-DLP
    # -----------------------------------------------------

    log(
        "Instagram: HTML failed -> yt-dlp"
    )

    files = yt_dlp_download(
        url,
        folder
    )

    if files:
        return files

    return []


# =========================================================
# TIKTOK
# =========================================================

def tiktok_download(url, folder):

    log("================================")
    log("TIKTOK")
    log("================================")

    # Try yt-dlp first
    files = yt_dlp_download(
        url,
        folder
    )

    if files:
        return files

    return []


# =========================================================
# FACEBOOK / X
# =========================================================

def generic_social_download(url, folder):

    log("================================")
    log(f"GENERIC: {detect_platform(url)}")
    log("================================")

    # HTML meta extraction first
    try:

        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9"
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        if response.status_code == 200:

            soup = BeautifulSoup(
                response.text,
                "html.parser"
            )

            candidates = []

            for tag in soup.find_all("meta"):

                prop = (
                    tag.get("property")
                    or tag.get("name")
                    or ""
                ).lower()

                content = tag.get("content")

                if not content:
                    continue

                if prop in (
                    "og:image",
                    "og:video",
                    "og:video:url",
                    "og:video:secure_url",
                    "twitter:image"
                ):

                    if looks_like_media_url(content):
                        candidates.append(content)

            files = instagram_from_urls(
                candidates,
                folder
            )

            if files:
                return files

    except Exception as e:
        log(f"Generic HTML error: {e}")

    # yt-dlp
    return yt_dlp_download(
        url,
        folder
    )


# =========================================================
# DOWNLOAD DISPATCHER
# =========================================================

def download_url(url, folder):

    platform = detect_platform(url)

    log(
        f"Platform detected: {platform}"
    )

    if platform == "instagram":
        return download_instagram(
            url,
            folder
        )

    if platform == "tiktok":
        return tiktok_download(
            url,
            folder
        )

    if platform in (
        "facebook",
        "twitter"
    ):
        return generic_social_download(
            url,
            folder
        )

    return yt_dlp_download(
        url,
        folder
    )


# =========================================================
# TELEGRAM SENDING
# =========================================================

async def send_one(message, path):

    if not os.path.exists(path):
        return False

    ext = os.path.splitext(
        path
    )[1].lower()

    # -----------------------------------------------------
    # VIDEO
    # -----------------------------------------------------

    if ext in (
        ".mp4",
        ".mov",
        ".m4v",
        ".webm",
        ".mkv"
    ):

        try:

            await message.reply_video(
                video=path,
                supports_streaming=True
            )

            return True

        except Exception as e:

            log(
                f"Video send failed: {e}"
            )

            try:

                await message.reply_document(
                    document=path
                )

                return True

            except Exception as e2:

                log(
                    f"Video document fallback failed: "
                    f"{e2}"
                )

                return False

    # -----------------------------------------------------
    # IMAGE
    # -----------------------------------------------------

    if ext in (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp"
    ):

        folder = os.path.dirname(path)

        jpg = convert_to_jpg(
            path,
            folder
        )

        if not jpg:
            return False

        try:

            await message.reply_photo(
                photo=jpg
            )

            if jpg != path:
                remove_file(jpg)

            return True

        except Exception as e:

            log(
                f"Photo send failed: {e}"
            )

            try:

                await message.reply_document(
                    document=jpg
                )

                if jpg != path:
                    remove_file(jpg)

                return True

            except Exception as e2:

                log(
                    f"Photo document fallback failed: "
                    f"{e2}"
                )

                if jpg != path:
                    remove_file(jpg)

                return False

    # -----------------------------------------------------
    # OTHER
    # -----------------------------------------------------

    try:

        await message.reply_document(
            document=path
        )

        return True

    except Exception as e:

        log(
            f"Document send failed: {e}"
        )

        return False


# =========================================================
# SEND ALL FILES
# =========================================================

async def send_files(message, files):

    sent = 0

    for path in files:

        if sent >= MAX_MEDIA_PER_LINK:
            break

        try:

            ok = await send_one(
                message,
                path
            )

            if ok:
                sent += 1

        except Exception as e:

            log(
                f"Send error: {e}"
            )

        await asyncio.sleep(0.5)

    return sent


# =========================================================
# CLEAN JOB
# =========================================================

def cleanup_folder(folder):

    try:

        if os.path.exists(folder):
            shutil.rmtree(folder)

    except Exception as e:

        log(
            f"Cleanup error: {e}"
        )


# =========================================================
# URL EXTRACTION FROM TELEGRAM MESSAGE
# =========================================================

URL_PATTERN = re.compile(
    r"https?://[^\s<>\"]+",
    re.IGNORECASE
)


def extract_urls(text):

    if not text:
        return []

    urls = URL_PATTERN.findall(
        text
    )

    final = []

    for url in urls:

        url = clean_url(url)

        if is_valid_url(url):
            final.append(url)

    return final


# =========================================================
# MESSAGE HANDLER
# =========================================================

@app.on_message(
    filters.private
    & filters.text
)
async def handle_message(client, message):

    urls = extract_urls(
        message.text or ""
    )

    if not urls:

        await message.reply_text(
            "🔗 أرسل رابط Instagram أو TikTok أو Facebook أو X."
        )

        return

    # Only process first 5 URLs per message
    urls = urls[:5]

    for url in urls:

        platform = detect_platform(
            url
        )

        job_id = (
            f"{message.chat.id}_"
            f"{message.id}_"
            f"{abs(hash(url))}"
        )

        folder = os.path.join(
            DOWNLOAD_DIR,
            str(job_id)
        )

        os.makedirs(
            folder,
            exist_ok=True
        )

        status = await message.reply_text(
            f"⏳ جاري استخراج محتوى {platform.upper()}..."
        )

        log("================================")
        log(f"NEW URL: {url}")
        log(f"PLATFORM: {platform}")
        log("================================")

        try:

            files = await asyncio.to_thread(
                download_url,
                url,
                folder
            )

            if not files:

                await status.edit_text(
                    "❌ ماكدرت أستخرج المحتوى من الرابط.\n\n"
                    "ممكن المنشور خاص أو الموقع مانع الوصول "
                    "أو الرابط غير مدعوم حاليًا."
                )

                cleanup_folder(
                    folder
                )

                continue

            # Limit files
            files = files[
                :MAX_MEDIA_PER_LINK
            ]

            await status.edit_text(
                f"📦 تم استخراج {len(files)} ملف.\n"
                f"📤 جاري الإرسال..."
            )

            sent = await send_files(
                message,
                files
            )

            await status.edit_text(
                f"✅ تم إرسال {sent} ملف."
            )

        except Exception as e:

            log(
                f"JOB ERROR: {e}"
            )

            try:

                await status.edit_text(
                    "❌ صار خطأ أثناء معالجة الرابط."
                )

            except Exception:
                pass

        finally:

            cleanup_folder(
                folder
            )


# =========================================================
# STARTUP
# =========================================================

async def startup_log():

    log("================================")
    log("TELEGRAM DOWNLOADER BOT STARTING")
    log("================================")

    log(
        f"RapidAPI configured: "
        f"{bool(RAPIDAPI_KEY)}"
    )

    log(
        f"Download directory: "
        f"{DOWNLOAD_DIR}"
    )

    log(
        f"Max media per link: "
        f"{MAX_MEDIA_PER_LINK}"
    )

    log("================================")


if __name__ == "__main__":

    os.makedirs(
        DOWNLOAD_DIR,
        exist_ok=True
    )

    asyncio.get_event_loop().run_until_complete(
        startup_log()
    )

    app.run()

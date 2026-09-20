import os
import re
import json
import uuid
import asyncio
import logging
import html as html_lib
from urllib.parse import urlparse, unquote

import requests
import yt_dlp
import imageio_ffmpeg

from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InputMediaPhoto,
    InputMediaVideo
)


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

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "ht4h4"
)

DOWNLOAD_DIR = "downloads"

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# BOT
# =========================================================

app = Client(
    "downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# =========================================================
# FFMPEG
# =========================================================

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()


# =========================================================
# HEADERS
# =========================================================

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
}


# =========================================================
# URL HELPERS
# =========================================================

def clean_url(url: str) -> str:

    if not url:
        return ""

    url = url.strip()

    try:

        parsed = urlparse(url)

        clean = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path}"
        )

        return clean.rstrip("/")

    except Exception:

        return url


def is_instagram_url(url: str) -> bool:

    if not url:
        return False

    value = url.lower()

    return (
        "instagram.com/" in value
        or "instagr.am/" in value
    )


def is_tiktok_url(url: str) -> bool:

    if not url:
        return False

    value = url.lower()

    return (
        "tiktok.com/" in value
        or "vm.tiktok.com/" in value
        or "vt.tiktok.com/" in value
    )


def get_extension_from_response(
    response,
    default="jpg"
):

    content_type = (
        response.headers
        .get("Content-Type", "")
        .lower()
        .split(";")[0]
        .strip()
    )

    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/quicktime": "mov",
    }

    return mapping.get(
        content_type,
        default
    )


def is_allowed_media_url(url: str) -> bool:

    if not url:
        return False

    try:

        parsed = urlparse(url)

        host = parsed.netloc.lower()

        allowed_hosts = [
            "instagram.com",
            "cdninstagram.com",
            "fbcdn.net",
            "facebook.com",
            "fbsbx.com",
        ]

        if any(
            host == item
            or host.endswith("." + item)
            for item in allowed_hosts
        ):
            return True

        if (
            "instagram" in host
            or "fbcdn" in host
        ):
            return True

        return False

    except Exception:

        return False


def normalize_media_url(url: str) -> str:

    if not isinstance(url, str):
        return ""

    url = html_lib.unescape(url)

    url = url.replace("\\/", "/")
    url = url.replace("\\u0026", "&")
    url = url.replace("\\u003D", "=")
    url = url.replace("\\u002F", "/")
    url = url.replace("&amp;", "&")

    try:
        url = unquote(url)
    except Exception:
        pass

    return url.strip()


# =========================================================
# MEDIA DOWNLOADER
# =========================================================

def download_media_url(
    media_url,
    prefix="ig_image",
    default_ext="jpg",
    is_video=False
):

    media_url = normalize_media_url(
        media_url
    )

    if not media_url:
        return None

    try:

        headers = {
            "User-Agent":
                BROWSER_HEADERS["User-Agent"],
            "Referer":
                "https://www.instagram.com/"
        }

        response = requests.get(
            media_url,
            headers=headers,
            timeout=90,
            stream=True
        )

        response.raise_for_status()

        content_type = (
            response.headers
            .get("Content-Type", "")
            .lower()
        )

        if (
            not is_video
            and "image/" not in content_type
        ):

            logger.warning(
                "Media URL did not return image: "
                f"{content_type}"
            )

            return None

        if is_video:

            if (
                "video/" not in content_type
                and ".mp4" not in media_url.lower()
            ):

                logger.warning(
                    "Media URL did not return video: "
                    f"{content_type}"
                )

                return None

        ext = get_extension_from_response(
            response,
            default_ext
        )

        if is_video:

            if ext not in [
                "mp4",
                "webm",
                "mov",
                "mkv"
            ]:
                ext = "mp4"

        else:

            if ext not in [
                "jpg",
                "jpeg",
                "png",
                "webp"
            ]:
                ext = "jpg"

        path = os.path.join(
            DOWNLOAD_DIR,
            f"{prefix}_"
            f"{uuid.uuid4().hex}."
            f"{ext}"
        )

        total = 0

        with open(path, "wb") as file:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_FILE_SIZE:

                    logger.warning(
                        "Media exceeds maximum "
                        "file size"
                    )

                    file.close()

                    try:
                        os.remove(path)
                    except Exception:
                        pass

                    return None

                file.write(chunk)

        if (
            os.path.exists(path)
            and os.path.getsize(path) > 0
        ):

            logger.info(
                f"Media saved: {path}"
            )

            return path

        return None

    except Exception as e:

        logger.error(
            f"Media download failed: {e}"
        )

        return None


# =========================================================
# RAPIDAPI HELPERS
# =========================================================

def _collect_media_dicts(
    obj,
    results=None
):

    if results is None:
        results = []

    if isinstance(obj, dict):

        possible_keys = [
            "url",
            "download_url",
            "video_url",
            "image_url",
            "display_url",
            "src",
            "media_url",
        ]

        for key in possible_keys:

            value = obj.get(key)

            if isinstance(value, str):

                value = normalize_media_url(
                    value
                )

                if value.startswith("http"):

                    results.append({
                        "url": value,
                        "type": (
                            "video"
                            if (
                                "video" in key.lower()
                                or ".mp4" in value.lower()
                                or "video" in value.lower()
                            )
                            else "image"
                        )
                    })

        for value in obj.values():

            _collect_media_dicts(
                value,
                results
            )

    elif isinstance(obj, list):

        for item in obj:

            _collect_media_dicts(
                item,
                results
            )

    return results


def _find_media_list(obj):

    if isinstance(obj, dict):

        for key, value in obj.items():

            key_lower = str(key).lower()

            if isinstance(value, list):

                if any(
                    word in key_lower
                    for word in [
                        "media",
                        "items",
                        "images",
                        "videos",
                        "result"
                    ]
                ):

                    return value

                found = _find_media_list(
                    value
                )

                if found:
                    return found

            elif isinstance(value, dict):

                found = _find_media_list(
                    value
                )

                if found:
                    return found

    elif isinstance(obj, list):

        return obj

    return None


# =========================================================
# RAPIDAPI INSTAGRAM
# =========================================================

def fetch_instagram_media(url):

    if not RAPIDAPI_KEY:

        logger.warning(
            "RAPIDAPI_KEY is not configured"
        )

        return []

    clean = clean_url(url)

    endpoint = (
        f"https://{RAPIDAPI_HOST}/instagram/"
    )

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "User-Agent":
            BROWSER_HEADERS["User-Agent"]
    }

    params = {
        "url": clean
    }

    try:

        logger.info(
            f"Instagram RapidAPI request: "
            f"{clean}"
        )

        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=45
        )

        logger.info(
            f"Instagram API status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            return []

        try:

            data = response.json()

        except Exception:

            logger.error(
                "Instagram API returned "
                "invalid JSON"
            )

            return []

        logger.info(
            "Instagram API response: "
            + json.dumps(
                data,
                ensure_ascii=False
            )[:1200]
        )

        if isinstance(data, dict):

            if data.get("status") is False:

                logger.warning(
                    "Instagram API returned "
                    f"status=false: "
                    f"{data.get('message')}"
                )

                return []

        media_objects = (
            _collect_media_dicts(data)
        )

        if not media_objects:

            media_list = _find_media_list(
                data
            )

            if media_list:

                media_objects = (
                    _collect_media_dicts(
                        media_list
                    )
                )

        unique = []
        seen = set()

        for item in media_objects:

            media_url = normalize_media_url(
                item.get("url", "")
            )

            if not media_url:
                continue

            if media_url in seen:
                continue

            seen.add(media_url)

            unique.append({
                "url": media_url,
                "type": item.get(
                    "type",
                    "image"
                )
            })

        if not unique:

            logger.warning(
                "Instagram API returned no media"
            )

            return []

        downloaded = []

        for item in unique:

            media_url = item["url"]

            media_type = item.get(
                "type",
                "image"
            )

            is_video = (
                media_type == "video"
                or ".mp4" in media_url.lower()
                or "video" in media_url.lower()
            )

            path = download_media_url(
                media_url,
                prefix=(
                    "ig_video"
                    if is_video
                    else "ig_image"
                ),
                default_ext=(
                    "mp4"
                    if is_video
                    else "jpg"
                ),
                is_video=is_video
            )

            if path:

                downloaded.append(
                    (
                        path,
                        is_video
                    )
                )

        return downloaded

    except Exception as e:

        logger.error(
            f"Instagram RapidAPI failed: {e}"
        )

        return []


# =========================================================
# INSTAGRAM HTML / JSON EXTRACTION
# =========================================================

def extract_instagram_urls_from_text(
    text
):

    if not text:
        return []

    results = []

    patterns = [
        (
            r'"display_url"\s*:\s*"([^"]+)"',
            "image"
        ),
        (
            r'"thumbnail_url"\s*:\s*"([^"]+)"',
            "image"
        ),
        (
            r'"video_url"\s*:\s*"([^"]+)"',
            "video"
        ),
        (
            r'"contentUrl"\s*:\s*"([^"]+)"',
            "image"
        ),
        (
            r'"image_url"\s*:\s*"([^"]+)"',
            "image"
        ),
    ]

    for pattern, media_type in patterns:

        try:

            matches = re.findall(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            for match in matches:

                media_url = normalize_media_url(
                    match
                )

                if (
                    media_url.startswith("http")
                    and is_allowed_media_url(
                        media_url
                    )
                ):

                    results.append({
                        "url": media_url,
                        "type": media_type
                    })

        except Exception:
            pass

    # image_versions2 / direct CDN URLs
    try:

        direct_urls = re.findall(
            r'https?://[^"\'<>\s\\]+',
            text
        )

        for raw_url in direct_urls:

            media_url = normalize_media_url(
                raw_url
            )

            if not is_allowed_media_url(
                media_url
            ):
                continue

            lower = media_url.lower()

            if (
                ".jpg" in lower
                or ".jpeg" in lower
                or ".png" in lower
                or ".webp" in lower
                or "scontent" in lower
            ):

                results.append({
                    "url": media_url,
                    "type": "image"
                })

            elif (
                ".mp4" in lower
                or "video" in lower
            ):

                results.append({
                    "url": media_url,
                    "type": "video"
                })

    except Exception:
        pass

    return results


def fetch_instagram_html_media(url):

    clean = clean_url(url)

    session = requests.Session()

    session.headers.update(
        BROWSER_HEADERS
    )

    try:

        logger.info(
            "Instagram HTML extractor: "
            f"{clean}"
        )

        response = session.get(
            clean,
            timeout=45,
            allow_redirects=True
        )

        logger.info(
            f"Instagram HTML status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            return []

        page = response.text

        if not page:
            return []

        logger.info(
            "Instagram HTML length: "
            f"{len(page)}"
        )

        soup = BeautifulSoup(
            page,
            "html.parser"
        )

        candidates = []

        # -------------------------------------------------
        # META TAGS
        # -------------------------------------------------

        selectors = [
            (
                "meta[property='og:image']",
                "image"
            ),
            (
                "meta[name='og:image']",
                "image"
            ),
            (
                "meta[property='og:image:url']",
                "image"
            ),
            (
                "meta[property='og:video']",
                "video"
            ),
            (
                "meta[property='og:video:url']",
                "video"
            ),
        ]

        for selector, media_type in selectors:

            for tag in soup.select(
                selector
            ):

                media_url = normalize_media_url(
                    tag.get("content", "")
                )

                if (
                    media_url.startswith("http")
                    and is_allowed_media_url(
                        media_url
                    )
                ):

                    candidates.append({
                        "url": media_url,
                        "type": media_type
                    })

        # -------------------------------------------------
        # WHOLE PAGE
        # -------------------------------------------------

        candidates.extend(
            extract_instagram_urls_from_text(
                page
            )
        )

        # -------------------------------------------------
        # SCRIPT TAGS
        # -------------------------------------------------

        for script in soup.find_all(
            "script"
        ):

            script_text = script.string

            if not script_text:

                script_text = script.get_text(
                    strip=False
                )

            if not script_text:
                continue

            candidates.extend(
                extract_instagram_urls_from_text(
                    script_text
                )
            )

        # -------------------------------------------------
        # UNIQUE
        # -------------------------------------------------

        unique = []
        seen = set()

        for item in candidates:

            media_url = normalize_media_url(
                item.get("url", "")
            )

            if not media_url:
                continue

            if media_url in seen:
                continue

            if not is_allowed_media_url(
                media_url
            ):
                continue

            seen.add(media_url)

            unique.append({
                "url": media_url,
                "type": item.get(
                    "type",
                    "image"
                )
            })

        logger.info(
            "Instagram HTML candidates: "
            f"{len(unique)}"
        )

        if not unique:
            return []

        downloaded = []

        # لا ننزل عدد غير محدود من الروابط
        for item in unique[:30]:

            media_url = item["url"]

            media_type = item.get(
                "type",
                "image"
            )

            is_video = (
                media_type == "video"
                or ".mp4" in media_url.lower()
            )

            path = download_media_url(
                media_url,
                prefix=(
                    "ig_html_video"
                    if is_video
                    else "ig_html_image"
                ),
                default_ext=(
                    "mp4"
                    if is_video
                    else "jpg"
                ),
                is_video=is_video
            )

            if path:

                downloaded.append(
                    (
                        path,
                        is_video
                    )
                )

        return downloaded

    except Exception as e:

        logger.error(
            "Instagram HTML extractor "
            f"failed: {e}"
        )

        return []


# =========================================================
# OG IMAGE FALLBACK
# =========================================================

def fetch_instagram_single_image(url):

    clean = clean_url(url)

    try:

        logger.info(
            "Instagram single-image fallback: "
            f"{clean}"
        )

        response = requests.get(
            clean,
            headers=BROWSER_HEADERS,
            timeout=30,
            allow_redirects=True
        )

        logger.info(
            f"Instagram page status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        selectors = [
            "meta[property='og:image']",
            "meta[name='og:image']",
            "meta[property='og:image:url']",
        ]

        image_url = None

        for selector in selectors:

            tag = soup.select_one(
                selector
            )

            if tag:

                image_url = tag.get(
                    "content"
                )

                if image_url:
                    break

        if not image_url:

            logger.warning(
                "Instagram single image: "
                "og:image not found"
            )

            return []

        image_url = normalize_media_url(
            image_url
        )

        if not image_url.startswith(
            "http"
        ):
            return []

        if not is_allowed_media_url(
            image_url
        ):
            return []

        path = download_media_url(
            image_url,
            prefix="ig_single",
            default_ext="jpg",
            is_video=False
        )

        if path:

            return [
                (
                    path,
                    False
                )
            ]

        return []

    except Exception as e:

        logger.error(
            "Instagram single-image "
            f"fallback failed: {e}"
        )

        return []


# =========================================================
# TIKTOK
# =========================================================

def fetch_tiktok_media(url):

    clean = clean_url(url)

    endpoint = (
        "https://www.tikwm.com/api/"
    )

    headers = {
        "User-Agent":
            BROWSER_HEADERS["User-Agent"]
    }

    params = {
        "url": clean,
        "hd": "1"
    }

    try:

        logger.info(
            f"TikTok TikWM request: {clean}"
        )

        response = requests.get(
            endpoint,
            params=params,
            headers=headers,
            timeout=45
        )

        logger.info(
            f"TikTok API status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            return []

        data = response.json()

        if not isinstance(data, dict):
            return []

        result = data.get("data")

        if not isinstance(
            result,
            dict
        ):
            return []

        images = result.get(
            "images"
        )

        if not isinstance(
            images,
            list
        ):
            return []

        if not images:
            return []

        downloaded = []

        for image_url in images:

            if not isinstance(
                image_url,
                str
            ):
                continue

            try:

                r = requests.get(
                    image_url,
                    headers=headers,
                    timeout=60,
                    stream=True
                )

                r.raise_for_status()

                ext = get_extension_from_response(
                    r,
                    "jpg"
                )

                if ext not in [
                    "jpg",
                    "jpeg",
                    "png",
                    "webp"
                ]:

                    ext = "jpg"

                path = os.path.join(
                    DOWNLOAD_DIR,
                    f"tiktok_image_"
                    f"{uuid.uuid4().hex}."
                    f"{ext}"
                )

                with open(
                    path,
                    "wb"
                ) as file:

                    for chunk in r.iter_content(
                        chunk_size=1024 * 1024
                    ):

                        if chunk:
                            file.write(chunk)

                if (
                    os.path.exists(path)
                    and os.path.getsize(path) > 0
                ):

                    downloaded.append(
                        (
                            path,
                            False
                        )
                    )

            except Exception as e:

                logger.error(
                    f"TikTok image failed: {e}"
                )

        return downloaded

    except Exception as e:

        logger.error(
            f"TikTok API failed: {e}"
        )

        return []


# =========================================================
# YT-DLP
# =========================================================

def build_ytdlp_options(
    output_template
):

    options = {
        "outtmpl": output_template,

        "format": (
            "bestvideo+bestaudio/"
            "best"
        ),

        "merge_output_format": "mp4",

        "ffmpeg_location": FFMPEG_PATH,

        "noplaylist": False,

        "quiet": True,

        "no_warnings": False,

        "retries": 3,

        "fragment_retries": 3,

        "socket_timeout": 30,

        "concurrent_fragment_downloads": 4,

        "http_headers": {
            "User-Agent":
                BROWSER_HEADERS[
                    "User-Agent"
                ]
        },

        "restrictfilenames": True,

        "windowsfilenames": True,

        "ignoreerrors": False,
    }

    if os.path.exists(
        "cookies.txt"
    ):

        options["cookiefile"] = (
            "cookies.txt"
        )

    return options


def download_with_ytdlp(url):

    unique_id = uuid.uuid4().hex

    output_template = os.path.join(
        DOWNLOAD_DIR,
        f"download_{unique_id}.%(ext)s"
    )

    options = build_ytdlp_options(
        output_template
    )

    try:

        logger.info(
            f"yt-dlp downloading: {url}"
        )

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

        if not info:
            return []

        results = []

        entries = info.get(
            "entries"
        )

        if entries:

            for entry in entries:

                if not entry:
                    continue

                requested = (
                    entry.get(
                        "requested_downloads"
                    )
                    or []
                )

                for item in requested:

                    filepath = item.get(
                        "filepath"
                    )

                    if (
                        filepath
                        and os.path.exists(
                            filepath
                        )
                    ):

                        results.append(
                            (
                                filepath,
                                True
                            )
                        )

                filepath = entry.get(
                    "_filename"
                )

                if (
                    filepath
                    and os.path.exists(
                        filepath
                    )
                ):

                    results.append(
                        (
                            filepath,
                            True
                        )
                    )

            unique = []
            seen = set()

            for item in results:

                if item[0] in seen:
                    continue

                seen.add(item[0])

                unique.append(item)

            return unique

        requested = (
            info.get(
                "requested_downloads"
            )
            or []
        )

        for item in requested:

            filepath = item.get(
                "filepath"
            )

            if (
                filepath
                and os.path.exists(
                    filepath
                )
            ):

                return [
                    (
                        filepath,
                        True
                    )
                ]

        filepath = info.get(
            "_filename"
        )

        if (
            filepath
            and os.path.exists(
                filepath
            )
        ):

            return [
                (
                    filepath,
                    True
                )
            ]

        prefix = (
            f"download_{unique_id}."
        )

        for filename in os.listdir(
            DOWNLOAD_DIR
        ):

            if filename.startswith(
                prefix
            ):

                filepath = os.path.join(
                    DOWNLOAD_DIR,
                    filename
                )

                if os.path.isfile(
                    filepath
                ):

                    return [
                        (
                            filepath,
                            True
                        )
                    ]

        return []

    except Exception as e:

        logger.error(
            f"yt-dlp failed: {e}"
        )

        return []


# =========================================================
# TELEGRAM SEND
# =========================================================

async def send_media_items(
    message: Message,
    media_items
):

    if not media_items:
        return False

    valid_items = []

    for path, is_video in media_items:

        if not os.path.exists(path):
            continue

        if os.path.getsize(path) <= 0:
            continue

        valid_items.append(
            (
                path,
                is_video
            )
        )

    if not valid_items:
        return False

    # Telegram media groups max 10
    for start in range(
        0,
        len(valid_items),
        10
    ):

        batch = valid_items[
            start:start + 10
        ]

        # -------------------------------------------------
        # SINGLE FILE
        # -------------------------------------------------

        if len(batch) == 1:

            path, is_video = batch[0]

            try:

                if is_video:

                    await message.reply_video(
                        video=path,
                        supports_streaming=True
                    )

                else:

                    size = os.path.getsize(
                        path
                    )

                    if size <= 10 * 1024 * 1024:

                        await message.reply_photo(
                            photo=path
                        )

                    else:

                        await message.reply_document(
                            document=path
                        )

                continue

            except Exception as e:

                logger.error(
                    "Single Telegram upload "
                    f"failed: {e}"
                )

                return False

        # -------------------------------------------------
        # MEDIA GROUP
        # -------------------------------------------------

        media_group = []

        for path, is_video in batch:

            try:

                if is_video:

                    media_group.append(
                        InputMediaVideo(
                            media=path
                        )
                    )

                else:

                    media_group.append(
                        InputMediaPhoto(
                            media=path
                        )
                    )

            except Exception as e:

                logger.error(
                    f"Creating media failed: {e}"
                )

        if not media_group:
            continue

        try:

            await message.reply_media_group(
                media=media_group
            )

        except Exception as e:

            logger.error(
                f"Media group upload failed: {e}"
            )

            # fallback
            for path, is_video in batch:

                try:

                    if is_video:

                        await message.reply_video(
                            video=path,
                            supports_streaming=True
                        )

                    else:

                        size = os.path.getsize(
                            path
                        )

                        if size <= 10 * 1024 * 1024:

                            await message.reply_photo(
                                photo=path
                            )

                        else:

                            await message.reply_document(
                                document=path
                            )

                except Exception as fallback_error:

                    logger.error(
                        "Fallback upload failed: "
                        f"{fallback_error}"
                    )

    return True


# =========================================================
# CLEANUP
# =========================================================

def cleanup_files(
    media_items
):

    for item in media_items:

        try:

            path = item[0]

            if (
                path
                and os.path.exists(path)
            ):

                os.remove(path)

                logger.info(
                    f"Deleted: {path}"
                )

        except Exception as e:

            logger.warning(
                f"Cleanup failed: {e}"
            )


# =========================================================
# START
# =========================================================

@app.on_message(
    filters.command("start")
)
async def start_handler(
    client,
    message: Message
):

    await message.reply_text(
        "👋 هلا بيك!\n\n"
        "🤖 أرسل رابط المحتوى وأنا أحاول "
        "تحميله لك.\n\n"
        "📸 Instagram\n"
        "🎵 TikTok\n"
        "▶️ YouTube والمواقع المدعومة "
        "من yt-dlp"
    )


# =========================================================
# HELP
# =========================================================

@app.on_message(
    filters.command("help")
)
async def help_handler(
    client,
    message: Message
):

    await message.reply_text(
        "📖 طريقة الاستخدام:\n\n"
        "أرسل الرابط فقط.\n\n"
        "Instagram:\n"
        "• صورة مفردة\n"
        "• Carousel\n"
        "• Reels\n"
        "• فيديو\n\n"
        "TikTok:\n"
        "• فيديو\n"
        "• صور Carousel"
    )


# =========================================================
# MAIN DOWNLOAD HANDLER
# =========================================================

@app.on_message(
    filters.text
    & ~filters.command(
        [
            "start",
            "help"
        ]
    )
)
async def download_handler(
    client,
    message: Message
):

    raw_text = message.text.strip()

    match = re.search(
        r"https?://[^\s]+",
        raw_text
    )

    if not match:

        await message.reply_text(
            "❌ أرسل رابط صحيح."
        )

        return

    url = match.group(0)

    url = url.rstrip(
        ".,!?)]}>\"'"
    )

    status_message = await message.reply_text(
        "⏳ جاري معالجة الرابط..."
    )

    media_items = []

    loop = asyncio.get_running_loop()

    try:

        # =================================================
        # INSTAGRAM
        # =================================================

        if is_instagram_url(url):

            # -------------------------------------------------
            # 1. RAPIDAPI
            # -------------------------------------------------

            await status_message.edit_text(
                "📸 جاري استخراج محتوى Instagram..."
            )

            media_items = await loop.run_in_executor(
                None,
                fetch_instagram_media,
                url
            )

            if media_items:

                await status_message.edit_text(
                    "🚀 تم استخراج المحتوى، "
                    "جاري رفعه..."
                )

                success = await send_media_items(
                    message,
                    media_items
                )

                if success:

                    await status_message.delete()

                else:

                    await status_message.edit_text(
                        "❌ حدث خطأ أثناء رفع "
                        "المحتوى."
                    )

                return

            # -------------------------------------------------
            # 2. HTML / JSON
            # -------------------------------------------------

            logger.warning(
                "RapidAPI returned no media. "
                "Trying Instagram HTML/JSON..."
            )

            await status_message.edit_text(
                "🔎 جاري البحث داخل بيانات Instagram..."
            )

            media_items = await loop.run_in_executor(
                None,
                fetch_instagram_html_media,
                url
            )

            if media_items:

                await status_message.edit_text(
                    "🚀 تم العثور على المحتوى، "
                    "جاري رفعه..."
                )

                success = await send_media_items(
                    message,
                    media_items
                )

                if success:

                    await status_message.delete()

                else:

                    await status_message.edit_text(
                        "❌ حدث خطأ أثناء الرفع."
                    )

                return

            # -------------------------------------------------
            # 3. OG IMAGE
            # -------------------------------------------------

            logger.warning(
                "HTML/JSON returned no media. "
                "Trying og:image..."
            )

            await status_message.edit_text(
                "🖼️ جاري محاولة استخراج الصورة..."
            )

            media_items = await loop.run_in_executor(
                None,
                fetch_instagram_single_image,
                url
            )

            if media_items:

                await status_message.edit_text(
                    "🚀 تم العثور على الصورة، "
                    "جاري رفعها..."
                )

                success = await send_media_items(
                    message,
                    media_items
                )

                if success:

                    await status_message.delete()

                else:

                    await status_message.edit_text(
                        "❌ حدث خطأ أثناء رفع الصورة."
                    )

                return

            # -------------------------------------------------
            # 4. YT-DLP
            # -------------------------------------------------

            logger.warning(
                "Instagram extraction failed. "
                "Trying yt-dlp..."
            )

            await status_message.edit_text(
                "🎬 جاري محاولة استخراج الفيديو..."
            )

            media_items = await loop.run_in_executor(
                None,
                download_with_ytdlp,
                url
            )

            if media_items:

                await status_message.edit_text(
                    "🚀 تم التحميل، جاري الرفع..."
                )

                success = await send_media_items(
                    message,
                    media_items
                )

                if success:

                    await status_message.delete()

                else:

                    await status_message.edit_text(
                        "❌ حدث خطأ أثناء رفع الفيديو."
                    )

                return

            await status_message.edit_text(
                "❌ لم أستطع استخراج محتوى هذا "
                "الرابط من Instagram.\n\n"
                "تأكد أن المنشور عام ويمكن فتحه "
                "بدون تسجيل دخول."
            )

            return

        # =================================================
        # TIKTOK
        # =================================================

        if is_tiktok_url(url):

            await status_message.edit_text(
                "🎵 جاري استخراج محتوى TikTok..."
            )

            # صور TikTok
            media_items = await loop.run_in_executor(
                None,
                fetch_tiktok_media,
                url
            )

            if media_items:

                await status_message.edit_text(
                    "🚀 تم استخراج الصور، "
                    "جاري رفعها..."
                )

                success = await send_media_items(
                    message,
                    media_items
                )

                if success:

                    await status_message.delete()

                else:

                    await status_message.edit_text(
                        "❌ حدث خطأ أثناء رفع الصور."
                    )

                return

            # فيديو TikTok
            await status_message.edit_text(
                "🎬 جاري تحميل فيديو TikTok..."
            )

            media_items = await loop.run_in_executor(
                None,
                download_with_ytdlp,
                url
            )

            if media_items:

                await status_message.edit_text(
                    "🚀 تم تحميل الفيديو، "
                    "جاري رفعه..."
                )

                success = await send_media_items(
                    message,
                    media_items
                )

                if success:

                    await status_message.delete()

                else:

                    await status_message.edit_text(
                        "❌ حدث خطأ أثناء رفع الفيديو."
                    )

                return

            await status_message.edit_text(
                "❌ لم أستطع تحميل هذا الرابط."
            )

            return

        # =================================================
        # OTHER WEBSITES
        # =================================================

        await status_message.edit_text(
            "🎬 جاري تحميل المحتوى..."
        )

        media_items = await loop.run_in_executor(
            None,
            download_with_ytdlp,
            url
        )

        if media_items:

            await status_message.edit_text(
                "🚀 تم التحميل، جاري الرفع..."
            )

            success = await send_media_items(
                message,
                media_items
            )

            if success:

                await status_message.delete()

            else:

                await status_message.edit_text(
                    "❌ حدث خطأ أثناء الرفع."
                )

            return

        await status_message.edit_text(
            "❌ لم أستطع تحميل هذا الرابط.\n\n"
            "تأكد أن الرابط عام ويعمل في المتصفح."
        )

    except Exception as e:

        logger.exception(
            f"Download handler error: {e}"
        )

        try:

            await status_message.edit_text(
                "❌ حدث خطأ أثناء التحميل."
            )

        except Exception:
            pass

    finally:

        if media_items:

            cleanup_files(
                media_items
            )


# =========================================================
# RUN BOT
# =========================================================

if __name__ == "__main__":

    logger.info(
        "Starting Telegram downloader bot..."
    )

    logger.info(
        f"FFmpeg: {FFMPEG_PATH}"
    )

    logger.info(
        f"Download directory: {DOWNLOAD_DIR}"
    )

    app.run()

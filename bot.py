import os
import re
import json
import uuid
import shutil
import asyncio
import logging
import mimetypes
from urllib.parse import urlparse

import requests
import yt_dlp
import imageio_ffmpeg

from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import Message


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

IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "ht4h4"
)

DOWNLOAD_DIR = "downloads"

# الحد الأقصى المعلن للملف
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

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
# URL HELPERS
# =========================================================

def clean_url(url: str) -> str:
    """
    تنظيف الرابط من بعض بارامترات التتبع.
    """

    if not url:
        return ""

    url = url.strip()

    try:
        parsed = urlparse(url)

        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        return clean.rstrip("/")

    except Exception:
        return url


def is_instagram_url(url: str) -> bool:
    if not url:
        return False

    url = url.lower()

    return (
        "instagram.com/" in url
        or "instagr.am/" in url
    )


def is_tiktok_url(url: str) -> bool:
    if not url:
        return False

    url = url.lower()

    return (
        "tiktok.com/" in url
        or "vm.tiktok.com/" in url
        or "vt.tiktok.com/" in url
    )


def get_extension_from_response(
    response,
    default="jpg"
):
    """
    محاولة معرفة امتداد الملف من Content-Type.
    """

    content_type = (
        response.headers.get(
            "Content-Type",
            ""
        )
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


# =========================================================
# GENERAL HTTP
# =========================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# =========================================================
# INSTAGRAM RAPIDAPI
# =========================================================

def _collect_media_dicts(obj, results=None):
    """
    البحث بشكل recursive داخل JSON عن objects
    التي تحتوي على URL لوسائط.
    """

    if results is None:
        results = []

    if isinstance(obj, dict):

        # نبحث عن مفاتيح URL الشائعة
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
    """
    يحاول العثور على قوائم media داخل استجابة RapidAPI.
    """

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

                found = _find_media_list(value)

                if found:
                    return found

            elif isinstance(value, dict):

                found = _find_media_list(value)

                if found:
                    return found

    elif isinstance(obj, list):

        return obj

    return None


def fetch_instagram_media(url):
    """
    محاولة استخراج صور وفيديوهات Instagram
    من RapidAPI.
    """

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
        "User-Agent": DEFAULT_HEADERS["User-Agent"],
    }

    params = {
        "url": clean
    }

    try:

        logger.info(
            f"Instagram RapidAPI request: {clean}"
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

            logger.warning(
                f"Instagram API HTTP error: "
                f"{response.status_code}"
            )

            return []

        try:
            data = response.json()

        except Exception as e:

            logger.error(
                f"Instagram API JSON error: {e}"
            )

            return []

        logger.info(
            "Instagram API response: "
            + json.dumps(
                data,
                ensure_ascii=False
            )[:1000]
        )

        # بعض APIs ترجع status=false حتى مع HTTP 200
        if isinstance(data, dict):

            if data.get("status") is False:

                logger.warning(
                    "Instagram API returned "
                    f"status=false: "
                    f"{data.get('message')}"
                )

                return []

        media_objects = _collect_media_dicts(data)

        if not media_objects:

            media_list = _find_media_list(data)

            if media_list:

                media_objects = (
                    _collect_media_dicts(
                        media_list
                    )
                )

        # إزالة التكرارات
        unique = []
        seen = set()

        for item in media_objects:

            media_url = item.get("url")

            if not media_url:
                continue

            if media_url in seen:
                continue

            seen.add(media_url)

            unique.append(item)

        if not unique:

            logger.warning(
                "Instagram API returned no media"
            )

            return []

        downloaded = []

        for index, item in enumerate(
            unique,
            start=1
        ):

            media_url = item.get("url")
            media_type = item.get(
                "type",
                "image"
            )

            try:

                logger.info(
                    f"Downloading Instagram media "
                    f"{index}: "
                    f"{media_url[:150]}"
                )

                r = requests.get(
                    media_url,
                    headers={
                        "User-Agent":
                            DEFAULT_HEADERS[
                                "User-Agent"
                            ]
                    },
                    timeout=90,
                    stream=True
                )

                r.raise_for_status()

                content_type = (
                    r.headers.get(
                        "Content-Type",
                        ""
                    ).lower()
                )

                is_video = (
                    media_type == "video"
                    or "video/" in content_type
                    or ".mp4" in media_url.lower()
                )

                ext = get_extension_from_response(
                    r,
                    "mp4" if is_video else "jpg"
                )

                if is_video:

                    allowed = [
                        "mp4",
                        "webm",
                        "mov",
                        "mkv"
                    ]

                    if ext not in allowed:
                        ext = "mp4"

                else:

                    allowed = [
                        "jpg",
                        "jpeg",
                        "png",
                        "webp"
                    ]

                    if ext not in allowed:
                        ext = "jpg"

                prefix = (
                    "ig_video"
                    if is_video
                    else "ig_image"
                )

                path = os.path.join(
                    DOWNLOAD_DIR,
                    f"{prefix}_"
                    f"{uuid.uuid4().hex}."
                    f"{ext}"
                )

                total = 0

                with open(path, "wb") as f:

                    for chunk in r.iter_content(
                        chunk_size=1024 * 1024
                    ):

                        if not chunk:
                            continue

                        total += len(chunk)

                        if total > MAX_FILE_SIZE:

                            logger.warning(
                                "Instagram media exceeds "
                                "maximum file size"
                            )

                            f.close()

                            try:
                                os.remove(path)
                            except Exception:
                                pass

                            continue

                        f.write(chunk)

                if (
                    os.path.exists(path)
                    and os.path.getsize(path) > 0
                ):

                    downloaded.append(
                        (
                            path,
                            is_video
                        )
                    )

                    logger.info(
                        f"Instagram media saved: "
                        f"{path}"
                    )

            except Exception as e:

                logger.error(
                    f"Instagram media download "
                    f"failed: {e}"
                )

        return downloaded

    except Exception as e:

        logger.error(
            f"Instagram RapidAPI failed: {e}"
        )

        return []


# =========================================================
# INSTAGRAM SINGLE IMAGE FALLBACK
# =========================================================

def fetch_instagram_single_image(url):
    """
    محاولة تحميل صورة Instagram المفردة
    من الصفحة العامة باستخدام og:image.

    هذا مهم لأن yt-dlp مخصص أساساً للفيديو/الصوت
    ولا يتعامل حالياً مع image-only Instagram posts.
    """

    clean = clean_url(url)

    headers = {
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

    try:

        logger.info(
            f"Instagram single-image fallback: "
            f"{clean}"
        )

        response = requests.get(
            clean,
            headers=headers,
            timeout=30,
            allow_redirects=True
        )

        logger.info(
            f"Instagram page status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            logger.warning(
                "Instagram page returned "
                f"HTTP {response.status_code}"
            )

            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # الطريقة الأولى
        og_image = soup.find(
            "meta",
            property="og:image"
        )

        # الطريقة الثانية
        if not og_image:

            og_image = soup.find(
                "meta",
                attrs={
                    "name": "og:image"
                }
            )

        # طريقة إضافية لبعض صفحات Instagram
        if not og_image:

            og_image = soup.find(
                "meta",
                attrs={
                    "property": "og:image:url"
                }
            )

        if not og_image:

            logger.warning(
                "Instagram single image: "
                "og:image not found"
            )

            return []

        image_url = og_image.get(
            "content"
        )

        if not image_url:

            logger.warning(
                "Instagram og:image has no content"
            )

            return []

        logger.info(
            "Instagram og:image found: "
            f"{image_url[:150]}"
        )

        image_response = requests.get(
            image_url,
            headers={
                "User-Agent":
                    DEFAULT_HEADERS[
                        "User-Agent"
                    ],
                "Referer": clean
            },
            timeout=60,
            stream=True
        )

        image_response.raise_for_status()

        content_type = (
            image_response.headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        if "image" not in content_type:

            logger.warning(
                f"URL is not image: "
                f"{content_type}"
            )

            return []

        ext = get_extension_from_response(
            image_response,
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
            f"ig_single_"
            f"{uuid.uuid4().hex}."
            f"{ext}"
        )

        total = 0

        with open(path, "wb") as f:

            for chunk in image_response.iter_content(
                chunk_size=1024 * 1024
            ):

                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_FILE_SIZE:

                    logger.warning(
                        "Instagram single image "
                        "exceeds maximum file size"
                    )

                    f.close()

                    try:
                        os.remove(path)
                    except Exception:
                        pass

                    return []

                f.write(chunk)

        if not os.path.exists(path):

            return []

        if os.path.getsize(path) <= 0:

            try:
                os.remove(path)
            except Exception:
                pass

            return []

        logger.info(
            f"Instagram single image saved: "
            f"{path}"
        )

        return [
            (
                path,
                False
            )
        ]

    except Exception as e:

        logger.error(
            "Instagram single-image "
            f"fallback failed: {e}"
        )

        return []


# =========================================================
# TIKTOK TIKWM
# =========================================================

def fetch_tiktok_media(url):
    """
    محاولة استخراج صور TikTok باستخدام TikWM.
    """

    clean = clean_url(url)

    endpoint = "https://www.tikwm.com/api/"

    headers = {
        "User-Agent":
            DEFAULT_HEADERS["User-Agent"]
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

        if data.get("code") not in [
            0,
            "0",
            None
        ]:

            logger.warning(
                f"TikTok API error: "
                f"{data}"
            )

            return []

        result = data.get("data")

        if not isinstance(result, dict):

            return []

        images = result.get("images")

        if not isinstance(images, list):
            return []

        if not images:
            return []

        downloaded = []

        for index, image_url in enumerate(
            images,
            start=1
        ):

            if not isinstance(
                image_url,
                str
            ):
                continue

            try:

                logger.info(
                    f"Downloading TikTok image "
                    f"{index}"
                )

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

                with open(path, "wb") as f:

                    for chunk in r.iter_content(
                        chunk_size=1024 * 1024
                    ):

                        if chunk:
                            f.write(chunk)

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
                    f"TikTok image download "
                    f"failed: {e}"
                )

        return downloaded

    except Exception as e:

        logger.error(
            f"TikTok TikWM failed: {e}"
        )

        return []


# =========================================================
# YT-DLP
# =========================================================

def build_video_opts(output_template):
    """
    إعدادات yt-dlp للفيديوهات.
    """

    opts = {
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
                DEFAULT_HEADERS[
                    "User-Agent"
                ]
        },

        "restrictfilenames": True,

        "windowsfilenames": True,

        "ignoreerrors": False,
    }

    # إذا عندك cookies.txt في Railway
    # سيتم استخدامها تلقائياً.
    if os.path.exists("cookies.txt"):

        opts["cookiefile"] = "cookies.txt"

    return opts


def download_with_ytdlp(url):
    """
    تحميل الفيديو باستخدام yt-dlp.
    """

    unique_id = uuid.uuid4().hex

    output_template = os.path.join(
        DOWNLOAD_DIR,
        f"download_{unique_id}.%(ext)s"
    )

    opts = build_video_opts(
        output_template
    )

    try:

        logger.info(
            f"yt-dlp downloading: {url}"
        )

        with yt_dlp.YoutubeDL(opts) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

        if not info:

            return []

        # في حالة playlist
        entries = info.get("entries")

        if entries:

            results = []

            for entry in entries:

                if not entry:
                    continue

                requested = (
                    entry.get(
                        "requested_downloads"
                    )
                    or []
                )

                candidates = []

                for item in requested:

                    filepath = item.get(
                        "filepath"
                    )

                    if filepath:
                        candidates.append(
                            filepath
                        )

                if not candidates:

                    filepath = (
                        entry.get(
                            "_filename"
                        )
                        or entry.get(
                            "filepath"
                        )
                    )

                    if filepath:
                        candidates.append(
                            filepath
                        )

                for filepath in candidates:

                    if os.path.exists(filepath):

                        results.append(
                            (
                                filepath,
                                True
                            )
                        )

            # إزالة التكرار
            unique_results = []
            seen = set()

            for item in results:

                if item[0] in seen:
                    continue

                seen.add(item[0])

                unique_results.append(
                    item
                )

            return unique_results

        # فيديو واحد
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
                and os.path.exists(filepath)
            ):

                return [
                    (
                        filepath,
                        True
                    )
                ]

        # fallback
        filepath = info.get(
            "_filename"
        )

        if (
            filepath
            and os.path.exists(filepath)
        ):

            return [
                (
                    filepath,
                    True
                )
            ]

        # البحث عن الملف الذي تم إنشاؤه
        base = os.path.join(
            DOWNLOAD_DIR,
            f"download_{unique_id}"
        )

        for filename in os.listdir(
            DOWNLOAD_DIR
        ):

            if filename.startswith(
                f"download_{unique_id}."
            ):

                filepath = os.path.join(
                    DOWNLOAD_DIR,
                    filename
                )

                if os.path.isfile(filepath):

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
# SEND MEDIA TO TELEGRAM
# =========================================================

async def send_media_items(
    message: Message,
    media_items
):
    """
    إرسال الصور والفيديوهات إلى Telegram.

    Telegram media group يسمح بحد أقصى 10 عناصر
    في المجموعة الواحدة.
    """

    if not media_items:

        return False

    # تنظيف الملفات غير الموجودة
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

    # تقسيم إلى مجموعات 10
    for start in range(
        0,
        len(valid_items),
        10
    ):

        batch = valid_items[
            start:start + 10
        ]

        # إذا عنصر واحد فقط
        if len(batch) == 1:

            path, is_video = batch[0]

            try:

                if is_video:

                    await message.reply_video(
                        video=path,
                        supports_streaming=True
                    )

                else:

                    # الصور الصغيرة ترسل كصورة
                    # والكبيرة كمستند
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

            except Exception as e:

                logger.error(
                    f"Telegram single upload "
                    f"failed: {e}"
                )

                return False

            continue

        # أكثر من عنصر
        media_group = []

        from pyrogram.types import (
            InputMediaPhoto,
            InputMediaVideo
        )

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
                    f"Could not create media "
                    f"item: {e}"
                )

        if not media_group:
            continue

        try:

            await message.reply_media_group(
                media=media_group
            )

        except Exception as e:

            logger.error(
                f"Telegram media group "
                f"upload failed: {e}"
            )

            # fallback:
            # إرسال العناصر بشكل منفرد
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

                        if (
                            size
                            <= 10 * 1024 * 1024
                        ):

                            await message.reply_photo(
                                photo=path
                            )

                        else:

                            await message.reply_document(
                                document=path
                            )

                except Exception as single_error:

                    logger.error(
                        "Fallback upload failed: "
                        f"{single_error}"
                    )

    return True


# =========================================================
# CLEANUP
# =========================================================

def cleanup_files(media_items):
    """
    حذف الملفات بعد إرسالها.
    """

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
    client: Client,
    message: Message
):

    text = (
        "👋 هلا بيك!\n\n"
        "🤖 هذا البوت يحمل الفيديوهات "
        "والصور من:\n\n"
        "📸 Instagram\n"
        "🎵 TikTok\n"
        "▶️ YouTube والمواقع المدعومة "
        "من yt-dlp\n\n"
        "📎 فقط أرسل الرابط هنا."
    )

    await message.reply_text(
        text
    )


# =========================================================
# HELP
# =========================================================

@app.on_message(
    filters.command("help")
)
async def help_handler(
    client: Client,
    message: Message
):

    text = (
        "📖 طريقة الاستخدام:\n\n"
        "1️⃣ انسخ رابط المنشور أو الفيديو.\n"
        "2️⃣ أرسله إلى البوت.\n"
        "3️⃣ انتظر إلى أن يتم استخراج "
        "الوسائط ورفعها.\n\n"
        "📸 Instagram:\n"
        "• صورة مفردة\n"
        "• Carousel\n"
        "• فيديو\n"
        "• Reels\n\n"
        "🎵 TikTok:\n"
        "• فيديو\n"
        "• صور Carousel\n\n"
        "🔗 والمواقع الأخرى المدعومة "
        "من yt-dlp."
    )

    await message.reply_text(
        text
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
    client: Client,
    message: Message
):

    url = message.text.strip()

    # استخراج أول رابط من الرسالة
    url_match = re.search(
        r"https?://[^\s]+",
        url
    )

    if not url_match:

        await message.reply_text(
            "❌ أرسل رابط صحيح."
        )

        return

    url = url_match.group(0)

    # تنظيف علامات شائعة تأتي بعد الرابط
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

            await status_message.edit_text(
                "📸 جاري استخراج محتوى Instagram..."
            )

            # ---------------------------------------------
            # 1. RapidAPI
            # ---------------------------------------------

            media_items = await loop.run_in_executor(
                None,
                fetch_instagram_media,
                url
            )

            if media_items:

                await status_message.edit_text(
                    "🚀 تم استخراج المحتوى، "
                    "جاري رفعه إلى Telegram..."
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
                        "المحتوى إلى Telegram."
                    )

                return

            # ---------------------------------------------
            # 2. SINGLE IMAGE FALLBACK
            # ---------------------------------------------

            logger.warning(
                "RapidAPI returned no media. "
                "Trying single-image fallback..."
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

            # ---------------------------------------------
            # 3. yt-dlp
            # ---------------------------------------------

            logger.warning(
                "Instagram image extraction failed. "
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
                "❌ لم أستطع استخراج محتوى "
                "هذا الرابط من Instagram."
            )

            return

        # =================================================
        # TIKTOK
        # =================================================

        if is_tiktok_url(url):

            await status_message.edit_text(
                "🎵 جاري استخراج محتوى TikTok..."
            )

            # أولاً نحاول صور TikTok
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

            # إذا ليس Carousel صور
            # نستخدم yt-dlp للفيديو

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
        # OTHER WEBSITES -> YT-DLP
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
                "🚀 تم التحميل، "
                "جاري رفع الملف..."
            )

            success = await send_media_items(
                message,
                media_items
            )

            if success:

                await status_message.delete()

            else:

                await status_message.edit_text(
                    "❌ حدث خطأ أثناء رفع الملف."
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
                "❌ حدث خطأ أثناء التحميل.\n\n"
                f"الخطأ: {str(e)[:500]}"
            )

        except Exception:
            pass

    finally:

        # حذف الملفات بعد الانتهاء
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
        f"Download directory: "
        f"{DOWNLOAD_DIR}"
    )

    app.run()

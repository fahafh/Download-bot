import os
import re
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
RAPIDAPI_HOST = (
    "instagram-post-reels-stories-downloader-api.p.rapidapi.com"
)

DOWNLOAD_DIR = "downloads"

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

MAX_MEDIA_PER_LINK = 10

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# =========================================================
# TELEGRAM
# =========================================================

app = Client(
    "telegram_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


# =========================================================
# LOG
# =========================================================

def log(message):
    print(f"[BOT] {message}", flush=True)


# =========================================================
# HTTP
# =========================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept": "*/*",
}


# =========================================================
# URL HELPERS
# =========================================================

URL_REGEX = re.compile(
    r"https?://[^\s<>]+",
    re.IGNORECASE,
)


def clean_url(url):
    if not url:
        return ""

    url = url.strip()

    url = url.strip(
        "<>[](){}\"'"
    )

    url = url.rstrip(
        ".,!?;:)]}>\"'"
    )

    return url


def extract_url(text):
    if not text:
        return None

    matches = URL_REGEX.findall(text)

    if not matches:
        return None

    return clean_url(matches[0])


def get_domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_instagram(url):
    d = get_domain(url)

    return (
        "instagram.com" in d
        or "instagr.am" in d
    )


def is_tiktok(url):
    d = get_domain(url)

    return (
        "tiktok.com" in d
        or "vm.tiktok.com" in d
    )


def is_facebook(url):
    d = get_domain(url)

    return (
        "facebook.com" in d
        or "fb.watch" in d
        or "fb.com" in d
    )


def is_x(url):
    d = get_domain(url)

    return (
        "x.com" in d
        or "twitter.com" in d
        or "mobile.twitter.com" in d
    )


# =========================================================
# FILE HELPERS
# =========================================================

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".heic",
    ".heif",
}


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".m4v",
    ".ts",
    ".flv",
}


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def safe_filename(name):
    name = re.sub(
        r'[\\/*?:"<>|]+',
        "_",
        name,
    )

    name = name.strip()

    return name[:150] or "media"


def create_job():
    return tempfile.mkdtemp(
        prefix="job_",
        dir=DOWNLOAD_DIR,
    )


def cleanup(path):
    if not path:
        return

    try:
        if os.path.exists(path):
            shutil.rmtree(
                path,
                ignore_errors=True,
            )
    except Exception as e:
        log(f"CLEANUP ERROR: {e}")


# =========================================================
# IMAGE VALIDATION
# =========================================================

def validate_image(path):
    try:

        if not os.path.exists(path):
            return False

        size = os.path.getsize(path)

        if size < 1000:
            return False

        if size > MAX_FILE_SIZE:
            return False

        with Image.open(path) as img:

            width, height = img.size

            # Reject tiny icons/avatars/favicons
            if width < 150 or height < 150:
                log(
                    f"IMAGE REJECTED: "
                    f"too small {width}x{height}"
                )
                return False

            img.verify()

        return True

    except Exception as e:

        log(
            f"IMAGE REJECTED: {e}"
        )

        return False


# =========================================================
# IMAGE -> JPG
# =========================================================

def convert_to_jpg(path):

    if not os.path.exists(path):
        return None

    ext = os.path.splitext(path)[1].lower()

    if ext in (".jpg", ".jpeg"):

        if validate_image(path):
            return path

        return None

    output = (
        os.path.splitext(path)[0]
        + "_telegram.jpg"
    )

    try:

        with Image.open(path) as img:

            img = ImageOps.exif_transpose(img)

            try:
                img.seek(0)
            except Exception:
                pass

            if img.mode in (
                "RGBA",
                "LA",
                "P",
            ):

                if img.mode == "P":
                    img = img.convert("RGBA")

                background = Image.new(
                    "RGB",
                    img.size,
                    "white",
                )

                if img.mode in (
                    "RGBA",
                    "LA",
                ):

                    background.paste(
                        img,
                        mask=img.getchannel("A"),
                    )

                else:

                    background.paste(img)

                img = background

            else:

                img = img.convert("RGB")

            max_dimension = 10000

            if max(img.size) > max_dimension:

                ratio = (
                    max_dimension
                    / max(img.size)
                )

                new_size = (
                    int(img.width * ratio),
                    int(img.height * ratio),
                )

                img = img.resize(
                    new_size,
                    Image.Resampling.LANCZOS,
                )

            img.save(
                output,
                "JPEG",
                quality=95,
                optimize=True,
            )

        if validate_image(output):
            return output

    except Exception as e:

        log(
            f"IMAGE CONVERSION ERROR: {e}"
        )

    return None


# =========================================================
# BAD ASSETS
# =========================================================

def is_bad_asset(url):

    low = url.lower()

    bad_words = (
        "favicon",
        "sprite",
        "profile_pic",
        "profilepic",
        "avatar",
        "default_avatar",
        "placeholder",
        "logo",
        "icon",
    )

    return any(
        word in low
        for word in bad_words
    )


def is_media_url(url):

    if not url:
        return False

    if not (
        url.startswith("http://")
        or url.startswith("https://")
    ):
        return False

    if is_bad_asset(url):
        return False

    return True


# =========================================================
# DIRECT DOWNLOAD
# =========================================================

def download_direct(
    url,
    job_dir,
    referer="",
):
    try:

        if not is_media_url(url):
            return None

        headers = dict(HEADERS)

        if referer:
            headers["Referer"] = referer

        log(
            f"DIRECT: {url[:220]}"
        )

        response = requests.get(
            url,
            headers=headers,
            timeout=45,
            stream=True,
            allow_redirects=True,
        )

        log(
            f"DIRECT RESPONSE: "
            f"{response.status_code} "
            f"{response.headers.get('Content-Type')}"
        )

        response.raise_for_status()

        content_type = (
            response.headers
            .get("Content-Type", "")
            .lower()
            .split(";")[0]
        )

        if content_type.startswith(
            "text/html"
        ):
            return None

        extension = (
            mimetypes.guess_extension(
                content_type
            )
        )

        if not extension:

            parsed = urlparse(
                response.url
            )

            original = unquote(
                os.path.basename(
                    parsed.path
                )
            )

            extension = os.path.splitext(
                original
            )[1]

        if not extension:
            extension = ".bin"

        if extension == ".jpe":
            extension = ".jpg"

        filename = safe_filename(
            f"media_{abs(hash(url))}"
        )

        path = os.path.join(
            job_dir,
            filename + extension,
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

                    log(
                        "DIRECT: file too large"
                    )

                    try:
                        os.remove(path)
                    except Exception:
                        pass

                    return None

                file.write(chunk)

        if not os.path.exists(path):
            return None

        if os.path.getsize(path) == 0:
            os.remove(path)
            return None

        if is_image(path):

            if not validate_image(path):

                try:
                    os.remove(path)
                except Exception:
                    pass

                return None

        return path

    except Exception as e:

        log(
            f"DIRECT ERROR: {e}"
        )

        return None


# =========================================================
# META EXTRACTION
# =========================================================

def extract_meta_urls(
    html,
    page_url,
):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    images = []
    videos = []

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

        value = urljoin(
            page_url,
            value,
        )

        if key in image_keys:

            if (
                is_media_url(value)
                and value not in images
            ):
                images.append(value)

        elif key in video_keys:

            if (
                is_media_url(value)
                and value not in videos
            ):
                videos.append(value)

    return images, videos


# =========================================================
# SOCIAL CDN EXTRACTION
# =========================================================

def extract_cdn_urls(
    html,
    page_url,
):
    images = []
    videos = []

    # Facebook image/CDN URLs
    image_patterns = [

        r'https?:\\?/\\?/scontent[^"\'<>\s]+',

        r'https?:\\?/\\?/lookaside\.fbsbx\.com[^"\'<>\s]+',

        r'https?:\\?/\\?/external[^"\'<>\s]+',

        r'https?:\\?/\\?/pbs\.twimg\.com[^"\'<>\s]+',

    ]

    video_patterns = [

        r'https?:\\?/\\?/video[^"\'<>\s]+',

        r'https?:\\?/\\?/video\.twimg\.com[^"\'<>\s]+',

    ]

    for pattern in image_patterns:

        try:

            matches = re.findall(
                pattern,
                html,
                re.IGNORECASE,
            )

            for value in matches:

                value = value.replace(
                    "\\/",
                    "/",
                )

                value = value.replace(
                    "\\u0026",
                    "&",
                )

                value = urljoin(
                    page_url,
                    value,
                )

                if (
                    is_media_url(value)
                    and value not in images
                ):
                    images.append(value)

        except Exception:
            pass

    for pattern in video_patterns:

        try:

            matches = re.findall(
                pattern,
                html,
                re.IGNORECASE,
            )

            for value in matches:

                value = value.replace(
                    "\\/",
                    "/",
                )

                value = value.replace(
                    "\\u0026",
                    "&",
                )

                value = urljoin(
                    page_url,
                    value,
                )

                if (
                    is_media_url(value)
                    and value not in videos
                ):
                    videos.append(value)

        except Exception:
            pass

    return images, videos


# =========================================================
# FACEBOOK / X HTML
# =========================================================

def social_html_download(
    url,
    job_dir,
):
    try:

        headers = dict(HEADERS)
        headers["Referer"] = url

        response = requests.get(
            url,
            headers=headers,
            timeout=35,
            allow_redirects=True,
        )

        log(
            f"SOCIAL HTML: "
            f"{response.status_code} "
            f"length={len(response.text)}"
        )

        if response.status_code != 200:
            return []

        html = response.text

        meta_images, meta_videos = (
            extract_meta_urls(
                html,
                response.url,
            )
        )

        cdn_images, cdn_videos = (
            extract_cdn_urls(
                html,
                response.url,
            )
        )

        image_urls = []
        video_urls = []

        for value in (
            meta_images + cdn_images
        ):

            if value not in image_urls:
                image_urls.append(value)

        for value in (
            meta_videos + cdn_videos
        ):

            if value not in video_urls:
                video_urls.append(value)

        log(
            f"SOCIAL HTML: "
            f"{len(image_urls)} images, "
            f"{len(video_urls)} videos"
        )

        results = []

        for media_url in image_urls[:10]:

            path = download_direct(
                media_url,
                job_dir,
                response.url,
            )

            if path and is_image(path):

                results.append(
                    (path, False)
                )

        for media_url in video_urls[:5]:

            path = download_direct(
                media_url,
                job_dir,
                response.url,
            )

            if path and is_video(path):

                results.append(
                    (path, True)
                )

        return results

    except Exception as e:

        log(
            f"SOCIAL HTML ERROR: {e}"
        )

        return []


# =========================================================
# INSTAGRAM RAPIDAPI
# =========================================================

def collect_instagram_media(
    obj,
    output=None,
    score=0,
):
    if output is None:
        output = []

    if isinstance(obj, dict):

        for key, value in obj.items():

            key_name = str(
                key
            ).lower()

            current_score = score

            if any(
                item in key_name
                for item in (
                    "display_url",
                    "displayurl",
                    "image_url",
                    "imageurl",
                    "media_url",
                    "mediaurl",
                    "video_url",
                    "videourl",
                    "download_url",
                    "downloadurl",
                    "photo_url",
                    "photourl",
                )
            ):
                current_score = max(
                    current_score,
                    10,
                )

            elif any(
                item in key_name
                for item in (
                    "image",
                    "photo",
                    "video",
                    "thumbnail",
                )
            ):
                current_score = max(
                    current_score,
                    5,
                )

            if isinstance(value, str):

                value = value.strip()

                if (
                    value.startswith(
                        "https://"
                    )
                    or value.startswith(
                        "http://"
                    )
                ):

                    if (
                        current_score >= 5
                        and is_media_url(value)
                    ):

                        output.append(
                            (
                                current_score,
                                value,
                            )
                        )

            else:

                collect_instagram_media(
                    value,
                    output,
                    current_score,
                )

    elif isinstance(obj, list):

        for item in obj:

            collect_instagram_media(
                item,
                output,
                score,
            )

    return output


def instagram_rapidapi(
    url,
    job_dir,
):
    if not RAPIDAPI_KEY:

        log(
            "INSTAGRAM: RAPIDAPI_KEY missing"
        )

        return []

    try:

        endpoint = (
            f"https://{RAPIDAPI_HOST}/instagram/"
        )

        headers = {
            "x-rapidapi-key": RAPIDAPI_KEY,
            "x-rapidapi-host": RAPIDAPI_HOST,
        }

        response = requests.get(
            endpoint,
            headers=headers,
            params={"url": url},
            timeout=45,
        )

        log(
            f"INSTAGRAM RAPIDAPI: "
            f"{response.status_code} "
            f"length={len(response.text)}"
        )

        if response.status_code != 200:
            return []

        try:

            data = response.json()

        except Exception:

            return []

        candidates = (
            collect_instagram_media(data)
        )

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        log(
            f"INSTAGRAM: "
            f"{len(candidates)} candidates"
        )

        results = []
        seen = set()

        for score, media_url in candidates:

            if media_url in seen:
                continue

            seen.add(media_url)

            log(
                f"IG candidate "
                f"score={score}: "
                f"{media_url[:180]}"
            )

            path = download_direct(
                media_url,
                job_dir,
                url,
            )

            if not path:
                continue

            if is_image(path):

                results.append(
                    (path, False)
                )

            elif is_video(path):

                results.append(
                    (path, True)
                )

            if len(results) >= MAX_MEDIA_PER_LINK:
                break

        return results

    except Exception as e:

        log(
            f"INSTAGRAM RAPIDAPI ERROR: {e}"
        )

        return []


# =========================================================
# INSTAGRAM HTML FALLBACK
# =========================================================

def instagram_html(
    url,
    job_dir,
):
    try:

        headers = dict(HEADERS)

        headers["Referer"] = (
            "https://www.instagram.com/"
        )

        response = requests.get(
            url,
            headers=headers,
            timeout=35,
        )

        log(
            f"INSTAGRAM HTML: "
            f"{response.status_code} "
            f"length={len(response.text)}"
        )

        if response.status_code != 200:
            return []

        images, videos = extract_meta_urls(
            response.text,
            response.url,
        )

        results = []

        for media_url in images[:5]:

            path = download_direct(
                media_url,
                job_dir,
                response.url,
            )

            if path and is_image(path):

                results.append(
                    (path, False)
                )

        for media_url in videos[:3]:

            path = download_direct(
                media_url,
                job_dir,
                response.url,
            )

            if path and is_video(path):

                results.append(
                    (path, True)
                )

        return results

    except Exception as e:

        log(
            f"INSTAGRAM HTML ERROR: {e}"
        )

        return []


# =========================================================
# TIKTOK
# =========================================================

def tiktok_api(
    url,
    job_dir,
):
    try:

        response = requests.get(
            "https://www.tikwm.com/api/",
            params={
                "url": url,
                "hd": 1,
            },
            headers=HEADERS,
            timeout=45,
        )

        log(
            f"TIKTOK API: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            return []

        data = response.json()

        payload = data.get(
            "data"
        ) or {}

        results = []

        # -----------------------------
        # VIDEO
        # -----------------------------

        for key in (
            "hdplay",
            "play",
            "wmplay",
            "download",
        ):

            value = payload.get(key)

            if (
                isinstance(value, str)
                and value.startswith("http")
            ):

                path = download_direct(
                    value,
                    job_dir,
                    "https://www.tiktok.com/",
                )

                if path and is_video(path):

                    results.append(
                        (path, True)
                    )

                    break

        # -----------------------------
        # PHOTOS
        # -----------------------------

        images = payload.get(
            "images"
        )

        if isinstance(images, list):

            for image_url in images[:10]:

                if not isinstance(
                    image_url,
                    str,
                ):
                    continue

                path = download_direct(
                    image_url,
                    job_dir,
                    "https://www.tiktok.com/",
                )

                if path and is_image(path):

                    results.append(
                        (path, False)
                    )

        return results

    except Exception as e:

        log(
            f"TIKTOK API ERROR: {e}"
        )

        return []


# =========================================================
# YT-DLP
# =========================================================

def ytdlp_download(
    url,
    job_dir,
):
    log(
        f"YT-DLP START: {url}"
    )

    before = set(
        glob.glob(
            os.path.join(
                job_dir,
                "**",
                "*",
            ),
            recursive=True,
        )
    )

    ffmpeg = (
        imageio_ffmpeg
        .get_ffmpeg_exe()
    )

    options = {

        "outtmpl": os.path.join(
            job_dir,
            "%(title).80s_%(id)s.%(ext)s",
        ),

        "format": (
            "bestvideo*+bestaudio/"
            "best"
        ),

        "merge_output_format": "mp4",

        "ffmpeg_location": ffmpeg,

        "noplaylist": True,

        "quiet": False,

        "no_warnings": False,

        "retries": 3,

        "fragment_retries": 3,

        "socket_timeout": 30,

        "http_headers": HEADERS,

        "restrictfilenames": True,
    }

    if os.path.exists(
        "cookies.txt"
    ):

        options["cookiefile"] = (
            "cookies.txt"
        )

        log(
            "YT-DLP: cookies.txt enabled"
        )

    try:

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True,
            )

            if info:

                log(
                    "YT-DLP: "
                    f"{info.get('title', 'unknown')}"
                )

        after = set(
            glob.glob(
                os.path.join(
                    job_dir,
                    "**",
                    "*",
                ),
                recursive=True,
            )
        )

        files = [
            path
            for path in (after - before)
            if os.path.isfile(path)
        ]

        results = []

        for path in files:

            if path.endswith(
                (
                    ".part",
                    ".ytdl",
                )
            ):
                continue

            try:

                size = os.path.getsize(
                    path
                )

            except Exception:
                continue

            if size == 0:
                continue

            if size > MAX_FILE_SIZE:
                continue

            if is_video(path):

                results.append(
                    (path, True)
                )

            elif is_image(path):

                if validate_image(path):

                    results.append(
                        (path, False)
                    )

        log(
            f"YT-DLP RESULT: "
            f"{len(results)} files"
        )

        return results

    except Exception as e:

        log(
            f"YT-DLP ERROR: "
            f"{type(e).__name__}: {e}"
        )

        return []


# =========================================================
# PLATFORM EXTRACTION
# =========================================================

async def extract_media(url):

    job_dir = create_job()

    results = []

    try:

        # =================================================
        # INSTAGRAM
        # =================================================

        if is_instagram(url):

            log(
                "========== INSTAGRAM =========="
            )

            results = await asyncio.to_thread(
                instagram_rapidapi,
                url,
                job_dir,
            )

            if not results:

                log(
                    "IG: RapidAPI failed"
                )

                results = await asyncio.to_thread(
                    instagram_html,
                    url,
                    job_dir,
                )

            if not results:

                log(
                    "IG: HTML failed -> yt-dlp"
                )

                results = await asyncio.to_thread(
                    ytdlp_download,
                    url,
                    job_dir,
                )

        # =================================================
        # FACEBOOK
        # =================================================

        elif is_facebook(url):

            log(
                "========== FACEBOOK =========="
            )

            results = await asyncio.to_thread(
                social_html_download,
                url,
                job_dir,
            )

            if not results:

                log(
                    "FB: HTML failed -> yt-dlp"
                )

                results = await asyncio.to_thread(
                    ytdlp_download,
                    url,
                    job_dir,
                )

        # =================================================
        # X / TWITTER
        # =================================================

        elif is_x(url):

            log(
                "========== X / TWITTER =========="
            )

            results = await asyncio.to_thread(
                social_html_download,
                url,
                job_dir,
            )

            if not results:

                log(
                    "X: HTML failed -> yt-dlp"
                )

                results = await asyncio.to_thread(
                    ytdlp_download,
                    url,
                    job_dir,
                )

        # =================================================
        # TIKTOK
        # =================================================

        elif is_tiktok(url):

            log(
                "========== TIKTOK =========="
            )

            results = await asyncio.to_thread(
                tiktok_api,
                url,
                job_dir,
            )

            if not results:

                log(
                    "TikTok API failed -> yt-dlp"
                )

                results = await asyncio.to_thread(
                    ytdlp_download,
                    url,
                    job_dir,
                )

        # =================================================
        # GENERIC
        # =================================================

        else:

            log(
                "========== GENERIC =========="
            )

            results = await asyncio.to_thread(
                ytdlp_download,
                url,
                job_dir,
            )

        # =================================================
        # DEDUPLICATE
        # =================================================

        unique = []

        seen = set()

        for path, video in results:

            if not os.path.exists(path):
                continue

            real_path = os.path.realpath(
                path
            )

            if real_path in seen:
                continue

            seen.add(
                real_path
            )

            unique.append(
                (path, video)
            )

        return job_dir, unique

    except Exception as e:

        log(
            f"EXTRACT ERROR: {e}"
        )

        return job_dir, []


# =========================================================
# TELEGRAM SEND
# =========================================================

async def send_one(
    message,
    path,
    video,
):
    if not os.path.exists(path):
        return False

    try:

        size = os.path.getsize(
            path
        )

        if size > MAX_FILE_SIZE:

            await message.reply_text(
                "❌ الملف أكبر من الحد المسموح."
            )

            return False

        # =================================================
        # VIDEO
        # =================================================

        if video or is_video(path):

            try:

                await message.reply_video(
                    path,
                    supports_streaming=True,
                )

                log(
                    "TELEGRAM: video sent"
                )

                return True

            except Exception as e:

                log(
                    f"VIDEO SEND ERROR: {e}"
                )

                try:

                    await message.reply_document(
                        path
                    )

                    log(
                        "TELEGRAM: video sent as document"
                    )

                    return True

                except Exception as e2:

                    log(
                        f"VIDEO DOCUMENT ERROR: {e2}"
                    )

                    return False

        # =================================================
        # IMAGE
        # =================================================

        jpg = await asyncio.to_thread(
            convert_to_jpg,
            path,
        )

        if not jpg:

            log(
                "TELEGRAM: image conversion failed"
            )

            return False

        try:

            await message.reply_photo(
                jpg
            )

            log(
                "TELEGRAM: photo sent"
            )

            return True

        except Exception as e:

            log(
                f"PHOTO SEND ERROR: {e}"
            )

            # Fallback
            try:

                await message.reply_document(
                    jpg
                )

                log(
                    "TELEGRAM: photo sent as document"
                )

                return True

            except Exception as e2:

                log(
                    f"PHOTO DOCUMENT ERROR: {e2}"
                )

                return False

    except Exception as e:

        log(
            f"SEND ERROR: {e}"
        )

        return False


# =========================================================
# SEND ALL
# =========================================================

async def send_all(
    message,
    media,
):
    sent = 0

    media = media[
        :MAX_MEDIA_PER_LINK
    ]

    for path, video in media:

        if not os.path.exists(path):
            continue

        if await send_one(
            message,
            path,
            video,
        ):

            sent += 1

        await asyncio.sleep(
            0.5
        )

    return sent


# =========================================================
# MESSAGE HANDLER
# =========================================================

@app.on_message(
    filters.private & filters.text
)
async def handle_message(
    client,
    message,
):
    url = extract_url(
        message.text
    )

    if not url:

        await message.reply_text(
            "📎 أرسل رابط Instagram أو "
            "TikTok أو Facebook أو X/Twitter."
        )

        return

    log(
        "================================"
    )

    log(
        f"NEW URL: {url}"
    )

    log(
        "================================"
    )

    status = await message.reply_text(
        "⏳ جاري الفحص..."
    )

    job_dir = None

    try:

        if is_instagram(url):

            await status.edit_text(
                "📸 جاري استخراج محتوى Instagram..."
            )

        elif is_tiktok(url):

            await status.edit_text(
                "🎵 جاري استخراج محتوى TikTok..."
            )

        elif is_facebook(url):

            await status.edit_text(
                "📘 جاري استخراج محتوى Facebook..."
            )

        elif is_x(url):

            await status.edit_text(
                "𝕏 جاري استخراج محتوى X/Twitter..."
            )

        else:

            await status.edit_text(
                "⬇️ جاري تحميل المحتوى..."
            )

        job_dir, media = await extract_media(
            url
        )

        if not media:

            await status.edit_text(
                "❌ ما گدرت أستخرج المحتوى من الرابط.\n\n"
                "ممكن المنشور خاص أو الموقع منع الوصول "
                "أو الرابط غير مدعوم حاليًا."
            )

            return

        log(
            f"MEDIA FOUND: {len(media)}"
        )

        await status.edit_text(
            f"📦 تم العثور على {len(media)} ملف.\n"
            "📤 جاري الإرسال..."
        )

        sent = await send_all(
            message,
            media,
        )

        log(
            f"SENT: {sent}/{len(media)}"
        )

        if sent:

            await status.edit_text(
                f"✅ تم إرسال {sent} ملف."
            )

        else:

            await status.edit_text(
                "❌ حصلت الملفات لكن فشل إرسالها."
            )

    except Exception as e:

        log(
            f"HANDLER ERROR: {type(e).__name__}: {e}"
        )

        try:

            await status.edit_text(
                "❌ حدث خطأ أثناء المعالجة."
            )

        except Exception:
            pass

    finally:

        cleanup(
            job_dir
        )

        log(
            "================================"
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    log(
        "========================================"
    )

    log(
        "TELEGRAM DOWNLOADER BOT STARTING"
    )

    log(
        f"RapidAPI configured: "
        f"{bool(RAPIDAPI_KEY)}"
    )

    log(
        f"Download directory: "
        f"{DOWNLOAD_DIR}"
    )

    log(
        "========================================"
    )

    app.run()

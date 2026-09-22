import os
import re
import uuid
import shutil
import asyncio
import logging
import mimetypes
from urllib.parse import urlparse

import requests
import yt_dlp
import imageio_ffmpeg

from pyrogram import Client, filters
from pyrogram.types import Message


# =========================
# CONFIG
# =========================

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = os.getenv(
    "RAPIDAPI_HOST",
    "instagram-post-reels-stories-downloader-api.p.rapidapi.com"
)

DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("downloader")


# =========================
# BOT
# =========================

app = Client(
    "media_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# =========================
# HELPERS
# =========================

def is_url(text):
    return bool(re.match(r"^https?://", text.strip(), re.I))


def is_instagram_url(url):
    host = urlparse(url).netloc.lower()
    return "instagram.com" in host or "instagr.am" in host


def is_tiktok_url(url):
    host = urlparse(url).netloc.lower()
    return (
        "tiktok.com" in host
        or "vt.tiktok.com" in host
        or "vm.tiktok.com" in host
    )


def is_tiktok_photo(url):
    return "/photo/" in urlparse(url).path.lower()


def get_content_extension(response, default=".jpg"):
    content_type = response.headers.get("content-type", "").lower()

    if "mp4" in content_type:
        return ".mp4"
    if "webm" in content_type:
        return ".webm"
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"

    return default


def is_video_file(path):
    return path.lower().endswith(
        (".mp4", ".mkv", ".webm", ".mov", ".avi")
    )


def is_image_file(path):
    return path.lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp")
    )


def file_size(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


# =========================
# PRIVATE DOWNLOAD FOLDER
# =========================

def create_job_folder():
    folder = os.path.join(
        DOWNLOAD_DIR,
        uuid.uuid4().hex
    )
    os.makedirs(folder, exist_ok=True)
    return folder


def cleanup_folder(folder):
    try:
        if os.path.exists(folder):
            shutil.rmtree(folder, ignore_errors=True)
    except Exception as e:
        log.warning("Cleanup error: %s", e)


# =========================
# RAPIDAPI INSTAGRAM
# =========================

def extract_instagram_urls(data):
    """
    Extract only URLs that look like actual media URLs.
    """

    found = []

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_lower = str(key).lower()

                if isinstance(value, str):
                    if (
                        value.startswith("http://")
                        or value.startswith("https://")
                    ):
                        media_key = any(
                            x in key_lower
                            for x in (
                                "video",
                                "image",
                                "media",
                                "download",
                                "display",
                                "thumbnail",
                                "photo"
                            )
                        )

                        media_ext = any(
                            x in value.lower()
                            for x in (
                                ".mp4",
                                ".jpg",
                                ".jpeg",
                                ".png",
                                ".webp"
                            )
                        )

                        if media_key or media_ext:
                            found.append((key_lower, value))

                elif isinstance(value, (dict, list)):
                    walk(value)

        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)

    result = []
    seen = set()

    for key, url in found:
        if url not in seen:
            seen.add(url)
            result.append((key, url))

    return result


def fetch_instagram_media(url, folder):
    if not RAPIDAPI_KEY:
        return []

    api_url = f"https://{RAPIDAPI_HOST}/instagram/"

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }

    try:
        response = requests.get(
            api_url,
            headers=headers,
            params={"url": url},
            timeout=30
        )

        log.info(
            "Instagram RapidAPI status=%s length=%s",
            response.status_code,
            len(response.text)
        )

        if response.status_code != 200:
            return []

        data = response.json()
        candidates = extract_instagram_urls(data)

        log.info(
            "Instagram media candidates=%s",
            len(candidates)
        )

        files = []

        for index, (key, media_url) in enumerate(candidates):
            try:
                r = requests.get(
                    media_url,
                    timeout=60,
                    stream=True
                )

                if r.status_code != 200:
                    continue

                ext = get_content_extension(
                    r,
                    ".mp4" if "video" in key else ".jpg"
                )

                filename = os.path.join(
                    folder,
                    f"instagram_{index}{ext}"
                )

                with open(filename, "wb") as f:
                    for chunk in r.iter_content(1024 * 64):
                        if chunk:
                            f.write(chunk)

                if file_size(filename) > 0:
                    files.append(filename)

            except Exception as e:
                log.warning(
                    "Instagram media download error: %s",
                    e
                )

        return files

    except Exception as e:
        log.warning(
            "Instagram RapidAPI error: %s",
            e
        )
        return []


# =========================
# INSTAGRAM OG IMAGE
# =========================

def fetch_instagram_og_image(url, folder):
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120 Safari/537.36"
            )
        }

        r = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        if r.status_code != 200:
            return None

        match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+'
            r'content=["\']([^"\']+)["\']',
            r.text,
            re.I
        )

        if not match:
            match = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+'
                r'property=["\']og:image["\']',
                r.text,
                re.I
            )

        if not match:
            return None

        image_url = match.group(1)

        img = requests.get(
            image_url,
            headers=headers,
            timeout=30
        )

        if img.status_code != 200:
            return None

        ext = get_content_extension(img, ".jpg")

        filename = os.path.join(
            folder,
            f"instagram_image{ext}"
        )

        with open(filename, "wb") as f:
            f.write(img.content)

        return filename if file_size(filename) else None

    except Exception as e:
        log.warning(
            "Instagram OG image error: %s",
            e
        )
        return None


# =========================
# TIKTOK PHOTO
# =========================

def fetch_tiktok_photo(url, folder):
    try:
        api_url = "https://www.tikwm.com/api/"

        response = requests.get(
            api_url,
            params={
                "url": url,
                "hd": "1"
            },
            timeout=30
        )

        if response.status_code != 200:
            return []

        data = response.json()

        images = (
            data.get("data", {}).get("images", [])
            if isinstance(data, dict)
            else []
        )

        if not images:
            return []

        files = []

        for index, image_url in enumerate(images):
            try:
                r = requests.get(
                    image_url,
                    timeout=60
                )

                if r.status_code != 200:
                    continue

                filename = os.path.join(
                    folder,
                    f"tiktok_photo_{index}.jpg"
                )

                with open(filename, "wb") as f:
                    f.write(r.content)

                if file_size(filename):
                    files.append(filename)

            except Exception as e:
                log.warning(
                    "TikTok photo error: %s",
                    e
                )

        return files

    except Exception as e:
        log.warning(
            "TikTok Photo API error: %s",
            e
        )
        return []


# =========================
# YT-DLP
# =========================

def download_with_ytdlp(url, folder):
    output_template = os.path.join(
        folder,
        "%(title).80s_%(id)s.%(ext)s"
    )

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    opts = {
        "outtmpl": output_template,

        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "best[ext=mp4]/"
            "best"
        ),

        "merge_output_format": "mp4",

        "noplaylist": True,

        "quiet": True,
        "no_warnings": True,

        "restrictfilenames": True,
        "windowsfilenames": True,

        "ffmpeg_location": ffmpeg_path,

        "socket_timeout": 30,
        "retries": 2,

        "max_filesize": MAX_FILE_SIZE,

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120 Safari/537.36"
            )
        }
    }

    # Optional cookies file
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                url,
                download=True
            )

            # =========================
    
# =========================

    except Exception as e:
        log.warning(
            "yt-dlp error: %s",
            e
        )

    # Fallback: scan only this job folder
    files = []

    for name in os.listdir(folder):
        path = os.path.join(folder, name)

        if os.path.isfile(path):
            if is_video_file(path) or is_image_file(path):
                files.append(path)

    return files
    # =========================
# SEND MEDIA - STABLE VERSION
# =========================
async def send_files(message, files):
    valid = []

    for path in files:
        if not os.path.exists(path):
            continue

        size = file_size(path)

        if size <= 0:
            continue

        if size > MAX_FILE_SIZE:
            log.warning("File too large: %s (%s bytes)", path, size)
            continue

        valid.append(path)

    if not valid:
        return False

    sent_any = False

    # Send files one-by-one instead of send_media_group.
    # This avoids Telegram FILE_PART_0_MISSING errors
    # that can happen while uploading multiple files together.
    for path in valid:
        try:
            log.info(
                "Sending file: %s (%s bytes)",
                os.path.basename(path),
                file_size(path)
            )

            if is_video_file(path):
                await message.reply_video(
                    path,
                    supports_streaming=True
                )

            elif is_image_file(path):
                # Telegram photo upload
                if file_size(path) <= 10 * 1024 * 1024:
                    await message.reply_photo(path)
                else:
                    await message.reply_document(path)

            else:
                await message.reply_document(path)

            sent_any = True

            # Small delay between uploads
            await asyncio.sleep(0.5)

        except Exception as e:
            log.exception(
                "Send file error for %s: %s",
                path,
                e
            )

            # Retry once
            try:
                await asyncio.sleep(1)

                if is_video_file(path):
                    await message.reply_video(
                        path,
                        supports_streaming=True
                    )

                elif is_image_file(path):
                    if file_size(path) <= 10 * 1024 * 1024:
                        await message.reply_photo(path)
                    else:
                        await message.reply_document(path)

                else:
                    await message.reply_document(path)

                sent_any = True

            except Exception as retry_error:
                log.exception(
                    "Retry failed for %s: %s",
                    path,
                    retry_error
                )

    return sent_any


# =========================
# COMMANDS
# =========================

@app.on_message(filters.command("start"))
async def start_handler(client, message):
    await message.reply_text(
        "👋 أهلاً بك\n\n"
        "أرسل رابط Instagram أو TikTok أو Facebook أو X "
        "وسأحاول تحميل الوسائط منه."
    )


@app.on_message(filters.command("help"))
async def help_handler(client, message):
    await message.reply_text(
        "📥 أرسل رابط الوسائط فقط.\n\n"
        "المواقع المدعومة:\n"
        "• Instagram\n"
        "• TikTok\n"
        "• Facebook\n"
        "• X / Twitter"
    )


# =========================
# DOWNLOAD HANDLER
# =========================

@app.on_message(filters.text & ~filters.command(["start", "help"]))
async def download_handler(client, message: Message):

    url = message.text.strip()

    if not is_url(url):
        await message.reply_text(
            "❌ أرسل رابط صحيح يبدأ بـ http أو https."
        )
        return

    status = await message.reply_text(
        "⏳ جاري التحميل..."
    )

    folder = create_job_folder()

    try:

        # =====================
        # INSTAGRAM
        # =====================

        if is_instagram_url(url):

            files = await asyncio.get_running_loop().run_in_executor(
                None,
                fetch_instagram_media,
                url,
                folder
            )

            # Fallback للصورة فقط
            if not files and "/p/" in url:
                image = await asyncio.get_running_loop().run_in_executor(
                    None,
                    fetch_instagram_og_image,
                    url,
                    folder
                )

                if image:
                    files = [image]

            # Final fallback
            if not files:
                files = await asyncio.get_running_loop().run_in_executor(
                    None,
                    download_with_ytdlp,
                    url,
                    folder
                )

        # =====================
        # TIKTOK
        # =====================

            elif is_tiktok_url(url):
            # Photo TikTok
            if is_tiktok_photo(url):
                files = await fetch_tiktok_photo(url, folder)
                if files:
                    return files

                # fallback
                if not files:
                    files = await asyncio.get_running_loop().run_in_executor(
                        None,
                        download_with_ytdlp,
                        url,
                        folder
                    )

            else:
                # TikTok Video مباشرة
                files = await asyncio.get_running_loop().run_in_executor(
                    None,
                    download_with_ytdlp,
                    url,
                    folder
                )

        # =====================
        # OTHER SITES
        # =====================

        else:

            files = await asyncio.get_running_loop().run_in_executor(
                None,
                download_with_ytdlp,
                url,
                folder
            )

        # =====================
        # SEND
        # =====================

        if not files:
            await status.edit_text(
                "❌ ما قدرت أحصل على الوسائط من هذا الرابط.\n\n"
                "إذا كان المحتوى خاص أو غير متاح للعامة، "
                "البوت ما يقدر يحمله."
            )
            return

        sent = await send_files(
            message,
            files
        )

        if sent:
            try:
                await status.delete()
            except Exception:
                pass
        else:
            await status.edit_text(
                "❌ حصلت مشكلة أثناء إرسال الملف."
            )

    except Exception as e:

        log.exception(
            "Download handler error"
        )

        try:
            await status.edit_text(
                "❌ صار خطأ أثناء التحميل."
            )
        except Exception:
            pass

    finally:
        cleanup_folder(folder)


# =========================
# RUN
# =========================

if __name__ == "__main__":
    log.info("BOT STARTING...")
    app.run()

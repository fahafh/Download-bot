import os
import re
import glob
import shutil
import asyncio
import json
from urllib.parse import urlparse

import requests
import yt_dlp
import imageio_ffmpeg

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

DOWNLOAD_DIR = "downloads"
MAX_SIZE = 2 * 1024 * 1024 * 1024
MAX_FILES = 10

UA = (
    "Mozilla/5.0 (Linux; Android 13) "
    "AppleWebKit/537.36 "
    "Chrome/140.0.0.0 Mobile Safari/537.36"
)

VIDEO_EXT = {
    ".mp4", ".m4v", ".mov", ".webm", ".mkv"
}

IMAGE_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"
}


# =========================================================
# BOT
# =========================================================

app = Client(
    "telegram_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


def log(x):
    print(f"[BOT] {x}", flush=True)


# =========================================================
# URL
# =========================================================

def clean_url(url):
    return url.strip().rstrip(".,!?)]}>\"'")


def platform(url):
    host = urlparse(url).netloc.lower()

    if "instagram.com" in host:
        return "instagram"

    if "facebook.com" in host or "fb.watch" in host:
        return "facebook"

    if "tiktok.com" in host:
        return "tiktok"

    if "twitter.com" in host or "x.com" in host:
        return "twitter"

    return "unknown"


def instagram_type(url):
    path = urlparse(url).path.lower()

    if "/reel/" in path or "/reels/" in path:
        return "reel"

    if "/p/" in path:
        return "post"

    return "unknown"


# =========================================================
# FILE HELPERS
# =========================================================

def remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except:
        pass


def size_ok(path):
    try:
        return os.path.getsize(path) <= MAX_SIZE
    except:
        return False


def valid_image(path):
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except:
        return False


def convert_to_jpg(path, folder):
    try:
        with Image.open(path) as im:

            im = ImageOps.exif_transpose(im)

            if getattr(im, "is_animated", False):
                im.seek(0)

            im = im.convert("RGBA")

            bg = Image.new(
                "RGB",
                im.size,
                "white"
            )

            bg.paste(
                im,
                mask=im.getchannel("A")
            )

            name = os.path.splitext(
                os.path.basename(path)
            )[0]

            out = os.path.join(
                folder,
                name + ".jpg"
            )

            n = 1

            while os.path.exists(out):
                out = os.path.join(
                    folder,
                    f"{name}_{n}.jpg"
                )
                n += 1

            bg.save(
                out,
                "JPEG",
                quality=95
            )

            return out

    except Exception as e:
        log(f"JPG ERROR: {e}")
        return None


# =========================================================
# DOWNLOAD DIRECT URL
# =========================================================

def download_direct(url, folder, index=0, force_video=False):
    try:

        r = requests.get(
            url,
            headers={
                "User-Agent": UA,
                "Referer": "https://www.instagram.com/"
            },
            stream=True,
            timeout=40
        )

        if r.status_code != 200:
            return None

        content_type = r.headers.get(
            "Content-Type",
            ""
        ).lower()

        if "text/html" in content_type:
            return None

        if force_video and "video/" not in content_type:
            return None

        if "video/" in content_type:
            ext = ".mp4"

        elif "image/" in content_type:
            ext = ".jpg"

        else:
            path_ext = os.path.splitext(
                urlparse(url).path
            )[1].lower()

            if path_ext in VIDEO_EXT:
                ext = ".mp4"
            elif path_ext in IMAGE_EXT:
                ext = ".jpg"
            else:
                return None

        path = os.path.join(
            folder,
            f"direct_{index}{ext}"
        )

        total = 0

        with open(path, "wb") as f:

            for chunk in r.iter_content(
                256 * 1024
            ):

                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_SIZE:
                    remove(path)
                    return None

                f.write(chunk)

        if not size_ok(path):
            remove(path)
            return None

        if ext == ".jpg" and not valid_image(path):
            remove(path)
            return None

        if os.path.getsize(path) < 1000:
            remove(path)
            return None

        return path

    except Exception as e:
        log(f"DIRECT ERROR: {e}")
        return None


# =========================================================
# YT-DLP
# =========================================================

def ytdlp_download(url, folder, force_video=False):
    before = set(
        glob.glob(
            os.path.join(folder, "*")
        )
    )

    opts = {
        "outtmpl": os.path.join(
            folder,
            "%(id)s.%(ext)s"
        ),

        "noplaylist": True,

        "retries": 3,
        "fragment_retries": 3,

        "socket_timeout": 30,

        "quiet": False,

        "ffmpeg_location":
            imageio_ffmpeg.get_ffmpeg_exe(),

        "http_headers": {
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9"
        }
    }

    if force_video:
        opts["format"] = (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "best[ext=mp4]/best"
        )
        opts["merge_output_format"] = "mp4"
    else:
        opts["format"] = (
            "bestvideo*+bestaudio/"
            "best"
        )
        opts["merge_output_format"] = "mp4"

    cookies = os.path.join(
        os.getcwd(),
        "cookies.txt"
    )

    if os.path.isfile(cookies):
        opts["cookiefile"] = cookies
        log("yt-dlp: cookies.txt enabled")

    try:

        log(f"YT-DLP: {url}")

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    except Exception as e:
        log(f"YT-DLP ERROR: {e}")

    after = set(
        glob.glob(
            os.path.join(folder, "*")
        )
    )

    result = []

    for path in after - before:

        if not os.path.isfile(path):
            continue

        if not size_ok(path):
            remove(path)
            continue

        ext = os.path.splitext(
            path
        )[1].lower()

        if force_video:

            if ext in VIDEO_EXT:
                result.append(path)
            else:
                remove(path)

        else:

            if ext in VIDEO_EXT:
                result.append(path)

            elif ext in IMAGE_EXT:

                if valid_image(path):
                    result.append(path)
                else:
                    remove(path)

    return result[:MAX_FILES]


# =========================================================
# RAPIDAPI INSTAGRAM
# =========================================================

def rapidapi_instagram(url, folder, reel=False):

    if not RAPIDAPI_KEY:
        return []

    endpoint = (
        f"https://{RAPIDAPI_HOST}/instagram/"
    )

    try:

        r = requests.get(
            endpoint,
            headers={
                "x-rapidapi-key": RAPIDAPI_KEY,
                "x-rapidapi-host": RAPIDAPI_HOST,
                "User-Agent": UA
            },
            params={"url": url},
            timeout=30
        )

        log(
            f"RapidAPI: {r.status_code} "
            f"length={len(r.text)}"
        )

        if r.status_code != 200:
            return []

        data = r.json()

    except Exception as e:
        log(f"RapidAPI ERROR: {e}")
        return []

    # -----------------------------------------------------
    # Find ALL URLs in response.
    # Do not depend on specific JSON key names.
    # -----------------------------------------------------

    found = []

    def scan(obj):

        if isinstance(obj, dict):

            for value in obj.values():
                scan(value)

        elif isinstance(obj, list):

            for value in obj:
                scan(value)

        elif isinstance(obj, str):

            if not obj.startswith(
                ("http://", "https://")
            ):
                return

            u = obj.replace("\\/", "/")

            low = u.lower()

            if any(
                x in low
                for x in (
                    ".mp4",
                    ".m3u8",
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp"
                )
            ):
                found.append(u)

    scan(data)

    # remove duplicates
    unique = []

    for u in found:
        if u not in unique:
            unique.append(u)

    log(
        f"RapidAPI URL candidates: "
        f"{len(unique)}"
    )

    result = []

    for i, u in enumerate(unique):

        if len(result) >= MAX_FILES:
            break

        # Reel: video only
        if reel:

            low = u.lower()

            if not any(
                x in low
                for x in (
                    ".mp4",
                    ".m3u8"
                )
            ):
                continue

            path = download_direct(
                u,
                folder,
                i,
                force_video=True
            )

        else:

            path = download_direct(
                u,
                folder,
                i,
                force_video=False
            )

        if path:
            result.append(path)

    log(
        f"RapidAPI downloaded: "
        f"{len(result)}"
    )

    return result


# =========================================================
# TIKTOK PHOTO
# =========================================================

def tiktok_photo(url, folder):
    """
    TikTok photo posts are not handled reliably by
    yt-dlp on some URLs.

    Try the public page and extract image URLs.
    """

    try:

        r = requests.get(
            url,
            headers={
                "User-Agent": UA,
                "Accept-Language": "en-US,en;q=0.9"
            },
            timeout=30,
            allow_redirects=True
        )

        log(
            f"TikTok page: "
            f"{r.status_code} "
            f"{r.url}"
        )

        if r.status_code != 200:
            return []

        text = r.text

        # URLs inside JSON / HTML
        urls = re.findall(
            r'https?://[^"\'<>\\ ]+',
            text
        )

        result = []
        seen = set()

        for u in urls:

            u = (
                u.replace("\\u002F", "/")
                 .replace("\\/", "/")
                .replace("&amp;", "&")
            )

            low = u.lower()

            if not any(
                x in low
                for x in (
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp"
                )
            ):
                continue

            # TikTok CDN only
            if (
                "tiktokcdn" not in low
                and "tiktok.com" not in low
            ):
                continue

            if u in seen:
                continue

            seen.add(u)

            path = download_direct(
                u,
                folder,
                len(result)
            )

            if path:
                result.append(path)

            if len(result) >= MAX_FILES:
                break

        log(
            f"TikTok images: {len(result)}"
        )

        return result

    except Exception as e:
        log(f"TikTok photo ERROR: {e}")
        return []


# =========================================================
# INSTAGRAM
# =========================================================

def download_instagram(url, folder):

    kind = instagram_type(url)

    log(f"Instagram type: {kind}")

    # -----------------------------------------------------
    # REEL
    # -----------------------------------------------------

    if kind == "reel":

        files = rapidapi_instagram(
            url,
            folder,
            reel=True
        )

        if files:
            return files

        return ytdlp_download(
            url,
            folder,
            force_video=True
        )

    # -----------------------------------------------------
    # POST
    # -----------------------------------------------------

    if kind == "post":

        files = rapidapi_instagram(
            url,
            folder,
            reel=False
        )

        if files:
            return files

        return ytdlp_download(
            url,
            folder,
            force_video=False
        )

    return ytdlp_download(
        url,
        folder,
        force_video=False
    )


# =========================================================
# TIKTOK
# =========================================================

def download_tiktok(url, folder):

    # First inspect the final TikTok URL
    try:

        r = requests.head(
            url,
            headers={"User-Agent": UA},
            allow_redirects=True,
            timeout=15
        )

        final_url = r.url

    except:
        final_url = url

    log(f"TikTok final URL: {final_url}")

    if "/photo/" in final_url.lower():

        files = tiktok_photo(
            final_url,
            folder
        )

        if files:
            return files

        return []

    return ytdlp_download(
        url,
        folder,
        force_video=True
    )


# =========================================================
# OTHER PLATFORMS
# =========================================================

def download_other(url, folder):

    return ytdlp_download(
        url,
        folder,
        force_video=False
    )


# =========================================================
# DOWNLOAD ROUTER
# =========================================================

def download(url, folder):

    p = platform(url)

    if p == "instagram":
        return download_instagram(
            url,
            folder
        )

    if p == "tiktok":
        return download_tiktok(
            url,
            folder
        )

    return download_other(
        url,
        folder
    )


# =========================================================
# TELEGRAM SEND
# =========================================================

async def send_file(message, path, folder):

    ext = os.path.splitext(
        path
    )[1].lower()

    if ext in VIDEO_EXT:

        try:

            await message.reply_video(
                video=path,
                supports_streaming=True
            )

            return True

        except Exception as e:

            log(f"VIDEO SEND ERROR: {e}")

            try:

                await message.reply_document(
                    document=path
                )

                return True

            except:
                return False

    if ext in IMAGE_EXT:

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

            remove(jpg)

            return True

        except Exception as e:

            log(f"PHOTO SEND ERROR: {e}")

            try:

                await message.reply_document(
                    document=jpg
                )

                remove(jpg)

                return True

            except:
                remove(jpg)
                return False

    return False


# =========================================================
# TELEGRAM HANDLER
# =========================================================

@app.on_message(
    filters.private & filters.text
)
async def handler(client, message):

    match = re.search(
        r"https?://\S+",
        message.text or ""
    )

    if not match:

        await message.reply_text(
            "🔗 أرسل رابط Instagram أو TikTok أو Facebook أو X."
        )

        return

    url = clean_url(
        match.group(0)
    )

    p = platform(url)

    folder = os.path.join(
        DOWNLOAD_DIR,
        f"{message.chat.id}_{message.id}"
    )

    os.makedirs(
        folder,
        exist_ok=True
    )

    status = await message.reply_text(
        f"⏳ جاري تحميل {p.upper()}..."
    )

    log("==============================")
    log(f"URL: {url}")
    log(f"PLATFORM: {p}")
    log("==============================")

    try:

        files = await asyncio.to_thread(
            download,
            url,
            folder
        )

        if not files:

            await status.edit_text(
                "❌ ماكدرت أنزل المحتوى.\n\n"
                "إذا كان الرابط خاص أو المحتوى غير متاح للعامة، "
                "ما أگدر أوصل له."
            )

            return

        sent = 0

        for path in files[:MAX_FILES]:

            if await send_file(
                message,
                path,
                folder
            ):
                sent += 1

            await asyncio.sleep(0.4)

        await status.edit_text(
            f"✅ تم إرسال {sent} ملف."
        )

    except Exception as e:

        log(f"JOB ERROR: {e}")

        await status.edit_text(
            "❌ صار خطأ أثناء التحميل."
        )

    finally:

        shutil.rmtree(
            folder,
            ignore_errors=True
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    os.makedirs(
        DOWNLOAD_DIR,
        exist_ok=True
    )

    log("BOT STARTING...")
    log(
        f"RapidAPI: {bool(RAPIDAPI_KEY)}"
    )

    app.run()

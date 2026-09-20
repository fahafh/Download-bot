import os
import re
import glob
import shutil
import asyncio
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

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13) "
    "AppleWebKit/537.36 "
    "Chrome/140.0.0.0 Mobile Safari/537.36"
)


# =========================================================
# BOT
# =========================================================

app = Client(
    "telegram_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


def log(text):
    print(f"[BOT] {text}", flush=True)


# =========================================================
# URL / PLATFORM
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

def size_ok(path):
    try:
        return os.path.getsize(path) <= MAX_SIZE
    except:
        return False


def is_image(path):
    try:
        with Image.open(path) as im:
            return im.format is not None
    except:
        return False


def remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except:
        pass


def convert_image_to_jpg(path, folder):
    try:
        with Image.open(path) as im:

            im = ImageOps.exif_transpose(im)

            # لا نريد إرسال GIF/WEBP متحرك كـ GIF
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

            counter = 1

            while os.path.exists(out):
                out = os.path.join(
                    folder,
                    f"{name}_{counter}.jpg"
                )
                counter += 1

            bg.save(
                out,
                "JPEG",
                quality=95,
                optimize=True
            )

            return out

    except Exception as e:
        log(f"Image conversion error: {e}")
        return None


# =========================================================
# YT-DLP
# =========================================================

def ytdlp_download(url, folder, force_video=False):
    """
    Download using yt-dlp.

    force_video=True:
    Instagram Reel must be video.
    """

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

        "quiet": False,

        "no_warnings": False,

        "retries": 2,

        "fragment_retries": 2,

        "socket_timeout": 30,

        "ffmpeg_location":
            imageio_ffmpeg.get_ffmpeg_exe(),

        "http_headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9"
        }
    }

    # Reel = video only
    if force_video:
        opts["format"] = (
            "bestvideo[ext=mp4]+"
            "bestaudio[ext=m4a]/"
            "best[ext=mp4]/"
            "best"
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

    if os.path.exists(cookies):
        opts["cookiefile"] = cookies
        log("yt-dlp: cookies.txt enabled")

    try:

        log(f"YT-DLP: {url}")

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    except Exception as e:

        log(f"YT-DLP ERROR: {e}")
        return []

    after = set(
        glob.glob(
            os.path.join(folder, "*")
        )
    )

    files = []

    for path in after - before:

        if not os.path.isfile(path):
            continue

        if not size_ok(path):
            remove(path)
            continue

        ext = os.path.splitext(
            path
        )[1].lower()

        # =================================================
        # REEL: ACCEPT VIDEO ONLY
        # =================================================

        if force_video:

            if ext not in (
                ".mp4",
                ".m4v",
                ".mov",
                ".webm",
                ".mkv"
            ):
                log(
                    f"Rejected non-video Reel file: {path}"
                )
                remove(path)
                continue

            files.append(path)
            continue

        # =================================================
        # NORMAL
        # =================================================

        if ext in (
            ".mp4",
            ".m4v",
            ".mov",
            ".webm",
            ".mkv"
        ):

            files.append(path)

        elif ext in (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp"
        ):

            if is_image(path):
                files.append(path)
            else:
                remove(path)

    return files[:MAX_FILES]


# =========================================================
# RAPIDAPI - INSTAGRAM
# =========================================================

def rapidapi_instagram(url, folder, reel=False):

    if not RAPIDAPI_KEY:
        return []

    endpoint = (
        f"https://{RAPIDAPI_HOST}/instagram/"
    )

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "User-Agent": USER_AGENT
    }

    try:

        r = requests.get(
            endpoint,
            headers=headers,
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

        log(f"RapidAPI error: {e}")
        return []

    # Collect only obvious media URLs.
    urls = []

    def scan(obj):

        if isinstance(obj, dict):

            for key, value in obj.items():

                key = str(key).lower()

                if isinstance(value, str):

                    if not value.startswith(
                        ("http://", "https://")
                    ):
                        continue

                    low = value.lower()

                    video_key = any(
                        x in key
                        for x in (
                            "video",
                            "play",
                            "download"
                        )
                    )

                    image_key = any(
                        x in key
                        for x in (
                            "image",
                            "photo",
                            "display"
                        )
                    )

                    if reel and video_key:
                        urls.append(
                            ("video", value)
                        )

                    elif not reel and (
                        video_key or image_key
                    ):
                        urls.append(
                            (
                                "video"
                                if video_key
                                else "image",
                                value
                            )
                        )

                elif isinstance(
                    value,
                    (dict, list)
                ):
                    scan(value)

        elif isinstance(obj, list):

            for item in obj:
                scan(item)

    scan(data)

    # Deduplicate
    unique = []
    seen = set()

    for kind, url in urls:

        if url in seen:
            continue

        seen.add(url)

        unique.append(
            (kind, url)
        )

    log(
        f"RapidAPI media candidates: "
        f"{len(unique)}"
    )

    downloaded = []

    for index, (kind, url) in enumerate(
        unique[:MAX_FILES]
    ):

        try:

            r = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer":
                        "https://www.instagram.com/"
                },
                stream=True,
                timeout=30
            )

            if r.status_code != 200:
                continue

            content_type = (
                r.headers.get(
                    "Content-Type",
                    ""
                ).lower()
            )

            if "text/html" in content_type:
                continue

            # Reel must be video
            if reel and not (
                "video/" in content_type
                or kind == "video"
            ):
                continue

            ext = ".mp4" if kind == "video" else ".jpg"

            path = os.path.join(
                folder,
                f"rapid_{index}{ext}"
            )

            with open(path, "wb") as f:

                total = 0

                for chunk in r.iter_content(
                    256 * 1024
                ):

                    if not chunk:
                        continue

                    total += len(chunk)

                    if total > MAX_SIZE:
                        break

                    f.write(chunk)

            if not size_ok(path):
                remove(path)
                continue

            # Validate downloaded media
            if kind == "video":

                if os.path.getsize(path) > 1000:
                    downloaded.append(path)

            else:

                if is_image(path):
                    downloaded.append(path)
                else:
                    remove(path)

        except Exception as e:
            log(f"Rapid media error: {e}")

    return downloaded


# =========================================================
# INSTAGRAM
# =========================================================

def download_instagram(url, folder):

    kind = instagram_type(url)

    log(
        f"Instagram type: {kind}"
    )

    # -----------------------------------------------------
    # REEL
    # -----------------------------------------------------

    if kind == "reel":

        # RapidAPI first
        files = rapidapi_instagram(
            url,
            folder,
            reel=True
        )

        if files:
            log("Instagram Reel: RapidAPI success")
            return files

        # yt-dlp video ONLY
        files = ytdlp_download(
            url,
            folder,
            force_video=True
        )

        return files

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

        # fallback
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
# FACEBOOK / TIKTOK / X
# =========================================================

def download_other(url, folder):

    return ytdlp_download(
        url,
        folder,
        force_video=False
    )


# =========================================================
# SEND TO TELEGRAM
# =========================================================

async def send_file(message, path, folder):

    ext = os.path.splitext(
        path
    )[1].lower()

    # -----------------------------------------------------
    # VIDEO
    # -----------------------------------------------------

    if ext in (
        ".mp4",
        ".m4v",
        ".mov",
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

            log(f"Video send error: {e}")

            try:

                await message.reply_document(
                    document=path
                )

                return True

            except:
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

        jpg = convert_image_to_jpg(
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

            log(f"Photo send error: {e}")

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
# MAIN DOWNLOAD
# =========================================================

def download(url, folder):

    p = platform(url)

    if p == "instagram":
        return download_instagram(
            url,
            folder
        )

    return download_other(
        url,
        folder
    )


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
            "🔗 أرسل رابط Instagram أو Facebook أو TikTok."
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
                "❌ ماكدرت أنزل المحتوى.\n"
                "تأكد أن الرابط عام ومتاح بدون تسجيل دخول."
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

            await asyncio.sleep(0.5)

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

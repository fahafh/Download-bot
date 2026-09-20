import os
import uuid
import asyncio
import logging
import mimetypes

import requests
import imageio_ffmpeg
import yt_dlp

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

CHANNEL_USERNAME = "ht4h4"

# RapidAPI
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = (
    "instagram-post-reels-stories-downloader-api.p.rapidapi.com"
)

# ============================================================
# DOWNLOAD SETTINGS
# ============================================================

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

COOKIES_FILE = "cookies.txt"
HAS_COOKIES = os.path.exists(COOKIES_FILE)


# ============================================================
# PYROGRAM
# ============================================================

app = Client(
    "downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# ============================================================
# HELPERS
# ============================================================

def is_instagram_url(url):
    url = url.lower()
    return (
        "instagram.com" in url
        or "instagr.am" in url
    )


def is_tiktok_url(url):
    return (
        "tiktok.com" in url.lower()
        or "vt.tiktok.com" in url.lower()
    )


def clean_url(url):
    return url.split("?")[0].strip()


def safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning(f"تعذر حذف الملف {path}: {e}")


def get_extension_from_response(response, default="jpg"):
    """
    يحاول تحديد امتداد الملف من Content-Type.
    """

    content_type = response.headers.get("Content-Type", "").lower()

    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"

    if "png" in content_type:
        return "png"

    if "webp" in content_type:
        return "webp"

    if "gif" in content_type:
        return "gif"

    if "mp4" in content_type:
        return "mp4"

    if "quicktime" in content_type:
        return "mov"

    return default


# ============================================================
# MEMBERSHIP
# ============================================================

async def check_membership(client, user_id):
    try:
        chat = f"@{CHANNEL_USERNAME}"

        member = await client.get_chat_member(
            chat,
            user_id
        )

        return member.status in [
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ]

    except UserNotParticipant:
        return False

    except Exception as e:
        logger.error(f"خطأ بفحص الاشتراك: {e}")
        return False


def force_sub_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 اشترك في القناة أولاً",
                url=f"https://t.me/{CHANNEL_USERNAME}"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ تحقق من الاشتراك",
                callback_data="check_sub"
            )
        ]
    ])


# ============================================================
# CALLBACK
# ============================================================

@app.on_callback_query(filters.regex("^check_sub$"))
async def check_callback(client, callback_query):

    if await check_membership(
        client,
        callback_query.from_user.id
    ):

        try:
            await callback_query.message.delete()
        except Exception:
            pass

        await callback_query.message.reply_text(
            "✅ تم التحقق من اشتراكك بنجاح!\n\n"
            "أرسل رابط الفيديو أو الصورة الآن 🔥"
        )

    else:
        await callback_query.answer(
            "⚠️ أنت غير مشترك في القناة بعد!",
            show_alert=True
        )


# ============================================================
# START
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(client, message):

    if not await check_membership(
        client,
        message.from_user.id
    ):
        await message.reply_text(
            "⚠️ عذراً، يجب عليك الاشتراك في قناة البوت أولاً!",
            reply_markup=force_sub_keyboard()
        )
        return

    await message.reply_text(
        "أهلاً بك 🔥\n\n"
        "أرسل لي أي رابط فيديو أو صورة أو ألبوم صور "
        "وسأحاول تحميله بأعلى جودة متوفرة."
    )


# ============================================================
# HELP
# ============================================================

@app.on_message(filters.command("help"))
async def help_cmd(client, message):

    await message.reply_text(
        "📖 طريقة الاستخدام:\n\n"
        "1️⃣ أرسل رابط المحتوى\n"
        "2️⃣ انتظر حتى يكتمل التحميل\n"
        "3️⃣ سأرسل لك الصور أو الفيديوهات\n\n"
        "🖼️ يدعم الصور والألبومات\n"
        "🎬 يدعم الفيديوهات\n"
        "⚠️ الحد الأقصى للملف: 2GB"
    )


# ============================================================
# YT-DLP VIDEO OPTIONS
# ============================================================

def build_video_opts():

    opts = {
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),

        # فيديو بأعلى جودة متوفرة
        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best[ext=mp4]/"
            "best"
        ),

        "outtmpl": os.path.join(
            DOWNLOAD_DIR,
            "%(title).100s_%(id)s.%(ext)s"
        ),

        "merge_output_format": "mp4",

        "concurrent_fragment_downloads": 5,

        "quiet": True,
        "no_warnings": True,

        "restrictfilenames": True,

        "socket_timeout": 30,

        "retries": 3,

        "fragment_retries": 3,

        "noplaylist": False,

        "extractor_args": {
            "youtube": {
                "player_client": [
                    "android",
                    "ios"
                ]
            }
        }
    }

    if HAS_COOKIES:
        opts["cookiefile"] = COOKIES_FILE

    return opts


# ============================================================
# RAPIDAPI - FIND MEDIA
# ============================================================

def _find_media_list(node):

    if isinstance(node, list):

        if node and all(
            isinstance(i, dict) and i.get("url")
            for i in node
        ):
            return node

        for item in node:
            found = _find_media_list(item)

            if found:
                return found

    elif isinstance(node, dict):

        for value in node.values():
            found = _find_media_list(value)

            if found:
                return found

    return []


def _collect_media_dicts(node, out):

    if isinstance(node, dict):

        url = node.get("url")
        media_type = node.get("type")

        if (
            url
            and isinstance(media_type, str)
            and media_type.lower().startswith(
                ("image", "video")
            )
        ):
            out.append(node)

        for value in node.values():
            _collect_media_dicts(value, out)

    elif isinstance(node, list):

        for item in node:
            _collect_media_dicts(item, out)


# ============================================================
# INSTAGRAM DOWNLOADER
# ============================================================

def fetch_instagram_media(url):

    if not RAPIDAPI_KEY:
        logger.error(
            "RAPIDAPI_KEY غير موجود في Railway Variables"
        )
        return []

    clean = clean_url(url)

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "User-Agent": "Mozilla/5.0"
    }

    api_url = (
        f"https://{RAPIDAPI_HOST}/instagram/"
    )

    try:

        logger.info(
            f"Instagram API request: {clean}"
        )

        response = requests.get(
            api_url,
            params={"url": clean},
            headers=headers,
            timeout=60
        )

        logger.info(
            f"Instagram API status: "
            f"{response.status_code}"
        )

    except Exception as e:

        logger.error(
            f"Instagram API connection error: {e}"
        )

        return []

    if response.status_code != 200:

        logger.error(
            "Instagram API failed: "
            f"{response.text[:500]}"
        )

        return []

    try:

        data = response.json()

    except Exception as e:

        logger.error(
            f"Instagram JSON error: {e}"
        )

        logger.error(
            response.text[:500]
        )

        return []

    # -----------------------------------------
    # البحث عن media
    # -----------------------------------------

    items = _find_media_list(data)

    if not items:

        items = []

        _collect_media_dicts(
            data,
            items
        )

    logger.info(
        f"Instagram media count: {len(items)}"
    )

    if not items:

        logger.info(
            f"Instagram API response: "
            f"{response.text[:1000]}"
        )

        return []

    results = []

    # لمنع تكرار نفس الرابط
    seen_urls = set()

    for index, item in enumerate(items):

        media_url = item.get("url")

        if not media_url:
            continue

        if media_url in seen_urls:
            continue

        seen_urls.add(media_url)

        media_type = str(
            item.get("type", "image")
        ).lower()

        is_video = media_type.startswith("video")

        try:

            logger.info(
                f"Downloading Instagram media "
                f"{index + 1}/{len(items)}"
            )

            r = requests.get(
                media_url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                },
                timeout=120 if is_video else 90,
                stream=True
            )

            r.raise_for_status()

            content_type = (
                r.headers
                .get("Content-Type", "")
                .lower()
            )

            # -------------------------------------
            # إذا API ما حدد النوع، نتحقق من MIME
            # -------------------------------------

            if (
                not is_video
                and "video" in content_type
            ):
                is_video = True

            if (
                is_video
                and "image" in content_type
            ):
                is_video = False

            default_ext = (
                "mp4" if is_video else "jpg"
            )

            ext = get_extension_from_response(
                r,
                default=default_ext
            )

            if is_video:
                ext = "mp4"

            filename = (
                f"ig_{uuid.uuid4().hex}.{ext}"
            )

            path = os.path.join(
                DOWNLOAD_DIR,
                filename
            )

            # -------------------------------------
            # تنزيل على أجزاء بدل r.content
            # -------------------------------------

            total_size = 0

            with open(path, "wb") as f:

                for chunk in r.iter_content(
                    chunk_size=1024 * 1024
                ):

                    if not chunk:
                        continue

                    total_size += len(chunk)

                    if total_size > MAX_FILE_SIZE:

                        logger.error(
                            "Instagram file > 2GB"
                        )

                        f.close()
                        safe_remove(path)

                        continue

                    f.write(chunk)

            if os.path.exists(path):

                results.append(
                    (path, is_video)
                )

                logger.info(
                    f"Instagram saved: {path}"
                )

        except Exception as e:

            logger.error(
                f"Instagram media download failed: {e}"
            )

    logger.info(
        f"Instagram downloaded: {len(results)}"
    )

    return results


# ============================================================
# TIKTOK PHOTO DOWNLOADER
# ============================================================

def fetch_tiktok_media(url):

    try:

        response = requests.get(
            "https://www.tikwm.com/api/",
            params={
                "url": url
            },
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=45
        )

        data = response.json()

    except Exception as e:

        logger.error(
            f"TikTok API error: {e}"
        )

        return []

    logger.info(
        f"TikTok API status={response.status_code} "
        f"code={data.get('code')} "
        f"msg={data.get('msg')}"
    )

    payload = data.get("data") or {}

    # صور Photo Mode
    image_urls = payload.get("images") or []

    logger.info(
        f"TikTok images: {len(image_urls)}"
    )

    results = []

    for image_url in image_urls:

        if not image_url:
            continue

        try:

            r = requests.get(
                image_url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                },
                timeout=90
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
                f"tt_{uuid.uuid4().hex}.{ext}"
            )

            with open(path, "wb") as f:
                f.write(r.content)

            if os.path.exists(path):
                results.append(
                    (path, False)
                )

        except Exception as e:

            logger.error(
                f"TikTok image download failed: {e}"
            )

    return results


# ============================================================
# YT-DLP DOWNLOAD
# ============================================================

def download_with_ytdlp(url):

    ydl_opts = build_video_opts()

    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        prepared = ydl.prepare_filename(
            info
        )

        # yt-dlp بعد الدمج قد يحول إلى mp4
        if os.path.exists(prepared):
            return prepared

        base, _ = os.path.splitext(
            prepared
        )

        possible_files = [
            base + ".mp4",
            base + ".mkv",
            base + ".webm",
            base + ".mov"
        ]

        for path in possible_files:

            if os.path.exists(path):
                return path

        # البحث عن أحدث ملف في downloads
        if os.path.exists(DOWNLOAD_DIR):

            candidates = []

            for name in os.listdir(
                DOWNLOAD_DIR
            ):

                path = os.path.join(
                    DOWNLOAD_DIR,
                    name
                )

                if os.path.isfile(path):

                    candidates.append(
                        (
                            os.path.getmtime(path),
                            path
                        )
                    )

            if candidates:

                candidates.sort(
                    reverse=True
                )

                return candidates[0][1]

        return None


# ============================================================
# SEND MEDIA TO TELEGRAM
# ============================================================

async def send_media_items(
    message,
    media_items
):

    if not media_items:
        return False

    # Telegram media group = max 10
    for start in range(
        0,
        len(media_items),
        10
    ):

        chunk = media_items[
            start:start + 10
        ]

        # -----------------------------------------
        # عنصر واحد
        # -----------------------------------------

        if len(chunk) == 1:

            path, is_video = chunk[0]

            if not os.path.exists(path):
                continue

            file_size = os.path.getsize(
                path
            )

            if file_size > MAX_FILE_SIZE:

                logger.error(
                    f"File too large: {path}"
                )

                continue

            if is_video:

                await message.reply_video(
                    video=path,
                    supports_streaming=True
                )

            else:

                await message.reply_photo(
                    photo=path
                )

        # -----------------------------------------
        # ألبوم
        # -----------------------------------------

        else:

            media = []

            for path, is_video in chunk:

                if not os.path.exists(path):
                    continue

                if is_video:

                    media.append(
                        InputMediaVideo(
                            media=path
                        )
                    )

                else:

                    media.append(
                        InputMediaPhoto(
                            media=path
                        )
                    )

            if media:

                await message.reply_media_group(
                    media=media
                )

    return True


# ============================================================
# MAIN DOWNLOAD HANDLER
# ============================================================

@app.on_message(
    filters.text
    & ~filters.command(
        ["start", "help"]
    )
)
async def download_media(
    client,
    message
):

    # -----------------------------------------
    # الاشتراك
    # -----------------------------------------

    if not await check_membership(
        client,
        message.from_user.id
    ):

        await message.reply_text(
            "⚠️ عذراً، يجب عليك الاشتراك "
            "في قناة البوت أولاً!",
            reply_markup=force_sub_keyboard()
        )

        return

    # -----------------------------------------
    # الرابط
    # -----------------------------------------

    url = message.text.strip()

    if not url.startswith(
        ("http://", "https://")
    ):

        await message.reply_text(
            "❌ أرسل رابط صحيح يبدأ بـ "
            "http أو https"
        )

        return

    status_message = await message.reply_text(
        "🔎 جاري التعرف على نوع المحتوى..."
    )

    file_path = None
    media_items = []

    loop = asyncio.get_event_loop()

    try:

        # ====================================================
        # 1. INSTAGRAM
        # ====================================================
        #
        # هنا التغيير المهم:
        # لا ننتظر yt-dlp يفشل.
        # إذا الرابط Instagram نجرب RapidAPI أولاً.
        # ====================================================

        if is_instagram_url(url):

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
                    "جاري رفعه إلى Telegram..."
                )

                await send_media_items(
                    message,
                    media_items
                )

                await status_message.delete()

                return

            logger.warning(
                "Instagram API returned no media. "
                "Trying yt-dlp..."
            )

        # ====================================================
        # 2. TIKTOK PHOTO MODE
        # ====================================================

        if is_tiktok_url(url):

            await status_message.edit_text(
                "🎵 جاري فحص محتوى TikTok..."
            )

            tiktok_media = await loop.run_in_executor(
                None,
                fetch_tiktok_media,
                url
            )

            if tiktok_media:

                await status_message.edit_text(
                    "🚀 تم العثور على الصور، "
                    "جاري رفعها..."
                )

                await send_media_items(
                    message,
                    tiktok_media
                )

                await status_message.delete()

                return

            logger.info(
                "TikTok has no photo media. "
                "Trying yt-dlp for video..."
            )

        # ====================================================
        # 3. YT-DLP
        # ====================================================

        await status_message.edit_text(
            "⬇️ جاري تحميل الفيديو بأعلى جودة..."
        )

        def run_ytdlp():

            return download_with_ytdlp(
                url
            )

        file_path = await loop.run_in_executor(
            None,
            run_ytdlp
        )

        # -----------------------------------------
        # فشل yt-dlp
        # -----------------------------------------

        if not file_path or not os.path.exists(
            file_path
        ):

            await status_message.edit_text(
                "❌ لم أتمكن من تحميل المحتوى."
            )

            return

        # -----------------------------------------
        # الحجم
        # -----------------------------------------

        file_size = os.path.getsize(
            file_path
        )

        if file_size > MAX_FILE_SIZE:

            safe_remove(file_path)
            file_path = None

            await status_message.edit_text(
                "❌ حجم الملف أكبر من 2GB."
            )

            return

        # -----------------------------------------
        # إرسال الفيديو
        # -----------------------------------------

        await status_message.edit_text(
            "🚀 تم التحميل، "
            "جاري الرفع إلى Telegram..."
        )

        await message.reply_video(
            video=file_path,
            supports_streaming=True
        )

        await status_message.delete()

    # ========================================================
    # ERROR
    # ========================================================

    except Exception as e:

        error_text = str(e)

        logger.exception(
            f"Download error: {error_text}"
        )

        # إذا Instagram وفشل API و yt-dlp
        if is_instagram_url(url):

            await status_message.edit_text(
                "❌ لم أتمكن من تحميل محتوى Instagram.\n\n"
                "تأكد أن الرابط عام وغير محذوف، "
                "وتأكد من RAPIDAPI_KEY في Railway."
            )

        elif is_tiktok_url(url):

            await status_message.edit_text(
                "❌ لم أتمكن من تحميل محتوى TikTok.\n\n"
                "تأكد أن الرابط عام وغير محذوف."
            )

        elif (
            "403" in error_text
            or "Forbidden" in error_text
        ):

            await status_message.edit_text(
                "❌ الموقع رفض عملية التحميل (403).\n"
                "حاول برابط آخر."
            )

        elif (
            "404" in error_text
            or "Not Found" in error_text
        ):

            await status_message.edit_text(
                "❌ الرابط غير موجود أو تم حذفه."
            )

        else:

            await status_message.edit_text(
                "❌ حدث خطأ أثناء التحميل:\n\n"
                f"{error_text[:500]}"
            )

    # ========================================================
    # CLEANUP
    # ========================================================

    finally:

        if file_path:
            safe_remove(file_path)

        for path, _ in media_items:
            safe_remove(path)


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":

    if not API_ID:
        logger.warning(
            "⚠️ API_ID غير موجود!"
        )

    if not API_HASH:
        logger.warning(
            "⚠️ API_HASH غير موجود!"
        )

    if not BOT_TOKEN:
        logger.warning(
            "⚠️ BOT_TOKEN غير موجود!"
        )

    if not HAS_COOKIES:

        logger.warning(
            "⚠️ cookies.txt غير موجود. "
            "بعض الروابط قد لا تعمل."
        )

    if not RAPIDAPI_KEY:

        logger.warning(
            "⚠️ RAPIDAPI_KEY غير موجود. "
            "تحميل صور Instagram لن يعمل."
        )

    logger.info(
        "🚀 البوت يعمل الآن..."
    )

    app.run()

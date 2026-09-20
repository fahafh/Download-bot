import os
import re
import uuid
import asyncio
import logging
import http.cookiejar
import requests
import imageio_ffmpeg
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
)
import yt_dlp

# ============================================
# إعدادات التسجيل (اللوق)
# ============================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# بيانات API - تُقرأ من متغيرات البيئة بـ Railway (Variables tab)
# ============================================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_USERNAME = "ht4h4"

# مفتاح RapidAPI لتحميل صور انستغرام (متغير بيئة على Railway)
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "instagram-post-reels-stories-downloader-api.p.rapidapi.com"

# ============================================
# مجلد التحميل - ينشئ تلقائياً لو مو موجود
# ============================================
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ============================================
# ملف الكوكيز (لانستغرام وغيره) - اختياري
# ============================================
COOKIES_FILE = "cookies.txt"
HAS_COOKIES = os.path.exists(COOKIES_FILE)

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

app = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


async def check_membership(client, user_id):
    try:
        chat = f"@{CHANNEL_USERNAME}"
        member = await client.get_chat_member(chat, user_id)
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
        [InlineKeyboardButton("📢 اشترك في القناة أولاً", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub")]
    ])


@app.on_callback_query(filters.regex("check_sub"))
async def check_callback(client, callback_query):
    if await check_membership(client, callback_query.from_user.id):
        await callback_query.message.delete()
        await callback_query.message.reply_text(
            "✅ تم التحقق من اشتراكك بنجاح! أرسل رابط الفيديو الآن وسأنزله لك 🔥"
        )
    else:
        await callback_query.answer(
            "⚠️ أنت غير مشترك في القناة بعد! اشترك ثم اضغط تحقق.", show_alert=True
        )


@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    if not await check_membership(client, message.from_user.id):
        await message.reply_text(
            "⚠️ عذراً، يجب عليك الإشتراك في قناة البوت أولاً لاستخدامه!",
            reply_markup=force_sub_keyboard()
        )
        return
    await message.reply_text("أهلاً بك! أرسل لي أي رابط (فيديو أو صورة) وسأنزله لك بأعلى جودة متوفرة 🔥")


@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply_text(
        "📖 طريقة الاستخدام:\n\n"
        "1️⃣ أرسل رابط (فيديو أو صورة أو ألبوم صور)\n"
        "2️⃣ انتظر التحميل\n"
        "3️⃣ سأرسل لك المحتوى بأعلى جودة متوفرة\n\n"
        "⚠️ حد أقصى لملفات الفيديو: 2GB"
    )


def build_video_opts():
    opts = {
        'ffmpeg_location': imageio_ffmpeg.get_ffmpeg_exe(),
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title).100s_%(id)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'concurrent_fragment_downloads': 5,
        'quiet': True,
        'no_warnings': True,
        'restrictfilenames': True,
        'socket_timeout': 30,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios']
            }
        }
    }
    if HAS_COOKIES:
        opts['cookiefile'] = COOKIES_FILE
    return opts


# ============================================
# صور انستغرام عبر RapidAPI
# ============================================
def _find_media_list(node):
    """يدوّر على أول قائمة عناصر كل عنصر فيها حقل url."""
    if isinstance(node, list):
        if node and all(isinstance(i, dict) and "url" in i for i in node):
            return node
        for i in node:
            found = _find_media_list(i)
            if found:
                return found
    elif isinstance(node, dict):
        for v in node.values():
            found = _find_media_list(v)
            if found:
                return found
    return []


def _collect_media_dicts(node, out):
    """خطة بديلة: يجمع أي عنصر فيه url و type يبدأ بـ image أو video (حتى لو مو داخل قائمة)."""
    if isinstance(node, dict):
        t = node.get("type")
        if node.get("url") and isinstance(t, str) and t.startswith(("image", "video")):
            out.append(node)
        for v in node.values():
            _collect_media_dicts(v, out)
    elif isinstance(node, list):
        for i in node:
            _collect_media_dicts(i, out)


def fetch_instagram_images(url):
    """
    يجيب وسائط منشور انستغرام (صور وفيديوهات وريلز وألبومات) من RapidAPI،
    وينزّلها بمجلد downloads، ويرجّع قائمة من (مسار_الملف, هل_فيديو).
    """
    if not RAPIDAPI_KEY:
        logger.error("IG_API_DEBUG: متغير RAPIDAPI_KEY غير موجود")
        return []

    # نشيل الباراميترات (img_index وغيرها) حتى يرجع المنشور كامل
    clean_url = url.split("?")[0]
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
    }

    try:
        resp = requests.get(
            f"https://{RAPIDAPI_HOST}/instagram/",
            params={"url": clean_url},
            headers=headers,
            timeout=45,
        )
    except Exception as e:
        logger.error(f"IG_API_DEBUG: فشل الاتصال بـ RapidAPI: {e}")
        return []

    logger.info(f"IG_API_DEBUG: status_code={resp.status_code}")
    if resp.status_code != 200:
        logger.error(f"IG_API_DEBUG: رد غير ناجح: {resp.text[:200]}")
        return []

    try:
        data = resp.json()
    except Exception as e:
        logger.error(f"IG_API_DEBUG: فشل تحليل JSON: {e} - {resp.text[:150]}")
        return []

    items = _find_media_list(data)
    if not items:
        # ممكن المنشور بصورة وحدة يرجع بشكل ثاني (مو قائمة)
        items = []
        _collect_media_dicts(data, items)
    logger.info(f"IG_API_DEBUG: عدد العناصر بالرد = {len(items)}")
    if not items:
        logger.info(f"IG_API_DEBUG: الرد الخام (أول 600 حرف) = {resp.text[:600]}")

    logger.info(f"IG_API_DEBUG: أنواع العناصر = {[str(i.get('type')) for i in items]}")

    results = []  # (path, is_video)
    for item in items:
        is_video = str(item.get("type", "image")).startswith("video")
        try:
            r = requests.get(item["url"], timeout=120 if is_video else 60)
            r.raise_for_status()
        except Exception as e:
            logger.error(f"IG_API_DEBUG: فشل تنزيل عنصر: {e}")
            continue
        ext = "mp4" if is_video else "jpg"
        path = os.path.join(DOWNLOAD_DIR, f"ig_{uuid.uuid4().hex}.{ext}")
        with open(path, "wb") as f:
            f.write(r.content)
        results.append((path, is_video))

    logger.info(f"IG_API_DEBUG: عدد العناصر المنزّلة = {len(results)}")
    return results


def fetch_tiktok_images(url):
    """
    يجيب صور منشورات التيك توك (Photo Mode) من خدمة tikwm المجانية،
    وينزّلها بمجلد downloads، ويرجّع قائمة بمسارات الملفات.
    """
    try:
        resp = requests.get(
            "https://www.tikwm.com/api/",
            params={"url": url},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        data = resp.json()
    except Exception as e:
        logger.error(f"TT_API_DEBUG: فشل الطلب: {e}")
        return []

    logger.info(f"TT_API_DEBUG: status={resp.status_code} code={data.get('code')} msg={data.get('msg')}")
    image_urls = (data.get("data") or {}).get("images") or []
    logger.info(f"TT_API_DEBUG: عدد الصور = {len(image_urls)}")

    paths = []
    for u in image_urls:
        try:
            r = requests.get(u, timeout=60)
            r.raise_for_status()
        except Exception as e:
            logger.error(f"TT_API_DEBUG: فشل تنزيل صورة: {e}")
            continue
        path = os.path.join(DOWNLOAD_DIR, f"tt_{uuid.uuid4().hex}.jpg")
        with open(path, "wb") as f:
            f.write(r.content)
        paths.append(path)
    return [(p, False) for p in paths]


def is_instagram_url(url):
    return "instagram.com" in url.lower()


def is_tiktok_url(url):
    return "tiktok.com" in url.lower()


@app.on_message(filters.text & ~filters.command(["start", "help"]))
async def download_media(client, message):
    if not await check_membership(client, message.from_user.id):
        await message.reply_text(
            "⚠️ عذراً، يجب عليك الإشتراك في قناة البوت أولاً لتتمكن من التحميل!",
            reply_markup=force_sub_keyboard()
        )
        return

    url = message.text.strip()
    if not url.startswith("http"):
        await message.reply_text("❌ أرسل رابط صحيح يبدأ بـ http أو https")
        return

    msg = await message.reply_text("⏳ جاري سحب المحتوى بأعلى جودة ممكنة...")

    file_path = None
    media_items = []  # (path, is_video)
    try:
        loop = asyncio.get_event_loop()
        ydl_opts = build_video_opts()

        def run_dl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                dl_info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(dl_info)

        try:
            # ============================================
            # المحاولة الأولى: نتعامل معه كفيديو
            # ============================================
            file_path = await loop.run_in_executor(None, run_dl)

            if not os.path.exists(file_path):
                base, _ = os.path.splitext(file_path)
                file_path = base + ".mp4"

            if not os.path.exists(file_path):
                await msg.edit_text("❌ فشل التحميل: الملف لم يُحفظ")
                return

            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                os.remove(file_path)
                await msg.edit_text("❌ حجم الملف كبير جداً (أكثر من 2GB)")
                return

            await msg.edit_text("🚀 تم التحميل، جاري الرفع إلى تلغرام...")
            await message.reply_video(video=file_path, supports_streaming=True)
            await msg.delete()

        except Exception as video_error:
            error_msg = str(video_error)
            logger.info(f"فشل التحميل كفيديو، السبب: {error_msg[:150]}")

            # ============================================
            # المحاولة الثانية: صور انستغرام عبر RapidAPI
            # ============================================
            ig_photo = is_instagram_url(url) and (
                "No video formats" in error_msg or "Instagram" in error_msg
            )
            tt_photo = is_tiktok_url(url)

            if ig_photo or tt_photo:
                await msg.edit_text("🖼️ جاري التحميل من المصدر البديل...")

                fetcher = fetch_instagram_images if ig_photo else fetch_tiktok_images
                media_items = await loop.run_in_executor(None, fetcher, url)

                if not media_items:
                    await msg.edit_text(
                        "❌ لا يمكن تحميل هذا المحتوى\n"
                        "قد يكون الرابط خاصاً أو محذوفاً أو غير مدعوم."
                    )
                    return

                # تليجرام يسمح بـ 10 عناصر بالألبوم الواحد
                for start in range(0, len(media_items), 10):
                    chunk = media_items[start:start + 10]
                    if len(chunk) == 1:
                        path, is_video = chunk[0]
                        if is_video:
                            await message.reply_video(video=path, supports_streaming=True)
                        else:
                            await message.reply_photo(photo=path)
                    else:
                        await message.reply_media_group(
                            media=[
                                InputMediaVideo(path) if is_video else InputMediaPhoto(path)
                                for path, is_video in chunk
                            ]
                        )

                await msg.delete()
            else:
                raise video_error

    except Exception as e:
        error_msg = str(e)
        logger.error(f"خطأ بالتحميل: {error_msg}")

        if "No video formats" in error_msg or "Instagram" in error_msg:
            await msg.edit_text(
                "❌ لا يمكن تحميل هذا المحتوى\n"
                "حاول مع موقع آخر أو تأكد أن الرابط عام."
            )
        elif "403" in error_msg or "404" in error_msg:
            await msg.edit_text("❌ الرابط غير صحيح أو محذوف")
        else:
            await msg.edit_text(f"❌ حدث خطأ:\n{error_msg[:200]}")

    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as cleanup_err:
                logger.error(f"فشل حذف الملف: {cleanup_err}")
        for p, _ in media_items:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception as cleanup_err:
                logger.error(f"فشل حذف الصورة: {cleanup_err}")


if __name__ == "__main__":
    if not HAS_COOKIES:
        logger.warning("⚠️ ملف cookies.txt غير موجود - بعض المحتوى الخاص قد لا يعمل")
    if not RAPIDAPI_KEY:
        logger.warning("⚠️ متغير RAPIDAPI_KEY غير موجود - صور انستغرام ما راح تشتغل")
    logger.info("🚀 البوت شغال الآن بنجاح...")
    app.run()

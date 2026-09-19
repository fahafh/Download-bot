import os
import re
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

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

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


def get_requests_session():
    """يجهز جلسة requests فيها كوكيز انستغرام (لو موجودة) عشان نقدر نفتح منشورات خاصة"""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    if HAS_COOKIES:
        jar = http.cookiejar.MozillaCookieJar(COOKIES_FILE)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = jar
        except Exception as e:
            logger.error(f"فشل تحميل الكوكيز: {e}")
    return session


def fetch_instagram_images(url):
    """
    يجيب روابط الصور من منشور انستغرام (صورة وحدة أو ألبوم كاروسيل)
    عن طريق تحليل كود الصفحة مباشرة بدل الاعتماد على yt-dlp
    """
    session = get_requests_session()
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    html = resp.text

    # نبحث عن كل روابط الصور عالية الجودة المذكورة بكود الصفحة (display_url)
    raw_urls = re.findall(r'"display_url":"(https:[^"]+?)"', html)

    # تنظيف الروابط من الـ escape characters
    clean_urls = []
    seen = set()
    for u in raw_urls:
        clean = u.encode().decode('unicode_escape')
        clean = clean.replace('\\/', '/')
        if clean not in seen:
            seen.add(clean)
            clean_urls.append(clean)

    return clean_urls


def is_instagram_url(url):
    return "instagram.com" in url.lower()


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
    try:
        loop = asyncio.get_event_loop()
        ydl_opts = build_video_opts()

        def run_dl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                dl_info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(dl_info)

        try:
            # ============================================
            # المحاولة الأولى: نتعامل معه كفيديو (يوتيوب، تيك توك، ريلز، الخ)
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

            # ============================================
            # لو فشل كفيديو وكان الرابط من انستغرام، نجرب كصورة/ألبوم
            # ============================================
            if is_instagram_url(url) and (
                "No video formats" in error_msg or "Instagram" in error_msg
            ):
                await msg.edit_text("🖼️ يبدو أنه منشور صور، جاري التحميل...")

                image_urls = await loop.run_in_executor(
                    None, fetch_instagram_images, url
                )

                if not image_urls:
                    await msg.edit_text(
                        "❌ لا يمكن تحميل هذا المحتوى (يحتاج تسجيل دخول/كوكيز أو الرابط خاص)\n"
                        "حاول مع موقع آخر أو تأكد أن الرابط عام."
                    )
                    return

                if len(image_urls) == 1:
                    await message.reply_photo(photo=image_urls[0])
                else:
                    media_group = [InputMediaPhoto(u) for u in image_urls[:10]]
                    await message.reply_media_group(media=media_group)

                await msg.delete()
            else:
                raise video_error

    except Exception as e:
        error_msg = str(e)
        logger.error(f"خطأ بالتحميل: {error_msg}")

        if "No video formats" in error_msg or "Instagram" in error_msg:
            await msg.edit_text(
                "❌ لا يمكن تحميل هذا المحتوى (يحتاج تسجيل دخول/كوكيز أو الرابط خاص)\n"
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


if __name__ == "__main__":
    if not HAS_COOKIES:
        logger.warning("⚠️ ملف cookies.txt غير موجود - تحميل انستغرام لن يعمل بشكل صحيح")
    logger.info("🚀 البوت شغال الآن بنجاح...")
    app.run()


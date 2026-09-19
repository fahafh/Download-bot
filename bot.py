import os
import re
import asyncio
import logging
import imageio_ffmpeg
import instaloader
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
# بيانات حساب انستغرام (لـ instaloader) - تُقرأ من Railway Variables
# ============================================
IG_USERNAME = os.environ.get("IG_USERNAME", "")
IG_PASSWORD = os.environ.get("IG_PASSWORD", "")

# ============================================
# مجلد التحميل - ينشئ تلقائياً لو مو موجود
# ============================================
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ============================================
# ملف الكوكيز (لـ yt-dlp، اختياري - يفيد بالفيديوهات/الريلز الخاصة)
# ============================================
COOKIES_FILE = "cookies.txt"
HAS_COOKIES = os.path.exists(COOKIES_FILE)

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

app = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ============================================
# إعداد instaloader (لتحميل صور/ألبومات انستغرام)
# ============================================
IG_LOADER = instaloader.Instaloader(
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    quiet=True,
)
IG_LOGGED_IN = False


def instaloader_login():
    """يسجل دخول انستغرام مرة وحدة عند بدء تشغيل البوت"""
    global IG_LOGGED_IN
    if not IG_USERNAME or not IG_PASSWORD:
        logger.warning("⚠️ IG_USERNAME/IG_PASSWORD غير موجودين - دعم صور انستغرام معطل")
        return
    try:
        IG_LOADER.login(IG_USERNAME, IG_PASSWORD)
        IG_LOGGED_IN = True
        logger.info("✅ instaloader: تسجيل الدخول لانستغرام نجح")
    except Exception as e:
        logger.error(f"❌ instaloader: فشل تسجيل الدخول لانستغرام: {e}")
        IG_LOGGED_IN = False


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


def extract_shortcode(url):
    """يستخرج الكود المختصر (shortcode) من رابط منشور انستغرام"""
    match = re.search(r'instagram\.com/(?:[^/]+/)?(?:p|reel|tv)/([^/?#&]+)', url)
    return match.group(1) if match else None


def fetch_instagram_images(url):
    """
    يجيب روابط الصور من منشور انستغرام (صورة وحدة أو ألبوم كاروسيل)
    باستخدام instaloader بدل yt-dlp
    """
    if not IG_LOGGED_IN:
        logger.error("IG_DEBUG: لا يمكن استخدام instaloader - تسجيل الدخول غير مفعّل")
        return []

    shortcode = extract_shortcode(url)
    if not shortcode:
        logger.error("IG_DEBUG: لم يتم استخراج shortcode من الرابط")
        return []

    logger.info(f"IG_DEBUG: shortcode المستخرج = {shortcode}")

    try:
        post = instaloader.Post.from_shortcode(IG_LOADER.context, shortcode)
    except Exception as e:
        logger.error(f"IG_DEBUG: فشل جلب المنشور عبر instaloader: {e}")
        return []

    urls = []
    try:
        if post.typename == "GraphSidecar":
            # ألبوم كاروسيل (عدة صور/فيديوهات)
            for node in post.get_sidecar_nodes():
                if not node.is_video:
                    urls.append(node.display_url)
        else:
            # منشور مفرد
            if not post.is_video:
                urls.append(post.url)
    except Exception as e:
        logger.error(f"IG_DEBUG: خطأ أثناء استخراج روابط الصور: {e}")

    logger.info(f"IG_DEBUG: عدد الصور المستخرجة = {len(urls)}")
    return urls


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
            logger.info(f"IG_DEBUG: فشل التحميل كفيديو، السبب: {error_msg[:150]}")

            # ============================================
            # لو فشل كفيديو وكان الرابط من انستغرام، نجرب كصورة/ألبوم عبر instaloader
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
        logger.warning("⚠️ ملف cookies.txt غير موجود - بعض فيديوهات انستغرام الخاصة قد لا تعمل")

    instaloader_login()

    logger.info("🚀 البوت شغال الآن بنجاح...")
    app.run()

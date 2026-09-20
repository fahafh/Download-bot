import os
import uuid
import asyncio
import logging
import subprocess
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
# ملف الكوكيز (لانستغرام، تيك توك وغيرها) - اختياري
# ============================================
COOKIES_FILE = "cookies.txt"
HAS_COOKIES = os.path.exists(COOKIES_FILE)

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}

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


def download_via_gallery_dl(url):
    """
    يستخدم gallery-dl كطريقة احتياطية لتحميل الصور/الألبومات
    اللي ما يقدر yt-dlp يتعامل معها (منشورات صور بدون فيديو)
    يرجع: (مسار المجلد المؤقت, قائمة مسارات الملفات المحملة)
    """
    temp_dir = os.path.join(DOWNLOAD_DIR, f"gdl_{uuid.uuid4().hex[:10]}")
    os.makedirs(temp_dir, exist_ok=True)

    cmd = ["gallery-dl", "--dest", temp_dir, "-q", "--no-mtime"]
    if HAS_COOKIES:
        cmd += ["--cookies", COOKIES_FILE]
    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
        )
        logger.info(f"GDL_DEBUG: returncode={result.returncode}")
        if result.stderr:
            logger.info(f"GDL_DEBUG: stderr={result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        logger.error("GDL_DEBUG: انتهت المهلة أثناء تحميل gallery-dl")
        return temp_dir, []
    except FileNotFoundError:
        logger.error("GDL_DEBUG: أمر gallery-dl غير موجود - تأكد من تثبيته بـ requirements.txt")
        return temp_dir, []

    files = []
    for root, _, filenames in os.walk(temp_dir):
        for fn in filenames:
            files.append(os.path.join(root, fn))

    logger.info(f"GDL_DEBUG: عدد الملفات المحملة = {len(files)}")
    return temp_dir, files


def cleanup_dir(path):
    """يحذف مجلد مؤقت وكل محتوياته بأمان"""
    try:
        for root, _, filenames in os.walk(path, topdown=False):
            for fn in filenames:
                os.remove(os.path.join(root, fn))
            os.rmdir(root)
    except Exception as e:
        logger.error(f"فشل حذف المجلد المؤقت: {e}")


async def handle_gallery_dl_fallback(message, msg, url):
    """يحاول تحميل المحتوى عبر gallery-dl ويرسله (صور/فيديو حسب النوع)"""
    await msg.edit_text("🖼️ جاري تجربة طريقة بديلة للتحميل...")

    loop = asyncio.get_event_loop()
    temp_dir, files = await loop.run_in_executor(None, download_via_gallery_dl, url)

    if not files:
        cleanup_dir(temp_dir)
        await msg.edit_text(
            "❌ لا يمكن تحميل هذا المحتوى بأي طريقة متاحة\n"
            "قد يكون الرابط خاصاً، محذوفاً، أو من نوع غير مدعوم."
        )
        return

    images = [f for f in files if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS]
    videos = [f for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS]

    try:
        if videos:
            for v in videos:
                size = os.path.getsize(v)
                if size > MAX_FILE_SIZE:
                    await message.reply_text("❌ أحد ملفات الفيديو كبير جداً (أكثر من 2GB)، تم تخطيه")
                    continue
                await message.reply_video(video=v, supports_streaming=True)

        if images:
            if len(images) == 1:
                await message.reply_photo(photo=images[0])
            else:
                # تلغرام يدعم حتى 10 عناصر بالألبوم الواحد
                for i in range(0, len(images), 10):
                    chunk = images[i:i + 10]
                    media_group = [InputMediaPhoto(open(p, "rb")) for p in chunk]
                    await message.reply_media_group(media=media_group)

        await msg.delete()

    except Exception as e:
        logger.error(f"خطأ أثناء رفع ملفات gallery-dl: {e}")
        await msg.edit_text(f"❌ حدث خطأ أثناء الرفع:\n{str(e)[:200]}")

    finally:
        cleanup_dir(temp_dir)


def is_photo_related_error(error_msg):
    return "No video formats" in error_msg or "Instagram" in error_msg or "TikTok" in error_msg


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
            # المحاولة الثانية: gallery-dl (لمنشورات الصور/الألبومات)
            # ============================================
            if is_photo_related_error(error_msg):
                await handle_gallery_dl_fallback(message, msg, url)
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


if __name__ == "__main__":
    if not HAS_COOKIES:
        logger.warning("⚠️ ملف cookies.txt غير موجود - بعض المحتوى الخاص قد لا يعمل")
    logger.info("🚀 البوت شغال الآن بنجاح...")
    app.run()

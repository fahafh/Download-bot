import os
import asyncio
import logging
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


def get_info(url):
    """يجيب معلومات الرابط بدون تحميل، عشان نعرف نوع المحتوى (فيديو/صورة/ألبوم)"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    if HAS_COOKIES:
        opts['cookiefile'] = COOKIES_FILE
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def collect_image_urls(info):
    """يستخرج روابط الصور من منشور (صورة واحدة أو ألبوم كاروسيل)"""
    urls = []

    def extract_from_entry(entry):
        # لو عنده صيغ (formats)، خذ أعلى جودة صورة متوفرة
        if entry.get('formats'):
            image_formats = [f for f in entry['formats'] if f.get('url')]
            if image_formats:
                return image_formats[-1]['url']
        if entry.get('url'):
            return entry['url']
        if entry.get('thumbnail'):
            return entry['thumbnail']
        return None

    if info.get('entries'):
        for entry in info['entries']:
            img = extract_from_entry(entry)
            if img:
                urls.append(img)
    else:
        img = extract_from_entry(info)
        if img:
            urls.append(img)

    return urls


def is_video_entry(entry):
    """يتحقق هل هذا العنصر فيديو فعلي (عنده صيغة فيها فيديو)"""
    formats = entry.get('formats') or []
    for f in formats:
        if f.get('vcodec') and f.get('vcodec') != 'none':
            return True
    return entry.get('ext') in ('mp4', 'webm', 'mkv', 'mov')


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

    msg = await message.reply_text("⏳ جاري تحليل الرابط...")

    file_path = None
    try:
        # الخطوة 1: نجيب معلومات المحتوى بدون تحميل عشان نعرف نوعه
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, get_info, url)

        has_entries = bool(info.get('entries'))
        single_is_video = (not has_entries) and is_video_entry(info)

        # ============================================
        # الحالة 1: فيديو واحد (مو ألبوم)
        # ============================================
        if single_is_video:
            await msg.edit_text("⏳ جاري سحب الفيديو بأعلى جودة ممكنة...")
            ydl_opts = build_video_opts()

            def run_dl():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    dl_info = ydl.extract_info(url, download=True)
                    return ydl.prepare_filename(dl_info)

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

        # ============================================
        # الحالة 2: صورة واحدة أو ألبوم صور (كاروسيل)
        # ============================================
        else:
            await msg.edit_text("🖼️ جاري تحميل الصور...")
            image_urls = collect_image_urls(info)

            if not image_urls:
                await msg.edit_text("❌ لم يتم العثور على محتوى قابل للتحميل بهذا الرابط")
                return

            if len(image_urls) == 1:
                await message.reply_photo(photo=image_urls[0])
            else:
                # تلغرام يدعم حتى 10 عناصر بالألبوم الواحد
                media_group = [InputMediaPhoto(u) for u in image_urls[:10]]
                await message.reply_media_group(media=media_group)

            await msg.delete()

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


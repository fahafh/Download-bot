import os
import asyncio
import logging
import imageio_ffmpeg
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    await message.reply_text("أهلاً بك! أرسل لي أي رابط وسأنزله لك بأعلى دقة متوفرة 🔥")


@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply_text(
        "📖 طريقة الاستخدام:\n\n"
        "1️⃣ أرسل رابط الفيديو (يوتيوب، تيك توك، انستغرام، تويتر...)\n"
        "2️⃣ انتظر التحميل\n"
        "3️⃣ سأرسل لك الفيديو\n\n"
        "⚠️ حد أقصى للملف: 2GB"
    )


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
        return

    msg = await message.reply_text("⏳ جاري سحب الفيديو بأعلى جودة ممكنة...")

    ydl_opts = {
        'ffmpeg_location': imageio_ffmpeg.get_ffmpeg_exe(),
        'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
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
        ydl_opts['cookiefile'] = COOKIES_FILE

    file_path = None
    try:
        loop = asyncio.get_event_loop()

        def run_dl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

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

    except Exception as e:
        error_msg = str(e)
        logger.error(f"خطأ بالتحميل: {error_msg}")

        if "No video formats" in error_msg or "Instagram" in error_msg:
            await msg.edit_text(
                "❌ لا يمكن تحميل هذا الفيديو (يحتاج تسجيل دخول/كوكيز)\n"
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
    app.run()    # تحقق من أن النص رابط
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.reply_text("❌ أرسل رابط صحيح يبدأ بـ http أو https")
        return
    
    # رسالة جاري التحميل
    status_message = await message.reply_text("⏳ جاري التحميل... انتظر قليلاً")
    
    try:
        # إعدادات yt-dlp
        ydl_opts = {
            'format': 'best[height<=720]/best',
            'outtmpl': os.path.join(DOWNLOAD_PATH, '%(title)s.%(ext)s'),
            'quiet': False,
            'no_warnings': False,
            'socket_timeout': 30,
            'http_chunk_size': 1024 * 1024,
        }
        
        # تحميل الفيديو
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"تحميل الفيديو من: {url}")
            info = ydl.extract_info(url, download=True)
            video_path = ydl.prepare_filename(info)
        
        # التحقق من أن الملف موجود
        if not os.path.exists(video_path):
            await status_message.edit_text("❌ فشل التحميل: الملف لم يُحفظ")
            return
        
        # حجم الملف
        file_size = os.path.getsize(video_path)
        
        # إذا كان أكبر من 2GB
        if file_size > 2 * 1024 * 1024 * 1024:
            os.remove(video_path)
            await status_message.edit_text("❌ حجم الملف كبير جداً (أكثر من 2GB)")
            return
        
        # إرسال الفيديو
        await status_message.edit_text("📤 جاري إرسال الفيديو...")
        
        await message.reply_video(
            video=open(video_path, 'rb'),
            caption=f"✅ تم التحميل بنجاح!\n\n📝 العنوان: {info.get('title', 'بدون عنوان')}"
        )
        
        # حذف الملف بعد الإرسال
        await status_message.delete()
        os.remove(video_path)
        logger.info(f"تم إرسال الفيديو بنجاح")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"خطأ في التحميل: {error_msg}")
        
        # رسائل خطأ مخصصة
        if "No video formats" in error_msg or "Instagram" in error_msg:
            await status_message.edit_text(
                "❌ لا يمكن تحميل من هذا الموقع\n"
                "حاول مع موقع آخر مثل YouTube أو TikTok"
            )
        elif "403" in error_msg or "404" in error_msg:
            await status_message.edit_text(
                "❌ الرابط غير صحيح أو محذوف"
            )
        else:
            await status_message.edit_text(
                f"❌ حدث خطأ:\n{error_msg[:100]}"
            )

# ============================================
# تشغيل البوت
# ============================================
if __name__ == "__main__":
    logger.info("🚀 البوت يعمل الآن...")
    app.run()

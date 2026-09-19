import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
import os
import time
from pathlib import Path

# إعدادات التسجيل
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# بيانات API
API_ID = 33133014
API_HASH = "fa7e20bfaa94f3901adbbc6c082c1c77"
BOT_TOKEN = "8721027576:AAGvAWmFvoyrOdBQ8FTSeTFpbIQX-5WmitI"
CHANNEL_USERNAME = "ht4h4"

# مسار التخزين (استخدام /tmp/ للـ Railway/PythonAnywhere)
DOWNLOAD_PATH = "/tmp/downloads/"
Path(DOWNLOAD_PATH).mkdir(parents=True, exist_ok=True)

# إنشاء البوت
app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ============================================
# فحص الاشتراك الإجباري
# ============================================
async def check_subscription(client, user_id):
    """التحقق من اشتراك المستخدم في القناة"""
    try:
        member = await client.get_chat_member(CHANNEL_USERNAME, user_id)
        # إذا كان المستخدم عضو أو إداري أو مالك
        return member.status in ["creator", "administrator", "member"]
    except:
        return False

# ============================================
# أوامر البوت
# ============================================

@app.on_message(filters.command("start"))
async def start_command(client, message):
    """أمر البداية"""
    user_id = message.from_user.id
    
    # فحص الاشتراك
    if not await check_subscription(client, user_id):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("اشترك في القناة", url=f"https://t.me/{CHANNEL_USERNAME}")]
        ])
        await message.reply_text(
            "❌ أنت لم تشترك في القناة!\n\n"
            "يجب الاشتراك أولاً لاستخدام البوت.",
            reply_markup=keyboard
        )
        return
    
    # رسالة الترحيب
    welcome_text = """
👋 مرحباً بك في بوت تحميل الفيديوهات!

🎬 أرسل لي رابط من:
  • YouTube
  • Instagram
  • TikTok
  • Facebook
  • Twitter
  • وغيرها...

📥 سأحمّل الفيديو لك مباشرة!

/help - للمساعدة
"""
    await message.reply_text(welcome_text)

@app.on_message(filters.command("help"))
async def help_command(client, message):
    """أمر المساعدة"""
    help_text = """
📖 طريقة الاستخدام:

1️⃣ أرسل رابط الفيديو
2️⃣ انتظر التحميل
3️⃣ سأرسل لك الفيديو

⚠️ ملاحظات مهمة:
  • الفيديوهات الطويلة قد تأخذ وقتاً
  • حد أقصى للملف: 2GB
  • قد لا تعمل بعض المواقع

/start - للعودة للقائمة الرئيسية
"""
    await message.reply_text(help_text)

@app.on_message(filters.text & ~filters.command())
async def download_video(client, message):
    """تحميل الفيديو من الرابط"""
    user_id = message.from_user.id
    url = message.text.strip()
    
    # فحص الاشتراك
    if not await check_subscription(client, user_id):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("اشترك في القناة", url=f"https://t.me/{CHANNEL_USERNAME}")]
        ])
        await message.reply_text(
            "❌ يجب الاشتراك في القناة أولاً!",
            reply_markup=keyboard
        )
        return
    
    # تحقق من أن النص رابط
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

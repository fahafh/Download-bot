import os
import asyncio
import imageio_ffmpeg
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

API_ID = 33133014
API_HASH = "fa7e20bfaa94f3901adbbc6c082c1c77"
BOT_TOKEN = "8721027576:AAGvAWmFvoyrOdBQ8FTSeTFpbIQX-5WmitI"

CHANNEL_USERNAME = "ht4h4"

app = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# دالة التحقق من الاشتراك الدقيقة
async def check_membership(client, user_id):
    try:
        chat = f"@{CHANNEL_USERNAME}"
        member = await client.get_chat_member(chat, user_id)
        # السماح للعضو، الأدمن، أو منشئ القناة (المالك)
        if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            return True
        return False
    except UserNotParticipant:
        return False
    except Exception as e:
        print(f"Error checking membership: {e}")
        return True  # في حال حدوث أي خطأ في الوصول لا يتم حظر المستخدم

def force_sub_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 اشترك في القناة أولاً", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub")]
    ])

@app.on_callback_query(filters.regex("check_sub"))
async def check_callback(client, callback_query):
    if await check_membership(client, callback_query.from_user.id):
        await callback_query.message.delete()
        await callback_query.message.reply_text("✅ تم التحقق من اشتراكك بنجاح! أرسل رابط الفيديو الآن وسأنزله لك 🔥")
    else:
        await callback_query.answer("⚠️ أنت غير مشترك في القناة بعد! اشترك ثم اضغط تحقق.", show_alert=True)

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    if not await check_membership(client, message.from_user.id):
        await message.reply_text(
            "⚠️ عذراً، يجب عليك الإشتراك في قناة البوت أولاً لاستخدامه!",
            reply_markup=force_sub_keyboard()
        )
        return
    await message.reply_text("أهلاً بك! أرسل لي أي رابط وسأنزله لك بأعلى دقة متوفرة 🔥")

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
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'merge_output_format': 'mp4',
        'concurrent_fragment_downloads': 5,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios']
            }
        }
    }

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

        await msg.edit_text("🚀 تم التحميل، جاري الرفع إلى تلغرام...")
        await message.reply_video(video=file_path, supports_streaming=True)
        await msg.delete()

        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ:\n{str(e)}")

print("البوت شغال الآن بنجاح...")
app.run()

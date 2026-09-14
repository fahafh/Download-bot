import os
import asyncio
from pyrogram import Client, filters
import yt_dlp

API_ID = 33133014
API_HASH = "fa7e20bfaa94f3901adbbc6c082c1c77"
BOT_TOKEN = "8721027576:AAGvAWmFvoyrOdBQ8FTSeTFpbIQX-5WmitI"

app = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text("أهلاً بك! أرسل لي أي رابط وسأنزله لك بأعلى دقة متوفرة 🔥")

@app.on_message(filters.text & ~filters.command(["start", "help"]))
async def download_media(client, message):
    url = message.text.strip()
    if not url.startswith("http"):
        return

    msg = await message.reply_text("⏳ جاري سحب الفيديو بأعلى جودة ممكنة...")

    ydl_opts = {
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

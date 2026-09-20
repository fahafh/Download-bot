import os
import aiohttp
from aiogram.types import Message, BufferedInputFile, InputMediaPhoto, InputMediaVideo
RAPIDAPI_HOST = "instagram-post-reels-stories-downloader-api.p.rapidapi.com"
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
async def fetch_instagram_media(session: aiohttp.ClientSession, post_url: str) -> list[dict]:
    headers = {
        "x-rapidapi-key": os.getenv("RAPIDAPI_KEY", ""),
        "x-rapidapi-host": RAPIDAPI_HOST,
    }
    # نشيل الباراميترات (img_index وغيرها) حتى يرجع المنشور كامل
    clean_url = post_url.split("?")[0]
    async with session.get(
        f"https://{RAPIDAPI_HOST}/instagram/",
        params={"url": clean_url},
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=45),
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"RapidAPI status {resp.status}")
        data = await resp.json()
    return _find_media_list(data)
async def send_instagram_media(message: Message, post_url: str):
    async with aiohttp.ClientSession() as session:
        try:
            items = await fetch_instagram_media(session, post_url)
        except Exception as e:
            print("Instagram API error:", e)
            await message.answer("صار خطأ بجلب المنشور، جرّب بعد شوية.")
            return
    if not items:
        await message.answer("ما لقيت وسائط بهذا الرابط (يمكن الحساب خاص).")
        return

    files = []  # (is_video, BufferedInputFile)
    for i, item in enumerate(items):
        is_video = str(item.get("type", "")).startswith("video")
        async with session.get(item["url"], timeout=aiohttp.ClientTimeout(total=60)) as r:
            r.raise_for_status()
            data = await r.read()
        name = f"{i}.{'mp4' if is_video else 'jpg'}"
        files.append((is_video, BufferedInputFile(data, filename=name)))

# تليجرام يسمح بـ 10 عناصر بالألبوم، والألبوم لازم 2 على الأقل
for start in range(0, len(files), 10):
    chunk = files[start:start + 10]
    if len(chunk) == 1:
        is_video, f = chunk[0]
        if is_video:
            await message.answer_video(f)
        else:
            await message.answer_photo(f)
    else:
        media = [
            InputMediaVideo(media=f) if is_video else InputMediaPhoto(media=f)
            for is_video, f in chunk
        ]
        await message.answer_media_group(media)

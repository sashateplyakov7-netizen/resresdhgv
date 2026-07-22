import asyncio
import os
import re
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from supabase import create_client, Client
from dotenv import load_dotenv

from downloader import download_tiktok

# Грузим переменные окружения из .env
load_dotenv()
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Инициализация Supabase и бота
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Регулярка для отлова ссылок на TikTok
TIKTOK_RE = r"https?://(?:www\.|vt\.|vm\.)?tiktok\.com/[^\s]+"

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    
    # Записываем юзера в Supabase (в таблицу 'users')
    try:
        supabase.table("users").upsert({"telegram_id": user_id, "username": username}).execute()
    except Exception as e:
        logging.error(f"Ошибка БД: {e}")

    await message.answer("Скинь ссылку на TikTok, и я пришлю видео без ватермарки.")

@dp.message(F.text.regexp(TIKTOK_RE))
async def handle_tiktok_link(message: types.Message):
    # Достаем ссылку из текста
    match = re.search(TIKTOK_RE, message.text)
    tt_url = match.group(0)
    
    status_msg = await message.answer("⏳ Качаю видео...")

    try:
        # Вызываем функцию из downloader.py
        file_path = await download_tiktok(tt_url)
        
        if file_path and os.path.exists(file_path):
            video_file = FSInputFile(file_path)
            await message.answer_video(video=video_file)
            
            # Удаляем видео с сервера, чтобы не забить память на Render
            os.remove(file_path)
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ Не удалось скачать. Проверь ссылку.")

    except Exception as e:
        logging.error(f"Ошибка скачивания: {e}")
        await status_msg.edit_text("❌ Ошибка при обработке.")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

async def main():
    # Дропаем вебхуки и запускаем Long Polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

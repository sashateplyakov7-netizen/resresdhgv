import asyncio
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from dotenv import load_dotenv
import google.generativeai as genai

# ==========================================
# МИКРО-СЕРВЕР ДЛЯ RENDER (чтобы не килял за отсутствие порта)
# ==========================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# Запускаем веб-заглушку в фоновом потоке ДО бота
threading.Thread(target=run_dummy_server, daemon=True).start()

# ==========================================
# ОСНОВНОЙ КОД БОТА
# ==========================================
load_dotenv()
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
Ты — ультимативный ИИ-помощник, эксперт и топ-трейдер по играм Blox Fruits, ABA (Anime Battle Arena) и AUT (A Universal Time) в Roblox. Твоя цель — помогать игрокам с гайдами, прокачкой, комбо и оценкой трейдов.

Твои главные обязанности:
1. По Blox Fruits: Ты знаешь актуальную ценность (Value) всех фруктов и геймпассов, тир-листы, как получить V4 расы, где фармить левелы, какие мечи/стили боя сейчас в мета-комбо. Помогаешь пользователям понять, выгоден ли их трейд (например: "Стоит ли менять Леопарда на Дракона?").
2. По ABA: Ты досконально знаешь персонажей, их мувсеты, ульты, тактики против разных героев и лучшие комбо (включая true-комбо и бреки). Помогаешь поднимать ранг.
3. По AUT: Ты шаришь в получении редких стендов/абилок (Specs), квестах, ценности скинов и предметов для обмена.
4. Трейдинг: Когда тебя спрашивают про обмен (L или W - Loss или Win), ты должен детально анализировать ценность предметов на текущий момент и давать четкий вердикт.

Стиль общения: Общайся как опытный, но дружелюбный геймер. Используй сленг (мувсет, баф, нерф, спавн, велью, трейд, W/L, плейс). Пиши коротко, емко и по делу, без лишней "воды" и длинных вступлений. Если игрок просит комбо, расписывай его по кнопкам или скиллам пошагово.
"""

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction=SYSTEM_PROMPT
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Здорово! Я твой эксперт по Blox Fruits, ABA и AUT. Задавай любой вопрос по трейдам, комбухам или прокачке!"
    )

@dp.message()
async def handle_user_message(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        response = await asyncio.to_thread(model.generate_content, message.text)
        
        if response.text:
            await message.answer(response.text)
        else:
            await message.answer("Не удалось сгенерировать ответ, попробуй переформулировать.")
            
    except Exception as e:
        logging.error(f"Ошибка Gemini API: {e}")
        await message.answer("Упсс, траблы с нейронкой. Попробуй ещё раз чуть позже.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

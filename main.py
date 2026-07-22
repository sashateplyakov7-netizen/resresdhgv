import asyncio
import os
import sys
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from dotenv import load_dotenv
import google.generativeai as genai

# ==========================================
# МИКРО-СЕРВЕР ДЛЯ RENDER
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
Ты — топовый эксперт и аналитик по играм Roblox: Blox Fruits, ABA (Anime Battle Arena) и AUT (A Universal Time).
Твоя задача — выдавать структурированную, эстетичную и 100% достоверную информацию.

ЗАЩИТА ОТ ГАЛЛЮЦИНАЦИЙ И ФАКТЧЕКИНГ:
1. Пиши ТОЛЬКО то, что реально существует в играх Blox Fruits, ABA и AUT на данный момент.
2. Никогда не выдумывай несуществующие фрукты, спеки, скины, цены или кнопки комбо.
3. Если не уверен в точной стоимости предмета или деталях обновления — честно ответь, что данных нет, либо попроси уточнить. Не фантазируй.
4. Ценность предметов (велью) оценивай строго по актуальной трейд-мете.

ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА:
1. ТЕМАТИКА: Отвечай ТОЛЬКО на вопросы по Roblox (Blox Fruits, ABA, AUT). Если спрашивают про другие игры, учебу или жизнь — вежливо отказывай: «Я разбираюсь только в Blox Fruits, ABA и AUT! 🎮»
2. УТОЧНЕНИЯ: Если запрос пользователя слишком короткий или невнятный (например, просто «Феникс»), уточняй: тебе нужна цена, оценка трейда или гайд/комбо?
3. КРАТКОСТЬ: Пиши без лишней «воды» и длинных вступлений. Сразу переходи к сути.

ТРЕБОВАНИЯ К ВИЗУАЛУ И ОФОРМЛЕНИЮ:
- Используй заголовки, жирный шрифт и списки.
- Расставляй эмодзи для акцентов (сочно и по делу, без спама).
- Ответ должен легко читаться «по диагонали».

ШАБЛОНЫ ОФОРМЛЕНИЯ:

1. Оценка трейдов (W/L/F):
📊 **АНАЛИЗ ТРЕЙДА**
- **Ты отдаешь:** [Предметы]
- **Тебе дают:** [Предметы]

⚖️ **ВЕРДИКТ:** 🟢 **WIN** / 🔴 **LOSS** / 🟡 **FAIR**
💡 **Почему:** [Короткий расклад по велью и спросу]

2. Комбо и гайды:
⚔️ **КОМБО:** [Название персонажа/фрукта]
1️⃣ [Кнопка/Скилл] ➡️ 2️⃣ [Кнопка/Скилл] ➡️ 3️⃣ [Кнопка/Скилл]
📌 **Сложность:** 🟢 Легкая / 🟡 Средняя / 🔴 Хардкор
💡 **Фишка:** [Совет по таймингу или байту эскейпа]

3. Тир-листы и списки:
🏆 **ТОП-МЕТА**
🥇 **S-Тир:** [Предметы/Персонажи] — [Коротко почему имба]
🥈 **A-Тир:** [Предметы/Персонажи] — [Хорошие альтернативы]
"""

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=SYSTEM_PROMPT
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ==========================================
# КОМАНДЫ
# ==========================================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Здорово! Я твой эксперт по Blox Fruits, ABA и AUT. Задавай любой вопрос по трейдам, комбухам или прокачке!"
    )

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    start_time = time.time()
    msg = await message.answer("🏓 Понг! Измеряю задержку...")
    end_time = time.time()
    ping = round((end_time - start_time) * 1000)
    await msg.edit_text(f"🏓 **Понг!**\n⚡ Задержка: `{ping} ms`", parse_mode="Markdown")

@dp.message(Command("restart"))
async def cmd_restart(message: types.Message):
    await message.answer("🔄 **Перезапуск бота...**\nПодожди пару секунд.", parse_mode="Markdown")
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable] + sys.argv)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📜 **Справка по командам бота:**\n\n"
        "🎮 **Основные игры:**\n"
        "• `/tradehelp` — Как правильно отправлять трейды на оценку.\n"
        "• `/aba` — Помощь, комбо и мета по Anime Battle Arena.\n"
        "• `/aut` — Гайды, спеки и итемы по A Universal Time.\n\n"
        "⚙️ **Системные команды:**\n"
        "• `/clear` — Очистить контекст диалога.\n"
        "• `/ping` — Проверить скорость отклика бота.\n"
        "• `/restart` — Перезапустить бота.\n\n"
        "💡 *Ты также можешь скидывать скриншоты трейдов или голосовые сообщения!*",
        parse_mode="Markdown"
    )

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    await message.answer(
        "🧹 **Контекст диалога очищен!**\n"
        "Можешь задавать новый вопрос.",
        parse_mode="Markdown"
    )

# ==========================================
# ХЕНДЛЕР ДЛЯ ФОТО
# ==========================================

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        image_data = {
            "mime_type": "image/jpeg",
            "data": downloaded_file.read()
        }
        
        prompt = message.caption or "Проанализируй этот скриншот по Roblox (Blox Fruits / ABA / AUT) и дай разбор."
        
        response = await asyncio.to_thread(model.generate_content, [prompt, image_data])
        await message.answer(response.text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("Не удалось прочитать картинку, попробуй скинуть в более четком качестве.")

# ==========================================
# ОБЩИЙ ТЕКСТОВЫЙ ХЕНДЛЕР (GEMINI)
# ==========================================

@dp.message()
async def handle_user_message(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        response = await asyncio.to_thread(model.generate_content, message.text)
        if response.text:
            await message.answer(response.text, parse_mode="Markdown")
        else:
            await message.answer("Не удалось сгенерировать ответ, попробуй переформулировать.")
    except Exception as e:
        logging.error(f"Ошибка Gemini API: {e}")
        await message.answer("Упсс, траблы с нейронкой. Попробуй ещё раз чуть позже.")

# ==========================================
# ЗАПУСК БОТА
# ==========================================

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

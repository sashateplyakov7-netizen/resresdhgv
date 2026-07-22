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
# Настройки генерации для максимальной точности
generation_config = {
    "temperature": 0.2,  # Чем ниже (к 0), тем меньше галлюцинаций и выдумок
    "top_p": 0.8,
}

model = genai.GenerativeModel(
    model_name="gemini-3.1-flash-lite",
    system_instruction=SYSTEM_PROMPT,
    generation_config=generation_config
)
model = genai.GenerativeModel(
    model_name="gemini-3.1-flash-lite",
    system_instruction=SYSTEM_PROMPT
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Словарь для хранения историй диалогов по id пользователя
user_chats = {}

def get_user_chat(user_id: int):
    if user_id not in user_chats:
        # Создаем новый чат с памятью для пользователя
        user_chats[user_id] = model.start_chat(history=[])
    return user_chats[user_id]
# ==========================================
# КРАСИВЫЕ КНОПКИ БЫСТРОГО КОНТЕКСТА
# ==========================================
def get_quick_keyboard():
    builder = InlineKeyboardBuilder()
    
    # 1-й ряд: Комбо и Контра
    builder.button(text="⚔️ Разбери комбо", callback_data="action_combo")
    builder.button(text="🛡️ Как контрить?", callback_data="action_counter")
    
    # 2-й ряд: Трейды и Мета
    builder.button(text="📊 Оценка W/L/F", callback_data="action_trade")
    builder.button(text="🏆 Мета & Тир-лист", callback_data="action_meta")
    
    # 3-й ряд: Фишки и Очистка
    builder.button(text="💡 Фишки & Советы", callback_data="action_tips")
    builder.button(text="🧹 Очистить чат", callback_data="action_clear")
    
    # Сетка: по 2 кнопки в 3 ряда
    builder.adjust(2, 2, 2)
    return builder.as_markup()

# ==========================================
# ОБРАБОТКА НАЖАТИЙ НА ИНЛАЙН-КНОПКИ
# ==========================================
@dp.callback_query(F.data.startswith("action_"))
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    # Кнопка очистки контекста
    if callback.data == "action_clear":
        if user_id in user_chats:
            del user_chats[user_id]
        await callback.answer("Чат очищен!")
        await callback.message.answer("🧹 **Контекст диалога очищен!** Задавай новый вопрос.")
        return

    # Карта быстрых промптов для кнопок
    prompts = {
        "action_combo": "Разбери подробное комбо и оптимальную ротацию скиллов для этого персонажа/фрукта.",
        "action_counter": "Расскажи, как эффективно контрить этого персонажа/фрукт, какие у него уязвимости и слабости.",
        "action_trade": "Оцени текущее велью и спрос на этот предмет/фрукт. Стоит ли делать с ним трейды?",
        "action_meta": "Какое место этот персонаж/фрукт занимает в текущей мете (S/A/B тир) и актуален ли он сейчас?",
        "action_tips": "Дай секретные фишки, байты эскейпов или тайминги для эффективной игры."
    }
    
    prompt_text = prompts.get(callback.data)
    if not prompt_text:
        return

    await callback.answer()
    await bot.send_chat_action(chat_id=callback.message.chat.id, action="typing")
    
    try:
        chat = get_user_chat(user_id)
        response = await asyncio.to_thread(chat.send_message, prompt_text)
        
        if response.text:
            try:
                await callback.message.answer(
                    response.text, 
                    parse_mode="Markdown", 
                    reply_markup=get_quick_keyboard()
                )
            except Exception:
                await callback.message.answer(
                    response.text, 
                    reply_markup=get_quick_keyboard()
                )
    except Exception as e:
        logging.error(f"Ошибка при обработке кнопки: {e}")
        await callback.message.answer("Произошла ошибка при обработке запроса.")
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
    user_id = message.from_user.id
    if user_id in user_chats:
        del user_chats[user_id] # Удаляем историю
    await message.answer(
        "🧹 **Контекст диалога очищен!**\nМожешь задавать новый вопрос.",
        parse_mode="Markdown"
    )
# ==========================================
# ХРАНИЛИЩЕ ДАННЫХ, ЛИМИТЫ И ЗАЩИТА ОТ СПАМА
# ==========================================
user_chats = {}
user_requests = defaultdict(list)  # Хранит таймстампы запросов
all_users = set()                  # Список уникальных юзеров
total_requests_count = 0           # Общий счетчик запросов

def get_user_chat(user_id: int):
    """Возвращает или создает сессию чата с памятью для юзера"""
    if user_id not in user_chats:
        user_chats[user_id] = model.start_chat(history=[])
    return user_chats[user_id]

def is_rate_limited(user_id: int) -> bool:
    """
    Защита от крашей и исчерпания API:
    - Максимум 10 запросов в минуту от одного юзера.
    - Автоматическая очистка старых таймстампов.
    """
    now = time.time()
    user_timestamps = user_requests[user_id]
    
    # Очищаем запросы старше 60 секунд
    user_requests[user_id] = [t for t in user_timestamps if now - t < 60]
    
    # Если за последнюю минуту сделано 10 или более запросов — блокируем
    if len(user_requests[user_id]) >= 10:
        return True
    
    # Фиксируем время нового запроса
    user_requests[user_id].append(now)
    return False
# ==========================================
# ХЕНДЛЕР ДЛЯ ФОТО
# ==========================================
@dp.message()
async def handle_user_message(message: types.Message):
    global total_requests_count
    user_id = message.from_user.id
    all_users.add(user_id)

    # 🛡️ ПРОВЕРКА ЛИМИТА: если юзер спамит — останавливаем выполнение
    if is_rate_limited(user_id):
        await message.answer(
            "⏳ **Слишком много запросов!**\n"
            "Пожалуйста, подожди минуту перед следующей отправкой, чтобы не перегружать нейронку."
        )
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    total_requests_count += 1
    
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
        
        # Защита от падающего Markdown
        try:
            await message.answer(response.text, parse_mode="Markdown")
        except Exception:
            await message.answer(response.text)
            
    except Exception as e:
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.answer("Не удалось прочитать картинку, попробуй скинуть в более четком качестве.")

# ==========================================
# ОБЩИЙ ТЕКСТОВЫЙ ХЕНДЛЕР (GEMINI)
# ==========================================
@dp.message()
async def handle_user_message(message: types.Message):
    global total_requests_count
    user_id = message.from_user.id
    all_users.add(user_id)

    # 🛡️ ПРОВЕРКА ЛИМИТА: если юзер спамит — останавливаем выполнение
    if is_rate_limited(user_id):
        await message.answer(
            "⏳ **Слишком много запросов!**\n"
            "Пожалуйста, подожди минуту перед следующей отправкой, чтобы не перегружать нейронку."
        )
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    total_requests_count += 1
    
@dp.message()
async def handle_user_message(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        # Берем чат конкретного пользователя с его историей
        chat = get_user_chat(message.from_user.id)
        
        # Отправляем сообщение ВНУТРЬ чата (сохраняя контекст)
        response = await chat.send_message_async(message.text)
        
        if response.text:
            try:
                await message.answer(response.text, parse_mode="Markdown")
            except Exception:
                await message.answer(response.text)
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

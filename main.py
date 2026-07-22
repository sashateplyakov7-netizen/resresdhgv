import asyncio
import os
import sys
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
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

    def log_message(self, format, *args):
        pass

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌐 HTTP-заглушка запущена на порту {port}")
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()
print("✅ HTTP-сервер работает в фоне")

# ==========================================
# ОСНОВНОЙ КОД БОТА
# ==========================================
load_dotenv()
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logging.error("❌ Не найдены TELEGRAM_TOKEN или GEMINI_API_KEY")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

# ✅ ИСПРАВЛЕНО: ЕДИНЫЙ СИСТЕМНЫЙ ПРОМПТ (без синтаксических ошибок)
SYSTEM_PROMPT = """
Ты — топовый эксперт и аналитик по играм Roblox: Blox Fruits, ABA (Anime Battle Arena) и AUT (A Universal Time).
Твоя задача — выдавать структурированную, эстетичную и 100% достоверную информацию.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 СТРОГИЙ ЗАПРЕТ НА ГАЛЛЮЦИНАЦИИ И ПУТАНИЦУ СКИЛЛОВ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 АБСОЛЮТНЫЙ ЗАПРЕТ НА ВЫДУМКИ И ИМПРЕВИЗАЦИЮ:
 ⚠️ КРИТИЧЕСКИЙ СБОЙ-ЗАПРЕТ: Если пользователь вводит имя персонажа из аниме (Мадара, Годжо, Наруто и т.д.), ТЕБЕ ЗАПРЕЩЕНО использовать свои знания из аниме/манги. Ты знаешь персонажей ИСКЛЮЧИТЕЛЬНО как наборы кнопок [1], [2], [3], [4] из файлов игры. Если точных названий нет — пиши только слоты.
- ПРИ СОМНЕНИИ — СЛОТЫ: Никаких английских названий из аниме-лора под угрозой удаления!
1️⃣ ANIME BATTLE ARENA (ABA):
- ЧЕТКО РАЗДЕЛЯЙ BASE (База: навыки [1], [2], [3], [4]) и AWAKENING / MODE (Пробуждение: навыки [1], [2], [3], [4]).
- ЗАПРЕЩЕНО использовать скиллы из Awakening в комбо для Base-формы!
- ВСЕГДА подписывай кнопки в комбо с точным номером слота, например: [1] Название, [2] Название.
- Запрещено брать названия из аниме-лора, если таких скиллов/слотов нет у персонажа на клавиатуре в ABA.

2️⃣ BLOX FRUITS:
- СКИЛЛЫ ФРУКТОВ И ОРУЖИЯ: Строго соблюдай привязку к кнопкам [Z], [X], [C], [V], [F].
- РАЗДЕЛЕНИЕ UNTRANSFORMED / TRANSFORMED (или Unawakened / Awakened): Не путай скиллы обычной формы фрукта и его трансформации/пробуждения (например, Будда, Дракон, Леопард, Феникс).

3️⃣ A UNIVERSAL TIME (AUT):
- СТРОГАЯ ПРИВЯЗКА К ЛЕЙАТУ: Соблюдай раскладку для стендов/спеков ([E], [R], [T], [Y], [F], [G], [H], [V], [J]).
- Не мешай способности обычной формы с Awakening / Mode / Form Switch.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📚 ОБЩИЕ ПРАВИЛА FACT-CHECKING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Пиши ТОЛЬКО то, что реально существует в актуальных версиях Blox Fruits, ABA и AUT.
2. Если ты не уверен на 100% в точном названии скилла или номере слота для конкретной формы — пиши абстрактно (например, «Используй 1-й скилл [Z] -> 2-й скилл [X]») вместо того, чтобы придумывать название!
3. ТЕМАТИКА: Отвечай ТОЛЬКО на вопросы по Roblox (Blox Fruits, ABA, AUT). Если спрашивают про другие игры, учебу или жизнь — вежливо отказывай: «Я разбираюсь только в Blox Fruits, ABA и AUT! 🎮»
4. УТОЧНЕНИЯ: Если запрос пользователя слишком короткий или невнятный (например, просто «Феникс»), уточняй: тебе нужна цена, оценка трейда или гайд/комбо?
5. КРАТКОСТЬ: Пиши без лишней «воды» и длинных вступлений. Сразу переходи к сути.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 ЖЕСТКОЕ ТРЕБОВАНИЕ К ИСТОЧНИКАМ И БАЗЕ ЗНАНИЙ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ЖЕСТКИЙ СТАНДАРТ ОПИСАНИЯ НАВЫКОВ (БЕЗ НАЗВАНИЙ):
1. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать любые английские, японские или лорные названия скиллов (никаких "Ratio Technique", "Fireball", "Collapse" и т.д.).
2. Описывай ВСЕХ персонажей ИСКЛЮЧИТЕЛЬНО через цифровые слоты и механические типы действий:
   - [1] Скилл: [Тип действия: Рывок / Ближний удар / Стан / Сближение / Дальний снаряд / Атака по площади (AoE) / Контрактара / Защита].
   - [2] Скилл: [Тип действия...].
   - [3] Скилл: [Тип действия...].
   - [4] Скилл: [Тип действия...].
1. Используй только реальную информацию из официальных Вики (ABA Wiki, Blox Fruits Wiki, AUT Wiki).
2. ЗАПРЕЩЕНО выдумывать скиллы из канона аниме/манги (Блич, Наруто, Ван Пис, ДжоДжо и т.д.), если их нет в самой игре на конкретных кнопках.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 ТРЕБОВАНИЯ К ВИЗУАЛУ И ОФОРМЛЕНИЮ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Используй заголовки, жирный шрифт и списки.
- Расставляй эмодзи для акцентов (сочно и по делу, без спама).
- Ответ должен легко читаться «по диагонали».

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 ШАБЛОНЫ ОФОРМЛЕНИЯ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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

generation_config = {
    "temperature": 0.0,  # ⚠️ МАКСИМАЛЬНАЯ ТОЧНОСТЬ
    "top_p": 0.1,
}

# ✅ ИСПРАВЛЕНО: ПРАВИЛЬНАЯ МОДЕЛЬ
model = genai.GenerativeModel(
    model_name="gemini-3.1-flash-lite",  # ✅ Рабочая модель
    system_instruction=SYSTEM_PROMPT,
    generation_config=generation_config
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ==========================================
# ХРАНИЛИЩЕ ДАННЫХ (исправлено)
# ==========================================
user_chats = defaultdict(list)  # user_id: [{"role": "user/assistant", "content": "..."}]
user_requests = defaultdict(list)  # user_id: [timestamps]
all_users = set()
total_requests_count = 0

def add_to_history(user_id: int, role: str, content: str, max_len: int = 10):
    """Добавляет сообщение в историю"""
    chat = user_chats[user_id]
    chat.append({"role": role, "content": content[:200]})
    if len(chat) > max_len:
        chat[:] = chat[-max_len:]

def get_context(user_id: int, question: str) -> str:
    """Формирует промпт с контекстом"""
    chat = user_chats.get(user_id, [])
    if not chat:
        return question
    
    context = []
    for msg in chat[-6:]:
        prefix = "Пользователь" if msg["role"] == "user" else "Ассистент"
        context.append(f"{prefix}: {msg['content']}")
    
    return f"Контекст разговора:\n" + "\n".join(context) + f"\n\nВопрос: {question}"

def is_rate_limited(user_id: int) -> bool:
    """Анти-спам: максимум 10 запросов в минуту"""
    now = time.time()
    timestamps = user_requests[user_id]
    user_requests[user_id] = [t for t in timestamps if now - t < 60]
    
    if len(user_requests[user_id]) >= 10:
        return True
    user_requests[user_id].append(now)
    return False

# ==========================================
# КНОПКИ
# ==========================================
def get_quick_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⚔️ Разбери комбо", callback_data="action_combo")
    builder.button(text="🛡️ Как контрить?", callback_data="action_counter")
    builder.button(text="📊 Оценка W/L/F", callback_data="action_trade")
    builder.button(text="🏆 Мета & Тир-лист", callback_data="action_meta")
    builder.button(text="💡 Фишки & Советы", callback_data="action_tips")
    builder.button(text="🧹 Очистить чат", callback_data="action_clear")
    builder.adjust(2, 2, 2)
    return builder.as_markup()

# ==========================================
# ОБРАБОТКА КНОПОК
# ==========================================
@dp.callback_query(F.data.startswith("action_"))
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if is_rate_limited(user_id):
        await callback.answer("⏳ Слишком много запросов! Подожди 60 секунд.", show_alert=True)
        return
    
    if callback.data == "action_clear":
        if user_id in user_chats:
            del user_chats[user_id]
        await callback.answer("🧹 Чат очищен!")
        await callback.message.delete()
        await callback.message.answer(
            "🧹 **Контекст очищен!**",
            parse_mode="Markdown",
            reply_markup=get_quick_keyboard()
        )
        return

    prompts = {
        "action_combo": "Разбери подробное комбо и оптимальную ротацию скиллов для этого персонажа/фрукта. ВАЖНО: строго разделяй Base и Awakening формы!",
        "action_counter": "Расскажи, как эффективно контрить этого персонажа/фрукт, какие у него уязвимости и слабости.",
        "action_trade": "Оцени текущее велью и спрос на этот предмет/фрукт. Стоит ли делать с ним трейды?",
        "action_meta": "Какое место этот персонаж/фрукт занимает в текущей мете (S/A/B тир) и актуален ли он сейчас?",
        "action_tips": "Дай секретные фишки, байты эскейпов или тайминги для эффективной игры."
    }
    
    prompt_text = prompts.get(callback.data)
    if not prompt_text:
        await callback.answer("⚠️ Неизвестная команда")
        return

    await callback.answer("⏳ Обрабатываю...")
    await bot.send_chat_action(chat_id=callback.message.chat.id, action="typing")
    
    try:
        full_prompt = get_context(user_id, prompt_text)
        response = await asyncio.to_thread(model.generate_content, full_prompt)
        
        if response.text:
            answer = response.text[:4096]
            add_to_history(user_id, "user", prompt_text)
            add_to_history(user_id, "assistant", answer)
            
            try:
                await callback.message.answer(answer, parse_mode="Markdown", reply_markup=get_quick_keyboard())
            except Exception:
                await callback.message.answer(answer, reply_markup=get_quick_keyboard())
        else:
            await callback.message.answer("❌ Не удалось сгенерировать ответ.", reply_markup=get_quick_keyboard())
    except Exception as e:
        logging.error(f"Ошибка кнопки: {e}")
        await callback.message.answer("⚠️ Ошибка.", reply_markup=get_quick_keyboard())

# ==========================================
# КОМАНДЫ
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    all_users.add(message.from_user.id)
    await message.answer(
        "🎮 **Эксперт по Blox Fruits, ABA и AUT!**\n\n"
        "Задавай вопросы по трейдам, комбухам или прокачке!\n"
        "Используй кнопки для быстрых запросов 👇",
        parse_mode="Markdown",
        reply_markup=get_quick_keyboard()
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
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора!")
        return
    await message.answer("🔄 **Перезапуск бота...**", parse_mode="Markdown")
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable] + sys.argv)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📜 **Команды бота:**\n\n"
        "/start — Приветствие с кнопками\n"
        "/ping — Проверить скорость\n"
        "/clear — Очистить историю\n"
        "/stats — Статистика (админ)\n"
        "/broadcast — Рассылка (админ)\n"
        "/admin — Помощь админа\n\n"
        "💡 Используй кнопки под сообщениями!",
        parse_mode="Markdown"
    )

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_chats:
        del user_chats[user_id]
    await message.answer(
        "🧹 **История диалога очищена!**",
        parse_mode="Markdown",
        reply_markup=get_quick_keyboard()
    )

# ==========================================
# АДМИН-ПАНЕЛЬ
# ==========================================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора!")
        return
    await message.answer(
        "👑 **АДМИН-ПАНЕЛЬ**\n\n"
        "• `/stats` — Просмотр статистики\n"
        "• `/broadcast ТЕКСТ` — Рассылка всем пользователям\n"
        "• `/restart` — Перезапуск бота",
        parse_mode="Markdown"
    )

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора!")
        return
    await message.answer(
        f"📊 **СТАТИСТИКА БОТА**\n\n"
        f"👥 Уникальных пользователей: `{len(all_users)}`\n"
        f"💬 Всего обработано запросов: `{total_requests_count}`\n"
        f"🧠 Активных чатов в памяти: `{len(user_chats)}`",
        parse_mode="Markdown"
    )

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора!")
        return
    
    text_to_send = message.text.replace("/broadcast", "").strip()
    if not text_to_send:
        await message.answer("❌ Используй: `/broadcast Твой текст рассылки`", parse_mode="Markdown")
        return

    success = 0
    failed = 0
    await message.answer(f"📢 Запускаю рассылку для {len(all_users)} пользователей...")

    for user_id in list(all_users):
        try:
            await bot.send_message(user_id, text_to_send, parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(
        f"✅ **Рассылка завершена!**\nУспешно: `{success}` | Ошибок: `{failed}`",
        parse_mode="Markdown"
    )

# ==========================================
# ХЕНДЛЕР ФОТО
# ==========================================
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    global total_requests_count
    user_id = message.from_user.id
    all_users.add(user_id)

    if is_rate_limited(user_id):
        await message.answer("⏳ **Слишком много запросов!** Подожди 60 секунд.", parse_mode="Markdown")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    total_requests_count += 1

    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        image_data = {
            "mime_type": "image/jpeg",
            "data": downloaded_file.read()
        }
        
        prompt = message.caption or "Проанализируй этот скриншот по Roblox (Blox Fruits / ABA / AUT) и дай разбор."
        
        full_prompt = get_context(user_id, prompt)
        response = await asyncio.to_thread(model.generate_content, [full_prompt, image_data])
        
        if response.text:
            answer = response.text[:4096]
            add_to_history(user_id, "user", prompt)
            add_to_history(user_id, "assistant", answer)
            
            try:
                await message.answer(answer, parse_mode="Markdown", reply_markup=get_quick_keyboard())
            except Exception:
                await message.answer(answer, reply_markup=get_quick_keyboard())
        else:
            await message.answer("❌ Не удалось распознать изображение.", reply_markup=get_quick_keyboard())
            
    except Exception as e:
        logging.error(f"Ошибка фото: {e}")
        await message.answer("⚠️ Не удалось прочитать картинку.", reply_markup=get_quick_keyboard())

# ==========================================
# ОСНОВНОЙ ТЕКСТОВЫЙ ХЕНДЛЕР
# ==========================================
@dp.message(F.text)
async def handle_user_message(message: types.Message):
    global total_requests_count
    user_id = message.from_user.id
    all_users.add(user_id)

    if is_rate_limited(user_id):
        await message.answer("⏳ **Слишком много запросов!** Подожди 60 секунд.", parse_mode="Markdown")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    total_requests_count += 1

    user_text = message.text
    add_to_history(user_id, "user", user_text)
    full_prompt = get_context(user_id, user_text)

    try:
        response = await asyncio.to_thread(model.generate_content, full_prompt)
        
        if response.text:
            answer = response.text[:4096]
            add_to_history(user_id, "assistant", answer)
            
            try:
                await message.answer(answer, parse_mode="Markdown", reply_markup=get_quick_keyboard())
            except Exception:
                await message.answer(answer, reply_markup=get_quick_keyboard())
        else:
            await message.answer("❌ Не удалось сгенерировать ответ.", reply_markup=get_quick_keyboard())
            
    except Exception as e:
        logging.error(f"Ошибка Gemini API: {e}")
        await message.answer("⚠️ Упсс, траблы с нейронкой. Попробуй позже.", reply_markup=get_quick_keyboard())

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    print("🤖 Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

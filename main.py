import asyncio
import os
import sys
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict
import re
import random
import hashlib
import json
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import google.generativeai as genai
from bs4 import BeautifulSoup
import requests
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==========================================
# КЭШИ И ГЛОБАЛЬНЫЕ ДАННЫЕ
# ==========================================
wiki_cache = {}
CACHE_EXPIRE = 3600
popular_answers_cache = {}
POPULAR_CACHE_EXPIRE = 1800
LEADERBOARD = defaultdict(int)
COMMAND_ALIASES = {}
PRIORITY_USERS = []
banned_users = set()
muted_users = {}
user_custom_limits = {}
last_update_check = None
update_cache = []
# ==========================================
# ЗАГРУЗКА ПРАВИЛ ИЗ ФАЙЛА prompt_rules.md
# ==========================================
def load_prompt_rules():
    """Загружает системный промпт из файла prompt_rules.md"""
    try:
        with open("prompt_rules.md", "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                return content
            else:
                logging.warning("⚠️ Файл prompt_rules.md пуст! Использую стандартный промпт.")
                return SYSTEM_PROMPT_DEFAULT
    except FileNotFoundError:
        logging.warning("⚠️ Файл prompt_rules.md не найден! Использую стандартный промпт.")
        return SYSTEM_PROMPT_DEFAULT
    except Exception as e:
        logging.error(f"❌ Ошибка при чтении prompt_rules.md: {e}")
        return SYSTEM_PROMPT_DEFAULT
# ==========================================
# HTTP-ЗАГЛУШКА ДЛЯ RENDER
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
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# ==========================================
# ЗАГРУЗКА .env И НАСТРОЙКА GEMINI
# ==========================================
load_dotenv()
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PRIORITY_USERS.append(ADMIN_ID)

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logging.error("❌ Не найдены TELEGRAM_TOKEN или GEMINI_API_KEY")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
[СМОТРИТЕ ТЕКСТОВЫЙ БЛОК НИЖЕ]
"""

generation_config = {
    "temperature": 0.0,
    "top_p": 0.1,
}

model = genai.GenerativeModel(
    model_name="gemini-3.1-flash-lite",
    system_instruction=SYSTEM_PROMPT,
    generation_config=generation_config
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# ==========================================
# ОСНОВНЫЕ ПЕРЕМЕННЫЕ
# ==========================================
user_chats = defaultdict(list)
user_requests = defaultdict(list)
all_users = set()
total_requests_count = 0
user_ratings = defaultdict(list)

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def add_to_history(user_id: int, role: str, content: str, max_len: int = 10):
    chat = user_chats[user_id]
    chat.append({"role": role, "content": content[:200]})
    if len(chat) > max_len:
        chat[:] = chat[-max_len:]

def get_context(user_id: int, question: str) -> str:
    chat = user_chats.get(user_id, [])
    if not chat:
        return question
    context = []
    for msg in chat[-6:]:
        prefix = "Пользователь" if msg["role"] == "user" else "Ассистент"
        context.append(f"{prefix}: {msg['content']}")
    return f"Контекст разговора:\n" + "\n".join(context) + f"\n\nВопрос: {question}"

def is_muted(user_id: int) -> bool:
    if user_id not in muted_users:
        return False
    if time.time() > muted_users[user_id]:
        del muted_users[user_id]
        return False
    return True

def is_rate_limited(user_id: int) -> bool:
    if user_id in banned_users:
        return True
    if is_muted(user_id):
        return True
    now = time.time()
    timestamps = user_requests[user_id]
    user_requests[user_id] = [t for t in timestamps if now - t < 60]
    limit = user_custom_limits.get(user_id, 10)
    if len(user_requests[user_id]) >= limit:
        return True
    user_requests[user_id].append(now)
    return False

def detect_command(text: str) -> str | None:
    text_lower = text.lower()
    if text_lower.startswith("/"):
        return text_lower.split()[0]
    for alias, command in COMMAND_ALIASES.items():
        if alias in text_lower:
            return command
    return None

def get_response_length(text: str) -> str:
    short_indicators = ["цена", "сколько", "кто", "где", "когда"]
    long_indicators = ["расскажи", "объясни", "подробно", "опиши", "как играть"]
    text_lower = text.lower()
    if any(word in text_lower for word in short_indicators):
        return "short"
    elif any(word in text_lower for word in long_indicators):
        return "long"
    else:
        return "medium"

# ==========================================
# УМНЫЙ ПОИСК ПО ВИКИ И TRELLO
# ==========================================
def search_all_wikis(query: str) -> list:
    results = []
    sources = [
        {"name": "Blox Fruits Wiki", "url": f"https://blox-fruits.fandom.com/wiki/{query.replace(' ', '_')}"},
        {"name": "Blox Fruits Wiki (RU)", "url": f"https://blox-fruits.fandom.com/ru/wiki/{query.replace(' ', '_')}"},
        {"name": "ABA Wiki", "url": f"https://roblox-anime-battle-arena.fandom.com/wiki/{query.replace(' ', '_')}"},
        {"name": "ABA Wiki (RU)", "url": f"https://roblox-anime-battle-arena.fandom.com/ru/wiki/{query.replace(' ', '_')}"},
        {"name": "AUT Wiki", "url": f"https://a-universal-time.fandom.com/wiki/{query.replace(' ', '_')}"},
        {"name": "AUT Wiki (RU)", "url": f"https://a-universal-time.fandom.com/ru/wiki/{query.replace(' ', '_')}"},
        {"name": "ABA Trello", "url": "https://trello.com/b/QBw7fnXX/black-magic-aba"},
        {"name": "AUT Trello", "url": "https://trello.com/b/XbM1pdjU/a-universal-time-aut"}
    ]
    for source in sources:
        try:
            response = requests.get(source["url"], timeout=5)
            if response.status_code == 200:
                results.append({"name": source["name"], "url": source["url"], "status": "found"})
            else:
                results.append({"name": source["name"], "url": source["url"], "status": "not_found"})
        except:
            results.append({"name": source["name"], "url": source["url"], "status": "error"})
    return results

# ==========================================
# ФОНОВЫЙ ПАРСИНГ ОБНОВЛЕНИЙ
# ==========================================
async def check_updates():
    global last_update_check, update_cache
    logging.info("🔄 Проверка обновлений игр...")
    games = {
        "Blox Fruits": "https://blox-fruits.fandom.com/wiki/Blox_Fruits_Wiki",
        "ABA": "https://roblox-anime-battle-arena.fandom.com/wiki/Anime_Battle_Arena_Wiki",
        "AUT": "https://a-universal-time.fandom.com/wiki/A_Universal_Time_Wiki"
    }
    new_updates = []
    for game_name, url in games.items():
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                recent_changes = soup.find_all("div", class_="recent-changes")
                if recent_changes:
                    for change in recent_changes[:3]:
                        text = change.get_text(strip=True)
                        if text:
                            new_updates.append({
                                "game": game_name,
                                "text": text[:100],
                                "source": url
                            })
        except Exception as e:
            logging.error(f"Ошибка парсинга {game_name}: {e}")
    if new_updates:
        update_cache = new_updates
        last_update_check = datetime.now()
        if new_updates and ADMIN_ID:
            msg = "📢 **НОВЫЕ ОБНОВЛЕНИЯ В ИГРАХ!**\n\n"
            for update in new_updates[:5]:
                msg += f"• **{update['game']}:** {update['text']}\n"
            msg += f"\n📖 [Подробнее на вики]({update['source']})"
            try:
                await bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")
            except:
                pass
    last_update_check = datetime.now()
    logging.info("✅ Проверка обновлений завершена")

# ==========================================
# КЭШИРОВАНИЕ ОТВЕТОВ
# ==========================================
def get_cached_answer(question: str) -> str | None:
    cache_key = hashlib.md5(question.lower().strip().encode()).hexdigest()
    if cache_key in popular_answers_cache:
        answer, timestamp = popular_answers_cache[cache_key]
        if time.time() - timestamp < POPULAR_CACHE_EXPIRE:
            return answer
        else:
            del popular_answers_cache[cache_key]
    return None

def cache_answer(question: str, answer: str):
    cache_key = hashlib.md5(question.lower().strip().encode()).hexdigest()
    popular_answers_cache[cache_key] = (answer, time.time())

def fetch_wiki_page_cached(url: str) -> str:
    cache_key = hashlib.md5(url.encode()).hexdigest()
    if cache_key in wiki_cache:
        cached_data, timestamp = wiki_cache[cache_key]
        if time.time() - timestamp < CACHE_EXPIRE:
            return cached_data
    result = fetch_wiki_page(url)
    wiki_cache[cache_key] = (result, time.time())
    return result

# ==========================================
# ДАННЫЕ ДЛЯ КНОПОК И ИГР
# ==========================================
FRUITS_DATA = [
    ("Леопард 🐆", "3.2M", "Физический"),
    ("Дракон 🐉", "2.5M", "Физический"),
    ("Китсун 🦊", "2.1M", "Физический"),
    ("Веном 🧪", "1.8M", "Природный"),
    ("Дух 👻", "1.5M", "Природный"),
    ("Феникс 🔥", "1.2M", "Природный"),
    ("Будда 🪷", "1.0M", "Природный"),
]

FACTS = [
    "🐆 Леопард — самый дорогой фрукт в Blox Fruits!",
    "👀 В ABA персонаж Годжо имеет 2 формы пробуждения!",
    "⚡ В AUT 12 стендов скрыты за квестами!",
    "🎯 Золотая эссенция в Blox Fruits даёт +20% к велью!",
    "🤯 В ABA комбо на Годжо может держать врага в стане 10 секунд!",
    "💀 В AUT стенд 'Стар Платина' считается самым сильным в мете!",
]

QUIZ_QUESTIONS = [
    {"question": "Сколько стоит Леопард в Blox Fruits?", "options": ["$2.5M", "$3.2M", "$4M", "$1.8M"], "correct": 1},
    {"question": "У кого в ABA есть пробуждение 'Бесконечность'?", "options": ["Мадара", "Годжо", "Наруто", "Ичиго"], "correct": 1},
    {"question": "Какой стенд в AUT считается самым редким?", "options": ["Star Platinum", "Golden Experience", "D4C", "Made in Heaven"], "correct": 2},
    {"question": "Какой фрукт в Blox Fruits стоит $2.5M?", "options": ["Дракон", "Леопард", "Китсун", "Веном"], "correct": 0},
]

GUESS_GAME_ITEMS = {
    "blox_fruits": [
        {"name": "Леопард", "hint": "Самый дорогой фрукт в игре 🐆"},
        {"name": "Дракон", "hint": "Огнедышащий фрукт 🐉"},
        {"name": "Китсун", "hint": "Девятихвостый фрукт 🦊"},
        {"name": "Веном", "hint": "Ядовитый фрукт 🧪"},
        {"name": "Дух", "hint": "Призрачный фрукт 👻"},
        {"name": "Феникс", "hint": "Возрождающийся фрукт 🔥"},
        {"name": "Будда", "hint": "Медитативный фрукт 🪷"},
    ],
    "aba": [
        {"name": "Годжо", "hint": "Персонаж с безграничностью 👁️"},
        {"name": "Мадара", "hint": "Легендарный шиноби 🍥"},
        {"name": "Наруто", "hint": "Главный герой с лисой 🍥"},
        {"name": "Саске", "hint": "Мститель с шаринганом 🍥"},
        {"name": "Ичиго", "hint": "Заместитель шинигами ⚔️"},
    ],
    "aut": [
        {"name": "Star Platinum", "hint": "Стенд с огромной силой 💪"},
        {"name": "Golden Experience", "hint": "Стенд с жизненной энергией 🌟"},
        {"name": "D4C", "hint": "Стенд с параллельными мирами 🌍"},
        {"name": "Made in Heaven", "hint": "Стенд с ускорением времени ⏳"},
    ]
}

MEMES = [
    "🤡 Когда сказали, что Леопард стоит $3.2M, а у тебя только $100K",
    "😤 Когда в ABA попал на Годжо в Awakening",
    "💀 AUT: когда выбил стенд, а он оказался D-tier",
    "🎮 Blox Fruits: фармишь 3 часа и выпадает Камень вместо фрукта",
    "🤣 Когда в ABA использовал ульту, а она улетела в стену",
    "😭 Когда продал Леопарда за $2M, а он подорожал до $3.2M",
    "🔥 Когда в AUT получил стенд S-тира с первого раза",
    "💀 Когда в Blox Fruits потратил все деньги на фрукт и он оказался не тем",
]

ROASTS = [
    "Ты настолько слаб, что даже камень в Blox Fruits бьёт сильнее тебя! 😂",
    "Твой скилл в ABA — как у босса на 1 уровне 🤡",
    "Ты фармишь дольше, чем фулл-билд качается 💀",
    "Твой главный фрукт — Камень. И это не шутка 😭",
    "В AUT ты так долго стоишь, что стенд думает, что ты сдался 💀",
    "Твой персонаж в ABA выглядит как новичок, но ты уже 100 уровень 🤡",
    "Твой билд в Blox Fruits такой же бесполезный, как камень в бою 😂",
]

QUOTES = [
    "«Самая сильная техника — это та, которую враг не видит» — Годжо",
    "«Удача — это навык» — Наруто",
    "«Побеждает не тот, кто сильнее, а тот, кто умнее» — Мадара",
    "«Стенд — это отражение души» — Джотаро",
    "«Фарм — это путь к величию» — Легенда Roblox",
    "«В PvP побеждает не тот, кто быстрее, а тот, кто хитрее» — Мастер ABA",
    "«Каждый фрукт может стать легендарным, если знать как его использовать» — Гуру Blox Fruits",
]

CHALLENGES = [
    "🔥 Победи босса за 2 минуты без фрукта!",
    "⚔️ Пройди рейд на сложности «Хардкор»!",
    "🎯 Выбей редкий фрукт за 30 минут!",
    "💀 Убей 100 мобов без смерти!",
    "🏆 Выиграй 3 PvP-боя подряд!",
    "👑 Достигни максимального уровня за 1 день!",
    "🎲 Собери 3 редких предмета за час!",
]

BUILDS = [
    "⚔️ **Билд:** Леопард + Тридент + Рыба-меч\n📊 **Для:** PvP\n💡 **Совет:** Используй [Z] для стана, [C] для дамага",
    "🪷 **Билд:** Будда + Палка + Электрический стиль\n📊 **Для:** Фарм\n💡 **Совет:** [Z] для сбора мобов, [X] для взрыва",
    "🐉 **Билд:** Дракон + Когти дракона + Огненный стиль\n📊 **Для:** PvP\n💡 **Совет:** Awakening даёт огромный урон",
    "👻 **Билд:** Дух + Томагавк + Теневой стиль\n📊 **Для:** PvP\n💡 **Совет:** Используй [C] для контроля",
    "🧪 **Билд:** Веном + Когти + Змеиный стиль\n📊 **Для:** PvP\n💡 **Совет:** [X] для отравления, [Z] для добивания",
    "🔥 **Билд:** Феникс + Меч + Огненный стиль\n📊 **Для:** Фарм/PvP\n💡 **Совет:** [C] для лечения, [V] для взрыва",
]

MOODS = {
    "добрый": "Отвечаю ласково и с эмодзи 😊",
    "злой": "Отвечаю агрессивно и саркастично 😠",
    "сарказм": "Отвечаю с иронией 🙄",
    "обычный": "Стандартный режим 🤖"
}

# ==========================================
# ПОИСК В ВИКИ
# ==========================================
def fetch_wiki_page(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
            " like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return f"Ошибка: не удалось открыть страницу (код {response.status_code})"
        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "aside", ".page-header"]):
            element.decompose()
        content_div = soup.find("div", {"class": "mw-parser-output"}) or soup.find("main")
        if content_div:
            text = content_div.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)
        return text[:10000]
    except Exception as e:
        return f"Ошибка при запросе к сайту: {str(e)}"

def search_wiki_for_query(query: str) -> tuple[str | None, str | None]:
    sources = {
        "blox_fruits": {
            "name": "Blox Fruits Wiki",
            "urls": [
                "https://blox-fruits.fandom.com/wiki/",
                "https://blox-fruits.fandom.com/ru/wiki/"
            ]
        },
        "aba": {
            "name": "ABA Wiki",
            "urls": [
                "https://roblox-anime-battle-arena.fandom.com/wiki/",
                "https://roblox-anime-battle-arena.fandom.com/ru/wiki/"
            ]
        },
        "aut": {
            "name": "AUT Wiki",
            "urls": [
                "https://a-universal-time.fandom.com/wiki/",
                "https://a-universal-time.fandom.com/ru/wiki/"
            ]
        }
    }
    trello_sources = {
        "aba": {"name": "ABA Trello (Black Magic)", "url": "https://trello.com/b/QBw7fnXX/black-magic-aba"},
        "aut": {"name": "AUT Trello (Std Dev)", "url": "https://trello.com/b/XbM1pdjU/a-universal-time-aut"}
    }
    
    query_lower = query.lower()
    game = None
    if "blox fruit" in query_lower or "фрукт" in query_lower:
        game = "blox_fruits"
    elif "aba" in query_lower or "anime battle" in query_lower:
        game = "aba"
    elif "aut" in query_lower or "universal time" in query_lower:
        game = "aut"
    else:
        keywords = ["блакс", "блокс", "фрукт", "леопард", "дракон", "китсун", "вена"]
        for kw in keywords:
            if kw in query_lower:
                game = "blox_fruits"
                break
        if not game:
            keywords = ["годжо", "мадара", "наруто", "саске", "ичиго", "айзен"]
            for kw in keywords:
                if kw in query_lower:
                    game = "aba"
                    break
        if not game:
            keywords = ["стенд", "спека", "aut", "universal"]
            for kw in keywords:
                if kw in query_lower:
                    game = "aut"
                    break
    if not game:
        return None, None
    
    if game in trello_sources:
        roadmap_keywords = ["обновлени", "патч", "роадмап", "будущ", "план", "трелло", "trello", "тизер"]
        if any(kw in query_lower for kw in roadmap_keywords):
            return trello_sources[game]["url"], trello_sources[game]["name"]
    
    base_urls = sources[game]["urls"]
    source_name = sources[game]["name"]
    keywords = re.findall(r'\b\w+\b', query_lower)
    stop_words = ['как', 'что', 'это', 'для', 'без', 'через', 'на', 'с', 'по', 'из', 'от', 'где', 'когда', 'почему']
    keywords = [kw for kw in keywords if kw not in stop_words and len(kw) > 2]
    
    for base_url in base_urls:
        for keyword in keywords[:3]:
            for variant in [keyword.capitalize(), keyword.title(), keyword.upper(), keyword.lower()]:
                potential_url = base_url + variant
                try:
                    response = requests.get(potential_url, timeout=5)
                    if response.status_code == 200:
                        return potential_url, source_name
                except:
                    pass
    return sources[game]["urls"][0], source_name

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
    builder.button(text="🎲 Случайная фича", callback_data="action_random")
    builder.button(text="🧹 Очистить чат", callback_data="action_clear")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()

def get_rating_keyboard():
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.button(text=f"⭐{i}", callback_data=f"rate_{i}")
    builder.adjust(5)
    return builder.as_markup()

def get_leaderboard_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🏆 Таблица лидеров", callback_data="leaderboard")
    return builder.as_markup()

# ==========================================
# ОБРАБОТЧИКИ СОБЫТИЙ
# ==========================================
@dp.callback_query(F.data.startswith("rate_"))
async def handle_rating(callback: types.CallbackQuery):
    rating = int(callback.data.replace("rate_", ""))
    user_ratings[callback.from_user.id].append(rating)
    messages = {
        1: "😔 Спасибо за честность! Постараюсь стать лучше!",
        2: "😊 Спасибо! Исправлю ошибки!",
        3: "👍 Спасибо! Буду стараться!",
        4: "🌟 Спасибо! Рад что помог!",
        5: "🔥 Спасибо! Ты лучший!"
    }
    await callback.answer(messages.get(rating, "Спасибо за оценку!"), show_alert=True)

@dp.callback_query(F.data.startswith("action_"))
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if is_rate_limited(user_id):
        await callback.answer("⏳ Слишком много запросов! Подожди 60 секунд.", show_alert=True)
        return
    if user_id in banned_users:
        await callback.answer("🚫 Вы забанены!", show_alert=True)
        return
    if is_muted(user_id):
        remain = int(muted_users[user_id] - time.time())
        await callback.answer(f"🔇 Вы замучены! Осталось {remain} сек.", show_alert=True)
        return
    
    if callback.data == "action_clear":
        if user_id in user_chats:
            del user_chats[user_id]
        await callback.answer("🧹 Чат очищен!")
        await callback.message.delete()
        await callback.message.answer("🧹 **Контекст очищен!**", parse_mode="Markdown", reply_markup=get_quick_keyboard())
        return
    
    if callback.data == "action_random":
        await callback.answer("🎲 Кидаю случайную фичу...")
        random_commands = ["/fruit", "/fact", "/quiz", "/roll", "/how", "/meme", "/quote", "/challenge"]
        await callback.message.answer(f"🎲 **Случайная фича:** {random.choice(random_commands)}\n\nПопробуй команду, чтобы узнать что-то новое!")
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

@dp.callback_query(F.data == "leaderboard")
async def show_leaderboard(callback: types.CallbackQuery):
    if not LEADERBOARD:
        await callback.answer("📊 Таблица лидеров пока пуста!", show_alert=True)
        return
    sorted_players = sorted(LEADERBOARD.items(), key=lambda x: x[1], reverse=True)[:10]
    leaderboard_text = "🏆 **ТАБЛИЦА ЛИДЕРОВ**\n\n"
    for i, (user_id, score) in enumerate(sorted_players, 1):
        try:
            user = await bot.get_chat(user_id)
            name = user.first_name or f"User_{user_id}"
        except:
            name = f"User_{user_id}"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        leaderboard_text += f"{medal} **{name}** — {score} очков\n"
    await callback.message.answer(leaderboard_text, parse_mode="Markdown")

# ==========================================
# ОСНОВНЫЕ КОМАНДЫ
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    all_users.add(message.from_user.id)
    bonus_text = ""
    if random.random() < 0.3:
        fruit, price, fruit_type = random.choice(FRUITS_DATA)
        bonus_text = f"\n\n🎁 **Бонус!** Ты получил случайный фрукт!\n🍎 {fruit} — {price}"
    await message.answer(
        f"🎮 **Эксперт по Blox Fruits, ABA и AUT!**\n\n"
        f"Задавай вопросы по трейдам, комбухам или прокачке!\n"
        f"Используй кнопки для быстрых запросов 👇\n\n"
        f"📚 **Источники информации:**\n"
        f"• Blox Fruits Wiki\n"
        f"• ABA Wiki\n"
        f"• AUT Wiki\n"
        f"• ABA Trello (Black Magic)\n"
        f"• AUT Trello (Std Dev){bonus_text}\n\n"
        f"🎯 **Новые фичи:**\n"
        f"• /guess — Угадай фрукт/стенд (викторина с опросом)\n"
        f"• /updates — Последние обновления игр\n"
        f"• /leaderboard — Таблица лидеров",
        parse_mode="Markdown",
        reply_markup=get_quick_keyboard()
    )

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    start_time = time.time()
    msg = await message.answer("🏓 Понг! Измеряю задержку...")
    ping = round((time.time() - start_time) * 1000)
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
        "🎮 **Основные:**\n"
        "/start — Приветствие с кнопками\n"
        "/ping — Проверить скорость\n"
        "/clear — Очистить историю\n\n"
        "🎲 **Развлечения:**\n"
        "/fruit — Фрукт дня\n"
        "/fact — Случайный факт\n"
        "/quiz — Викторина\n"
        "/roll — Бросок кубика (1-100)\n"
        "/how — Предсказание судьбы\n"
        "/mood — Сменить настроение бота\n"
        "/meme — Мем дня\n"
        "/roast — Подкол дня\n"
        "/luck — Проверка удачи\n"
        "/coin — Подбросить монетку\n"
        "/dice — Бросок двух кубиков\n"
        "/quote — Цитата дня\n"
        "/challenge — Божественный вызов\n"
        "/rps — Камень-ножницы-бумага\n"
        "/compliment — Комплимент от бота\n"
        "/build — Рекомендация билда\n"
        "/guess — Угадай фрукт/стенд (интерактивный опрос)\n\n"
        "📊 **Информационные:**\n"
        "/updates — Последние обновления игр\n"
        "/leaderboard — Таблица лидеров викторин\n\n"
        "⚙️ **Админ:**\n"
        "/stats — Статистика\n"
        "/broadcast — Рассылка\n"
        "/admin — Помощь админа\n\n"
        "💡 Используй кнопки под сообщениями!",
        parse_mode="Markdown"
    )

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_chats:
        del user_chats[user_id]
    await message.answer("🧹 **История диалога очищена!**", parse_mode="Markdown", reply_markup=get_quick_keyboard())

@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: types.Message):
    if not LEADERBOARD:
        await message.answer("📊 Таблица лидеров пока пуста!\n\nНачни играть в /guess, чтобы заработать очки!", parse_mode="Markdown")
        return
    sorted_players = sorted(LEADERBOARD.items(), key=lambda x: x[1], reverse=True)[:10]
    leaderboard_text = "🏆 **ТАБЛИЦА ЛИДЕРОВ**\n\n"
    for i, (user_id, score) in enumerate(sorted_players, 1):
        try:
            user = await bot.get_chat(user_id)
            name = user.first_name or f"User_{user_id}"
        except:
            name = f"User_{user_id}"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        leaderboard_text += f"{medal} **{name}** — {score} очков\n"
    await message.answer(leaderboard_text, parse_mode="Markdown")

# ==========================================
# РАЗВЛЕКАТЕЛЬНЫЕ КОМАНДЫ
# ==========================================
@dp.message(Command("fruit"))
async def cmd_fruit(message: types.Message):
    fruit, price, fruit_type = random.choice(FRUITS_DATA)
    await message.answer(
        f"🍎 **Фрукт дня:** {fruit}\n\n"
        f"💰 **Цена:** `{price}`\n"
        f"📦 **Тип:** {fruit_type}\n"
        f"💡 *Этот фрукт {random.choice(['сейчас в топе', 'имеет высокий спрос', 'отлично подходит для PvP', 'хорош для фарма'])}!*",
        parse_mode="Markdown"
    )

@dp.message(Command("fact"))
async def cmd_fact(message: types.Message):
    await message.answer(f"💡 **Случайный факт:**\n\n{random.choice(FACTS)}", parse_mode="Markdown")

@dp.message(Command("quiz"))
async def cmd_quiz(message: types.Message):
    quiz = random.choice(QUIZ_QUESTIONS)
    options_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(quiz["options"])])
    await message.answer(
        f"❓ **Викторина по Roblox:**\n\n"
        f"{quiz['question']}\n\n"
        f"{options_text}\n\n"
        f"💡 *Ответь числом (1-{len(quiz['options'])}) в следующем сообщении!*",
        parse_mode="Markdown"
    )

@dp.message(Command("roll"))
async def cmd_roll(message: types.Message):
    number = random.randint(1, 100)
    emoji = "🎲" if number < 50 else "🎯" if number < 80 else "🌟"
    await message.answer(
        f"{emoji} **Твой бросок:** `{number}/100`\n\n"
        f"{'😎 Отличный результат!' if number > 80 else '👍 Неплохо!' if number > 50 else '😅 В следующий раз повезёт!'}",
        parse_mode="Markdown"
    )

@dp.message(Command("how"))
async def cmd_how(message: types.Message):
    predictions = [
        "🌈 Сегодня твой день! Иди качать фрукты!",
        "⚠️ Осторожно на PvP-арене! Враги затаились!",
        "🔥 Отличный день для трейдов! Ты сделаешь выгодную сделку!",
        "💀 Лучше пофармить сегодня... Завтра будет сложнее.",
        "✨ Удача на твоей стороне! Используй это!",
        "🤔 Возможно, стоит сменить билд... Подумай над этим.",
        "🏆 Ты станешь легендой! Просто продолжай играть!"
    ]
    await message.answer(f"🔮 **Предсказание судьбы:**\n\n{random.choice(predictions)}", parse_mode="Markdown")

@dp.message(Command("mood"))
async def cmd_mood(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            f"🎭 **Текущий режим:** `обычный`\n\n"
            f"Доступные режимы:\n" +
            "\n".join([f"• `{mood}` — {desc}" for mood, desc in MOODS.items()]) +
            f"\n\n💡 Используй: `/mood добрый`",
            parse_mode="Markdown"
        )
        return
    mood = parts[1].lower()
    if mood not in MOODS:
        await message.answer(f"❌ Такого режима нет! Доступны: {', '.join(MOODS.keys())}", parse_mode="Markdown")
        return
    global SYSTEM_PROMPT
    SYSTEM_PROMPT = SYSTEM_PROMPT + f"\n\n🎭 **РЕЖИМ ОТВЕТОВ:** {MOODS[mood]}"
    await message.answer(f"✅ **Режим сменён на:** `{mood}`\n\n{MOODS[mood]}", parse_mode="Markdown")

@dp.message(Command("meme"))
async def cmd_meme(message: types.Message):
    await message.answer(f"😂 **Мем дня:**\n\n{random.choice(MEMES)}", parse_mode="Markdown")

@dp.message(Command("roast"))
async def cmd_roast(message: types.Message):
    await message.answer(f"🔥 **Подкол дня:**\n\n{random.choice(ROASTS)}", parse_mode="Markdown")

@dp.message(Command("luck"))
async def cmd_luck(message: types.Message):
    luck = random.randint(0, 100)
    emoji = "🔥" if luck > 70 else "😅" if luck > 40 else "💀"
    text = "Можешь идти на PvP! 🎯" if luck > 70 else "Сегодня лучше пофармить... 📊" if luck > 40 else "Сиди дома... 🏠"
    await message.answer(
        f"🍀 **Твоя удача сегодня:** `{luck}%`\n\n{emoji} {text}",
        parse_mode="Markdown"
    )

@dp.message(Command("coin"))
async def cmd_coin(message: types.Message):
    await message.answer(f"🪙 **Монетка упала:** {random.choice(['Орёл 🦅', 'Решка 🪙'])}", parse_mode="Markdown")

@dp.message(Command("dice"))
async def cmd_dice(message: types.Message):
    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    await message.answer(
        f"🎲 **Бросок двух кубиков:**\n\n"
        f"1-й кубик: `{d1}`\n"
        f"2-й кубик: `{d2}`\n"
        f"Сумма: `{d1 + d2}`",
        parse_mode="Markdown"
    )

@dp.message(Command("quote"))
async def cmd_quote(message: types.Message):
    await message.answer(f"💬 **Цитата дня:**\n\n{random.choice(QUOTES)}", parse_mode="Markdown")

@dp.message(Command("challenge"))
async def cmd_challenge(message: types.Message):
    await message.answer(f"⚔️ **БОЖЕСТВЕННЫЙ ВЫЗОВ:**\n\n{random.choice(CHALLENGES)}", parse_mode="Markdown")

@dp.message(Command("rps"))
async def cmd_rps(message: types.Message):
    choices = ["камень", "ножницы", "бумага"]
    user_choice = message.text.replace("/rps", "").strip().lower()
    if not user_choice or user_choice not in choices:
        await message.answer("❓ Используй: `/rps камень` или `/rps ножницы` или `/rps бумага`", parse_mode="Markdown")
        return
    bot_choice = random.choice(choices)
    if user_choice == bot_choice:
        result = "🤝 Ничья!"
    elif (user_choice == "камень" and bot_choice == "ножницы") or (user_choice == "ножницы" and bot_choice == "бумага") or (user_choice == "бумага" and bot_choice == "камень"):
        result = "🎉 Ты победил!"
    else:
        result = "😔 Бот победил!"
    await message.answer(
        f"🤖 Бот: `{bot_choice}`\n"
        f"👤 Ты: `{user_choice}`\n\n"
        f"**{result}**",
        parse_mode="Markdown"
    )

@dp.message(Command("compliment"))
async def cmd_compliment(message: types.Message):
    compliments = [
        "Ты просто космос! 🌟",
        "С тобой приятно общаться! 😊",
        "Ты настоящий геймер-легенда! 🎮",
        "Твой скилл впечатляет! 🔥",
        "Ты лучший собеседник! 💎",
        "У тебя отличный вкус в играх! 👌",
        "Ты на голову выше остальных! 🚀"
    ]
    await message.answer(f"💖 **Комплимент:**\n\n{random.choice(compliments)}", parse_mode="Markdown")

@dp.message(Command("build"))
async def cmd_build(message: types.Message):
    await message.answer(random.choice(BUILDS), parse_mode="Markdown")

@dp.message(Command("guess"))
async def cmd_guess(message: types.Message):
    categories = ["blox_fruits", "aba", "aut"]
    category = random.choice(categories)
    category_names = {
        "blox_fruits": "🍎 Blox Fruits",
        "aba": "⚔️ ABA Персонажи",
        "aut": "🌟 AUT Стенды"
    }
    items = GUESS_GAME_ITEMS[category]
    item = random.choice(items)
    options = [item["name"]]
    other_items = [i for i in items if i["name"] != item["name"]]
    random.shuffle(other_items)
    options.extend([i["name"] for i in other_items[:3]])
    random.shuffle(options)
    question_text = f"🔍 **Угадай, что это за {category_names[category]}?**\n\n💡 Подсказка: {item['hint']}"
    try:
        await bot.send_poll(
            chat_id=message.chat.id,
            question=question_text,
            options=options,
            type="quiz",
            correct_option_id=options.index(item["name"]),
            is_anonymous=False,
            explanation=f"✅ Правильный ответ: **{item['name']}**\n\n🏆 Ты получаешь +1 очко в таблицу лидеров!",
            explanation_parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка создания опроса: {e}")
        await message.answer("❌ Не удалось создать викторину. Попробуй позже.", reply_markup=get_quick_keyboard())

@dp.message(Command("updates"))
async def cmd_updates(message: types.Message):
    if not update_cache:
        await message.answer("📡 Проверяю обновления...")
        await check_updates()
    if not update_cache:
        await message.answer("📡 Обновлений не найдено.", parse_mode="Markdown")
        return
    response = "📢 **ПОСЛЕДНИЕ ОБНОВЛЕНИЯ В ИГРАХ**\n\n"
    for update in update_cache[:10]:
        response += f"• **{update['game']}:** {update['text']}\n"
    response += f"\n📖 [Подробнее на вики]({update_cache[0]['source']})"
    await message.answer(response, parse_mode="Markdown")

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
        "• `/restart` — Перезапуск бота\n"
        "• `/admin_ban ID` — Забанить пользователя\n"
        "• `/admin_unban ID` — Разбанить пользователя\n"
        "• `/admin_list_banned` — Список забаненных\n"
        "• `/admin_clear_user ID` — Очистить историю пользователя\n"
        "• `/admin_set_limit ID 5` — Установить лимит запросов (1-60)\n"
        "• `/admin_get_user ID` — Информация о пользователе\n"
        "• `/admin_logs` — Последние логи\n"
        "• `/admin_top_users` — Топ активных пользователей\n"
        "• `/admin_mute ID 10` — Замутить пользователя на N минут\n"
        "• `/admin_export` — Экспорт статистики в файл\n"
        "• `/admin_check_updates` — Принудительная проверка обновлений",
        parse_mode="Markdown"
    )

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора!")
        return
    avg_rating = 0
    if user_ratings:
        all_ratings = [r for ratings in user_ratings.values() for r in ratings]
        avg_rating = sum(all_ratings) / len(all_ratings) if all_ratings else 0
    leaderboard_size = len(LEADERBOARD)
    await message.answer(
        f"📊 **СТАТИСТИКА БОТА**\n\n"
        f"👥 Уникальных пользователей: `{len(all_users)}`\n"
        f"💬 Всего обработано запросов: `{total_requests_count}`\n"
        f"🧠 Активных чатов в памяти: `{len(user_chats)}`\n"
        f"⭐ Средняя оценка: `{avg_rating:.1f}/5`\n"
        f"🚫 Забанено: `{len(banned_users)}`\n"
        f"🔇 Замучено: `{len(muted_users)}`\n"
        f"🏆 Участников лидерборда: `{leaderboard_size}`\n"
        f"📦 Размер кэша вики: `{len(wiki_cache)}` страниц\n"
        f"⏳ Размер кэша популярных ответов: `{len(popular_answers_cache)}` вопросов",
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
    success, failed = 0, 0
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

@dp.message(Command("admin_check_updates"))
async def admin_check_updates(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🔄 Запускаю принудительную проверку обновлений...")
    await check_updates()
    if update_cache:
        await message.answer(f"✅ Найдено {len(update_cache)} обновлений! Используй /updates, чтобы посмотреть.")
    else:
        await message.answer("📡 Обновлений не найдено.")

# ==========================================
# АДМИН-КОМАНДЫ (БАН, МУТ И Т.Д.)
# ==========================================
@dp.message(Command("admin_ban"))
async def admin_ban(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Используй: `/admin_ban ID`", parse_mode="Markdown")
        return
    try:
        user_id = int(parts[1])
        banned_users.add(user_id)
        if user_id in user_chats:
            del user_chats[user_id]
        if user_id in muted_users:
            del muted_users[user_id]
        await message.answer(f"🚫 Пользователь `{user_id}` забанен!", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ ID должен быть числом!", parse_mode="Markdown")

@dp.message(Command("admin_unban"))
async def admin_unban(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Используй: `/admin_unban ID`", parse_mode="Markdown")
        return
    try:
        user_id = int(parts[1])
        if user_id in banned_users:
            banned_users.remove(user_id)
            await message.answer(f"✅ Пользователь `{user_id}` разбанен!", parse_mode="Markdown")
        else:
            await message.answer(f"⚠️ Пользователь `{user_id}` не был в бане.", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ ID должен быть числом!", parse_mode="Markdown")

@dp.message(Command("admin_list_banned"))
async def admin_list_banned(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    if not banned_users:
        await message.answer("📋 Список забаненных пуст.", parse_mode="Markdown")
        return
    await message.answer(f"🚫 **ЗАБАНЕННЫЕ ПОЛЬЗОВАТЕЛИ:**\n\n" + "\n".join([f"• `{uid}`" for uid in banned_users]), parse_mode="Markdown")

@dp.message(Command("admin_clear_user"))
async def admin_clear_user(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Используй: `/admin_clear_user ID`", parse_mode="Markdown")
        return
    try:
        user_id = int(parts[1])
        if user_id in user_chats:
            del user_chats[user_id]
            await message.answer(f"✅ История пользователя `{user_id}` очищена!", parse_mode="Markdown")
        else:
            await message.answer(f"⚠️ У пользователя `{user_id}` нет сохранённой истории.", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ ID должен быть числом!", parse_mode="Markdown")

@dp.message(Command("admin_set_limit"))
async def admin_set_limit(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("❌ Используй: `/admin_set_limit ID 5`", parse_mode="Markdown")
        return
    try:
        user_id = int(parts[1])
        limit = int(parts[2])
        if limit < 1 or limit > 60:
            await message.answer("❌ Лимит должен быть от 1 до 60 запросов в минуту!", parse_mode="Markdown")
            return
        user_custom_limits[user_id] = limit
        await message.answer(f"✅ Для пользователя `{user_id}` установлен лимит `{limit}` запросов/мин!", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ ID и лимит должны быть числами!", parse_mode="Markdown")

@dp.message(Command("admin_get_user"))
async def admin_get_user(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Используй: `/admin_get_user ID`", parse_mode="Markdown")
        return
    try:
        user_id = int(parts[1])
        has_history = user_id in user_chats
        history_len = len(user_chats.get(user_id, [])) if has_history else 0
        is_banned = user_id in banned_users
        is_muted_flag = is_muted(user_id)
        custom_limit = user_custom_limits.get(user_id, "По умолчанию (10)")
        now = time.time()
        user_timestamps = user_requests.get(user_id, [])
        recent_requests = len([t for t in user_timestamps if now - t < 60])
        await message.answer(
            f"👤 **ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ**\n\n"
            f"• ID: `{user_id}`\n"
            f"• В истории: `{history_len}` сообщений\n"
            f"• Запросов за минуту: `{recent_requests}`\n"
            f"• Лимит: `{custom_limit}` запросов/мин\n"
            f"• Статус: {'🚫 Забанен' if is_banned else '🔇 Замучен' if is_muted_flag else '✅ Активен'}",
            parse_mode="Markdown"
        )
    except ValueError:
        await message.answer("❌ ID должен быть числом!", parse_mode="Markdown")

@dp.message(Command("admin_logs"))
async def admin_logs(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        log_file = "bot.log"
        if not os.path.exists(log_file):
            await message.answer("❌ Файл логов не найден.", parse_mode="Markdown")
            return
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            last_lines = lines[-50:] if len(lines) > 50 else lines
        log_text = "".join(last_lines)[:3500]
        await message.answer(f"📋 **ПОСЛЕДНИЕ ЛОГИ:**\n```\n{log_text}\n```", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка при чтении логов: {e}", parse_mode="Markdown")

@dp.message(Command("admin_top_users"))
async def admin_top_users(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    if not user_requests:
        await message.answer("📊 Нет данных о пользователях.", parse_mode="Markdown")
        return
    user_counts = {uid: len(reqs) for uid, reqs in user_requests.items()}
    sorted_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    top_text = "\n".join([f"{i+1}. `{uid}` — {count} запросов" for i, (uid, count) in enumerate(sorted_users)])
    await message.answer(f"🏆 **ТОП-10 АКТИВНЫХ ПОЛЬЗОВАТЕЛЕЙ**\n\n{top_text}", parse_mode="Markdown")

@dp.message(Command("admin_mute"))
async def admin_mute(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("❌ Используй: `/admin_mute ID 10` (минут)", parse_mode="Markdown")
        return
    try:
        user_id = int(parts[1])
        minutes = int(parts[2])
        if minutes < 1 or minutes > 1440:
            await message.answer("❌ Время мута должно быть от 1 до 1440 минут!", parse_mode="Markdown")
            return
        muted_users[user_id] = time.time() + (minutes * 60)
        if user_id in banned_users:
            banned_users.remove(user_id)
        await message.answer(f"🔇 Пользователь `{user_id}` замучен на {minutes} минут!", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ ID и время должны быть числами!", parse_mode="Markdown")

@dp.message(Command("admin_export"))
async def admin_export(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        data = {
            "users": list(all_users),
            "chats": dict(user_chats),
            "requests": dict(user_requests),
            "ratings": dict(user_ratings),
            "banned": list(banned_users),
            "muted": dict(muted_users),
            "custom_limits": dict(user_custom_limits),
            "leaderboard": dict(LEADERBOARD),
            "total_requests": total_requests_count,
            "export_time": datetime.now().isoformat()
        }
        filename = f"export_{int(time.time())}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(filename, "rb") as f:
            await message.answer_document(
                types.BufferedInputFile(f.read(), filename=filename),
                caption="📊 **Экспорт статистики бота**"
            )
        os.remove(filename)
    except Exception as e:
        await message.answer(f"❌ Ошибка при экспорте: {e}", parse_mode="Markdown")

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
        image_data = {"mime_type": "image/jpeg", "data": downloaded_file.read()}
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
# ОБРАБОТКА БОНУС-КНОПКИ
# ==========================================
@dp.callback_query(F.data == "bonus")
async def handle_bonus(callback: types.CallbackQuery):
    await callback.answer("🎁 Получаешь бонус!")
    bonus_type = random.choice(["fruit", "fact", "quiz", "roll", "meme", "quote"])
    if bonus_type == "fruit":
        fruit, price, fruit_type = random.choice(FRUITS_DATA)
        text = f"🍎 **Бонусный фрукт:** {fruit}\n💰 {price}"
    elif bonus_type == "fact":
        text = f"💡 **Бонусный факт:**\n{random.choice(FACTS)}"
    elif bonus_type == "quiz":
        quiz = random.choice(QUIZ_QUESTIONS)
        text = f"❓ **Бонусная викторина:**\n{quiz['question']}"
    elif bonus_type == "roll":
        text = f"🎲 **Бонусный бросок:** {random.randint(1, 100)}/100"
    elif bonus_type == "meme":
        text = f"😂 **Бонусный мем:**\n{random.choice(MEMES)}"
    else:
        text = f"💬 **Бонусная цитата:**\n{random.choice(QUOTES)}"
    await callback.message.answer(text, parse_mode="Markdown")
# ==========================================
# 🔥 ОСНОВНОЙ ТЕКСТОВЫЙ ХЕНДЛЕР (ИСПРАВЛЕННЫЙ)
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
    
    cmd = detect_command(user_text)
    if cmd and cmd != user_text:
        await message.answer(f"⚠️ Возможно, вы имели в виду команду: `{cmd}`")
    
    # Проверяем кэш популярных вопросов
    cached_answer = get_cached_answer(user_text)
    if cached_answer:
        # ✅ ОБРЕЗАЕМ ДО БЕЗОПАСНОЙ ДЛИНЫ
        safe_answer = cached_answer[:4000]
        try:
            await message.answer(safe_answer, parse_mode="Markdown", reply_markup=get_quick_keyboard())
        except Exception:
            await message.answer(safe_answer, reply_markup=get_quick_keyboard())
        return
    
    response_type = get_response_length(user_text)
    if response_type == "short":
        generation_config["max_output_tokens"] = 300
    elif response_type == "long":
        generation_config["max_output_tokens"] = 2000
    else:
        generation_config["max_output_tokens"] = 1000
    
    wiki_url, source_name = search_wiki_for_query(user_text)
    wiki_content = None
    if wiki_url:
        await message.answer(f"🔍 Проверяю данные в **{source_name}**...", parse_mode="Markdown")
        wiki_content = fetch_wiki_page_cached(wiki_url)
        if "Ошибка" in wiki_content or "не удалось" in wiki_content:
            wiki_content = None
    
    if wiki_content:
        full_prompt = f"📚 **Информация из официального источника ({source_name}):**\n{wiki_content}\n\n━━━━━━━━━━━━━━━━━━━━━━\n🎯 **Вопрос пользователя:**\n{user_text}\n\n⚠️ ВАЖНО: Отвечай строго на основе данных из этого источника. Если информации недостаточно — честно скажи об этом."
    else:
        full_prompt = get_context(user_id, user_text)
        if wiki_url is None:
            full_prompt = f"⚠️ Не удалось найти страницу в официальных источниках по этому запросу. Отвечай на основе своих знаний, но если не уверен — честно скажи.\n\n{full_prompt}"

    try:
        response = await asyncio.to_thread(model.generate_content, full_prompt)
        if response.text:
            # ✅ ОБРЕЗАЕМ ОТВЕТ ДО БЕЗОПАСНОЙ ДЛИНЫ
            answer = response.text[:4096]
            add_to_history(user_id, "assistant", answer)
            
            if len(user_text.split()) < 5:
                cache_answer(user_text, answer)
            
            if wiki_url and not ("Ошибка" in wiki_content or "не удалось" in wiki_content):
                answer += f"\n\n📖 **Источник:** [{source_name}]({wiki_url})"
            
            # ✅ ЗАЩИТА ОТ ОШИБОК MARKDOWN
            safe_answer = answer[:4000]  # Оставляем запас под ссылки
            
            try:
                await message.answer(safe_answer, parse_mode="Markdown", reply_markup=get_quick_keyboard())
                await message.answer("⭐ Оцени ответ:", reply_markup=get_rating_keyboard())
            except Exception as e:
                # Если Markdown не работает — отправляем без форматирования
                logging.warning(f"Markdown error: {e}")
                await message.answer(safe_answer, reply_markup=get_quick_keyboard())
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
    scheduler.add_job(check_updates, 'interval', hours=6)
    scheduler.start()
    asyncio.create_task(check_updates())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

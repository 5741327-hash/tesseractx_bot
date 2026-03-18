import os
import logging
import re
import random
import requests
from functools import wraps
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

from openai import OpenAI
from duckduckgo_search import DDGS

# --- 1. Инициализация и Логирование ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

client = OpenAI(api_key=OPENAI_API_KEY)
user_conversations = {}

# --- 2. Системный промпт (Экспертиза) ---
SYSTEM_PROMPT = (
    "Ты — ведущий эксперт по микробиологической фильтрации в фармацевтике. "
    "Твоя задача: анализировать препараты заказчика и предлагать технические решения.\n\n"
    "ТВОЙ АЛГОРИТМ:\n"
    "1. Выдели активные вещества (АФС) из текста или сайта.\n"
    "2. Учти вязкость (например, Гипромеллоза), pH и риск сорбции на мембранах.\n"
    "3. Используй данные научного поиска для обоснования выбора мембраны (PES, PVDF, PTFE).\n"
    "4. Предлагай каскадную фильтрацию (предфильтр + финишный 0.22 мкм).\n"
    "5. Делай расчеты площади по формуле A = Q / (Flux * t).\n\n"
    "Стиль: профессиональный, инженерный."
)

# --- 3. Функции поиска и парсинга ---
def search_scientific_insights(keywords):
    search_results = ""
    try:
        with DDGS() as ddgs:
            query = f"{keywords} pharmaceutical filtration membrane compatibility challenges"
            results = ddgs.text(query, max_results=3)
            for r in results:
                search_results += f"\n- {r['title']}: {r['body']}"
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
    return search_results

def parse_website(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=' ', strip=True)[:7000]
    except Exception as e:
        logger.error(f"Ошибка парсинга {url}: {e}")
        return None

# --- 4. Декоратор доступа ---
def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if str(update.effective_user.id) != ADMIN_ID:
            await update.message.reply_text("⛔️ Доступ запрещен.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- 5. Логика ИИ ---
async def handle_request(user_id, user_input, is_url=False):
    if user_id not in user_conversations:
        user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    data_source = parse_website(user_input) if is_url else user_input
    if is_url and not data_source:
        return "Не удалось прочитать сайт."

    # Извлечение КВ для поиска
    kw_res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"Extract API/ingredients names for research: {data_source[:2000]}"}]
    )
    keywords = kw_res.choices[0].message.content
    
    # Поиск в сети
    science = search_scientific_insights(keywords)

    final_input = f"CONTEXT: {data_source}\n\nSCIENTIFIC DATA: {science}\n\nProvide filtration advice."
    user_conversations[user_id].append({"role": "user", "content": final_input})

    try:
        response = client.chat.completions.create(model="gpt-4o", messages=user_conversations[user_id])
        answer = response.choices[0].message.content
        user_conversations[user_id].append({"role": "assistant", "content": answer})
        return answer
    except Exception as e:
        logger.error(f"OpenAI Error: {e}")
        return "Ошибка генерации ответа."

# --- 6. Обработчики ---
@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔬 Бот активен. Пришлите ссылку или текст для анализа. /wake — сброс контекста.")

@restricted
async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_conversations[update.effective_user.id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await update.message.reply_text("✨ Контекст очищен.")

@restricted
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    url_match = re.search(r'https?://[^\s]+', text)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if url_match:
        await update.message.reply_text("🔎 Изучаю сайт и провожу научный поиск...")
        answer = await handle_request(user_id, url_match.group(0), is_url=True)
    else:
        answer = await handle_request(user_id, text)

    await update.message.reply_text(answer)

# --- 7. Запуск (Webhook для Render) ---
def main():
    # 1. Инициализация приложения
    # Используем билд без автоматического запуска, чтобы настроить параметры
    app = Application.builder().token(TOKEN).build()
    
    # 2. Регистрация ваших обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("wake", wake))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    PORT = int(os.environ.get("PORT", 8080))
    
    if WEBHOOK_URL:
        logger.info(f"Запуск Webhook: {WEBHOOK_URL} на порту {PORT}")
        
        # КЛЮЧЕВОЙ МОМЕНТ: drop_pending_updates=True убирает конфликт
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
            drop_pending_updates=True 
        )
    else:
        logger.info("Запуск Polling (локальный режим)")
        app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()

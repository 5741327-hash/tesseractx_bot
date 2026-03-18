import os
import logging
import re
import requests
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from openai import OpenAI
from duckduckgo_search import DDGS

# --- Настройки логов ---
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Данные из Render
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 10000))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- ИНЖЕНЕРНЫЙ ПРОМПТ ---
SYSTEM_PROMPT = (
    "Ты — Технический Аудитор фармпроизводства. Твоя задача: найти АФС и вспомогательные вещества.\n"
    "1. СПИСОК ПРОДУКЦИИ: Извлеки названия всех препаратов.\n"
    "2. ПОИСК СОСТАВА (АФС): Для КАЖДОГО препарата найди активное вещество и концентрацию полимеров.\n"
    "3. ХИМИЧЕСКИЙ АНАЛИЗ: Определи динамическую вязкость (cP) и молекулярный вес АФС.\n"
    "4. ТЕХНИЧЕСКОЕ РЕШЕНИЕ: Предложи материал мембраны (PES/PVDF/Nylon) и размер пор (0.22/0.45/0.1).\n"
    "5. РАСЧЕТ: Рассчитай площадь фильтрации для 100л/2ч. Используй A = V / (J * t)."
)

# --- Функции парсинга и поиска ---
def get_deep_info(query):
    try:
        with DDGS() as ddgs:
            search_query = f"{query} препараты список АФС вспомогательные вещества ГРЛС"
            results = ddgs.text(search_query, max_results=8)
            return "\n".join([r['body'] for r in results])
    except Exception as e:
        logger.error(f"DDGS Error: {e}")
        return ""

def parse_site_deep(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, timeout=15, headers=headers)
        soup = BeautifulSoup(r.content, 'html.parser')
        for s in soup(["script", "style"]): s.extract()
        return soup.get_text(separator=' ', strip=True)[:2500]
    except Exception as e:
        logger.error(f"Parse Error: {e}")
        return ""

# --- ОБРАБОТЧИКИ (ТЕПЕРЬ ВСЕ НА МЕСТЕ) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔬 R&D Модуль активен. Пришлите название компании или ссылку. Я найду АФС и рассчитаю фильтрацию.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Сбор данных
    deep_data = get_deep_info(user_input)
    url_match = re.search(r'https?://[^\s]+', user_input)
    site_info = parse_site_deep(url_match.group(0)) if url_match else ""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"ОБЪЕКТ: {user_input}\nДАННЫЕ ИЗ СЕТИ: {deep_data}\nДАННЫЕ САЙТА: {site_info}"}
            ],
            temperature=0
        )
        await update.message.reply_text(response.choices[0].message.content)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {str(e)}")

# --- ЗАПУСК ---
def main():
    if not TOKEN or not WEBHOOK_URL:
        logger.error("Переменные окружения не настроены!")
        return

    application = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info(f"Запуск Webhook на порту {PORT}")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()

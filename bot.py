import os
import logging
import re
import requests
from bs4 import BeautifulSoup

# СНАЧАЛА ИМПОРТЫ
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from openai import OpenAI
from duckduckgo_search import DDGS

# --- Настройки логов ---
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные из Render
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 10000))

# Инициализация OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# --- R&D Промпт (Универсальный инженерный уровень) ---
SYSTEM_PROMPT = (
    "Ты — эксперт R&D в области фармацевтической фильтрации. "
    "Твоя задача: глубокий технический анализ состава и процессов.\n"
    "1. АНАЛИЗ АФС: Определи химическую совместимость активных веществ с материалами мембран.\n"
    "2. ФИЗИКО-ХИМИЯ: Оцени вязкость, pH и риск адсорбции.\n"
    "3. МАТЕМАТИЧЕСКИЙ РАСЧЕТ: Используй формулу A = V / (J * t) для расчета площади фильтрации. "
    "Оперируй понятиями LMH (L/m2/h) и дифференциальным давлением (dP).\n"
    "4. ТЕХНОЛОГИЯ: Предложи каскад (предфильтр + стерильная мембрана). Сравни PES, PVDF, PTFE, Nylon.\n"
    "5. НАУЧНАЯ БАЗА: Опирайся на техданные о сорбции и экстрагируемых веществах."
)

# --- Вспомогательные функции ---
def get_science_data(query):
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"{query} pharmaceutical filtration technical compatibility", max_results=3)
            return "\n".join([f"Источник: {r['body']}" for r in results])
    except Exception as e:
        logger.error(f"DDGS Error: {e}")
        return "Научные данные временно недоступны."

def parse_site(url):
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        for s in soup(["script", "style"]): s.extract()
        return soup.get_text(separator=' ', strip=True)[:3000]
    except Exception as e:
        logger.error(f"Parsing Error: {e}")
        return None

# --- Обработчики событий ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧪 R&D Модуль запущен. Я готов анализировать составы, искать научные статьи и считать площади фильтрации. Жду ваш запрос или ссылку.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Поиск ссылки в сообщении
    url_match = re.search(r'https?://[^\s]+', text)
    web_content = parse_site(url_match.group(0)) if url_match else ""
    
    # Сбор научного контекста
    science_info = get_science_data(text[:100])

    try:
        # Запрос к GPT-4o
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"ЗАПРОС: {text}\nКОНТЕНТ САЙТА: {web_content}\nНАУЧНАЯ БАЗА: {science_info}"}
            ]
        )
        await update.message.reply_text(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"GPT Error: {e}")
        await update.message.reply_text(f"⚠️ Ошибка R&D модуля: {str(e)}")

# --- Основной цикл ---
def main():
    if not TOKEN or not WEBHOOK_URL:
        print("ОШИБКА: Проверьте TELEGRAM_BOT_TOKEN и WEBHOOK_URL")
        return

    application = Application.builder().token(TOKEN).build()
    
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

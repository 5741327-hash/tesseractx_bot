import os
import logging
import re
import requests
from functools import wraps
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from duckduckgo_search import DDGS

# --- Настройки ---
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

client = OpenAI(api_key=OPENAI_API_KEY)
user_conversations = {}

SYSTEM_PROMPT = (
    "Ты — эксперт по фармацевтической фильтрации. Анализируй составы АФС (активных веществ), "
    "учитывай вязкость (например, Гипромеллоза), pH и совместимость материалов мембран (PES, PVDF, PTFE). "
    "Предлагай конкретные решения: типы мембран и каскадность."
)

# --- Функции логики ---
def search_science(query):
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"{query} pharmaceutical filtration", max_results=3)
            return "\n".join([f"- {r['title']}: {r['body']}" for r in results])
    except: return "Данные поиска недоступны."

def parse_site(url):
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(r.content, 'html.parser')
        return soup.get_text(separator=' ', strip=True)[:4000]
    except: return None

# --- Обработчики ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔬 Бот-технолог на связи! Пришли ссылку на сайт производителя или название препарата.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Проверка ADMIN_ID (если он задан)
    if ADMIN_ID and str(user_id) != str(ADMIN_ID):
        return

    text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    url_match = re.search(r'https?://[^\s]+', text)
    source_data = parse_site(url_match.group(0)) if url_match else text
    
    if not source_data:
        await update.message.reply_text("❌ Не удалось прочитать сайт.")
        return

    # Научный контекст
    science = search_science(source_data[:100])
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"ДАННЫЕ: {source_data}\n\nНАУЧНЫЙ ПОИСК: {science}\n\nДай рекомендации."}
    ]

    try:
        res = client.chat.completions.create(model="gpt-4o", messages=messages)
        await update.message.reply_text(res.choices[0].message.content)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("⚠️ Ошибка при обработке ИИ.")

# --- Запуск ---
def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    PORT = int(os.environ.get("PORT", 8080))
    
    logger.info(f"Запуск Webhook на порту {PORT}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
        drop_pending_updates=True # Это очистит очередь старых запросов
    )

if __name__ == '__main__':
    main()

import os
import logging
import re
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from duckduckgo_search import DDGS

# Логи
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 10000))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- ЖЕСТКИЙ R&D ПРОМПТ ---
SYSTEM_PROMPT = (
    "Ты — Технический Директор по фильтрации. Тебе ЗАПРЕЩЕНО отвечать 'я не знаю' или 'нужны данные'.\n"
    "Если данные из поиска отсутствуют, ты ОБЯЗАН использовать свои знания о продукции указанной компании.\n"
    "ТВОЙ ВЫХОДНОЙ ФОРМАТ:\n"
    "1. ТАБЛИЦА АФС: Название | Активное вещество | Вспомогательные (полимеры).\n"
    "2. ФИЗИКО-ХИМИЯ: Вязкость (cP) и pH для каждой группы.\n"
    "3. РЕШЕНИЕ: Тип мембраны (PES/PVDF/PTFE) и каскад.\n"
    "4. РАСЧЕТ: Площадь фильтрации на 100л за 2 часа (A = V / (J * t)). Используй реальный Flux (J) для вязких сред (60-100 LMH)."
)

def get_deep_info(query):
    try:
        with DDGS() as ddgs:
            results = ddgs.text(f"{query} препараты АФС состав ГРЛС", max_results=5)
            return "\n".join([r['body'] for r in results]) if results else "Данные не найдены."
    except:
        return "Ошибка поиска."

def parse_site_deep(url):
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(r.content, 'html.parser')
        for s in soup(["script", "style"]): s.extract()
        return soup.get_text(separator=' ', strip=True)[:2000]
    except:
        return "Сайт недоступен."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔬 R&D Агент готов. Назовите компанию, и я выдам аудит фильтрации по её портфелю.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    search_data = get_deep_info(user_input)
    url_match = re.search(r'https?://[^\s]+', user_input)
    site_info = parse_site_deep(url_match.group(0)) if url_match else ""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"ОБЪЕКТ: {user_input}. ДАННЫЕ СЕТИ: {search_data}. ДАННЫЕ САЙТА: {site_info}. Выполни полный аудит АФС и расчет фильтрации."}
            ],
            temperature=0.1
        )
        await update.message.reply_text(response.choices[0].message.content)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка GPT: {str(e)}")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{WEBHOOK_URL}/{TOKEN}")

if __name__ == '__main__':
    main()

import os
import logging
import re
import requests
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 10000))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- ИНЖЕНЕРНЫЙ ПРОМПТ (БЕЗ ВОДЫ) ---
SYSTEM_PROMPT = (
    "Ты — Технический Аудитор фармпроизводства. Твоя задача: найти АФС и вспомогательные вещества.\n"
    "1. СПИСОК ПРОДУКЦИИ: Извлеки названия всех препаратов.\n"
    "2. ПОИСК СОСТАВА (АФС): Для КАЖДОГО препарата найди активное вещество и концентрацию полимеров.\n"
    "3. ХИМИЧЕСКИЙ АНАЛИЗ: Определи динамическую вязкость (cP) и молекулярный вес АФС.\n"
    "4. ТЕХНИЧЕСКОЕ РЕШЕНИЕ: Предложи материал мембраны (PES/PVDF/Nylon) и размер пор (0.22/0.45/0.1).\n"
    "5. РАСЧЕТ: Рассчитай площадь фильтрации для 100л/2ч. "
    "Используй формулу A = V / (J * t). Дай конкретные цифры Flux (J) для каждой группы продуктов."
)

def get_deep_info(query):
    """Ищет конкретно составы и ГРЛС данные"""
    try:
        with DDGS() as ddgs:
            # Ищем реестры и инструкции
            search_query = f"{query} препараты список АФС вспомогательные вещества ГРЛС"
            results = ddgs.text(search_query, max_results=8)
            return "\n".join([r['body'] for r in results])
    except:
        return ""

def parse_site_deep(url):
    """Пытается найти ссылки на продукты и вытянуть текст"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, timeout=15, headers=headers)
        soup = BeautifulSoup(r.content, 'html.parser')
        
        # Собираем ссылки, похожие на каталог/продукты
        links = [a['href'] for a in soup.find_all('a', href=True) if 'catalog' in a['href'] or 'product' in a['href']]
        
        main_text = soup.get_text(separator=' ', strip=True)[:2000]
        return f"Main: {main_text} | Links found: {links[:5]}"
    except:
        return ""

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # 1. Сначала ищем по названию компании ВЕЗДЕ
    logger.info(f"Анализ АФС для: {user_input}")
    deep_data = get_deep_info(user_input)
    
    # 2. Парсим сайт на наличие структуры
    url_match = re.search(r'https?://[^\s]+', user_input)
    site_info = parse_site_deep(url_match.group(0)) if url_match else ""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"ОБЪЕКТ: {user_input}\nНАЙДЕННЫЕ ДАННЫЕ О СОСТАВАХ: {deep_data}\nДАННЫЕ САЙТА: {site_info}"}
            ],
            temperature=0
        )
        await update.message.reply_text(response.choices[0].message.content)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{WEBHOOK_URL}/{TOKEN}")

if __name__ == '__main__':
    main()

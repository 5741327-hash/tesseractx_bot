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

# --- Системный промпт для Инженера-Исследователя ---
SYSTEM_PROMPT = (
    "Ты — ведущий R&D инженер по фильтрации. Твоя цель — выдать конкретное техзадание (ТЗ).\n"
    "1. ИДЕНТИФИКАЦИЯ: На основе данных поиска и сайта составь список продукции компании.\n"
    "2. АНАЛИЗ СОСТАВА: Найди активные вещества (АФС) и вспомогательные компоненты (особенно полимеры: ГПМЦ, ПВС, Карбомеры).\n"
    "3. ТЕХНИЧЕСКИЕ ПАРАМЕТРЫ: Определи вязкость (cP) и pH. Если точных данных нет, используй отраслевые стандарты (напр. 0.3% ГПМЦ = 6 cP).\n"
    "4. ВЫБОР МЕМБРАНЫ: Укажи конкретные материалы (PES, PVDF, PTFE). Обоснуй выбор (напр. 'PES с низкой сорбцией для сохранения титра АФС').\n"
    "5. РАСЧЕТ SCALE-UP: Для серии 100л и времени 2ч рассчитай необходимую площадь фильтрации (A = V / (J * t)). "
    "Учитывай падение потока (Flux) при росте вязкости. Выдай результат в м2.\n"
    "6. ФОРМАТ: Отвечай строго, структурированно, без воды."
)

# --- Функции парсинга и поиска ---
def parse_site(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(url, timeout=15, headers=headers)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        # Убираем лишний мусор
        for s in soup(["script", "style", "nav", "footer"]): s.extract()
        return soup.get_text(separator=' ', strip=True)[:4000]
    except Exception as e:
        logger.error(f"Ошибка парсинга {url}: {e}")
        return ""

def deep_osint_search(query):
    try:
        with DDGS() as ddgs:
            # Ищем конкретно составы и реестры
            search_query = f"{query} состав препаратов ГРЛС вспомогательные вещества инструкция"
            results = ddgs.text(search_query, max_results=5)
            return "\n".join([f"Найдено в сети: {r['body']}" for r in results])
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        return "Внешние данные не найдены."

# --- Обработчики ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔬 R&D Агент запущен. Скиньте название фарм-компании или ссылку на каталог. Я проведу поиск по реестрам и рассчитаю фильтрацию.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # 1. Сбор данных из сети (OSINT)
    osint_data = deep_osint_search(user_input)
    
    # 2. Сбор данных с сайта (если есть ссылка)
    url_match = re.search(r'https?://[^\s]+', user_input)
    site_data = parse_site(url_match.group(0)) if url_match else ""

    try:
        # 3. Генерация экспертного ответа
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"ОБЪЕКТ: {user_input}\nДАННЫЕ ИЗ РЕЕСТРОВ: {osint_data}\nДАННЫЕ САЙТА: {site_data}"}
            ]
        )
        await update.message.reply_text(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"GPT Error: {e}")
        await update.message.reply_text(f"⚠️ Ошибка анализа: {str(e)}")

# --- Запуск приложения ---
def main():
    if not TOKEN or not WEBHOOK_URL:
        print("Ошибка: Проверьте переменные окружения!")
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

import os
import logging
import re
import requests
from bs4 import BeautifulSoup

# ИМПОРТЫ, КОТОРЫХ НЕ ХВАТАЛО
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

# --- R&D Промпт (Универсальный) ---
SYSTEM_PROMPT = (
    "Ты — ведущий R&D эксперт по процессам разделения и фильтрации в фармацевтике. "
    "Твой алгоритм анализа:\n"
    "1. ОПРЕДЕЛЕНИЕ АФС: Найди активные вещества и вспомогательные компоненты. Оцени их химическую природу и pH.\n"
    "2. ВЯЗКОСТЬ И ТЕМПЕРАТУРА: Оцени динамическую вязкость (μ) и её влияние на поток.\n"
    "3. МАТЕМАТИКА: Рассчитай требуемую площадь фильтрации (A) по формуле A = V / (J * t). "
    "Используй типовые значения потока (Flux, J) для разных сред (например, 500-1000 LMH для воды, 50-150 LMH для вязких сред).\n"
    "4. ВЫБОР МЕМБРАНЫ: Сравни PES, PVDF, PTFE, Nylon. Обоснуй выбор с точки зрения адсорбции и экстрагируемых веществ.\n"
    "5. КАСКАД: Предложи схему предфильтрации (глубинная фильтрация) перед стерильной мембраной."
)

# --- Функции поиска и парсинга ---
def get_science_data(query):
    try:
        with DDGS() as ddgs:
            # Ищем научные данные и тех. листы
            results = ddgs.text(f"{query} pharma filtration technical compatibility", max_results=3)
            return "\n".join([f"- {r['body']}" for r in results])
    except:
        return "Научные данные временно недоступны."

def parse_site(url):
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(r.content, 'html.parser')
        # Берем только важный текст, чтобы не перегружать контекст
        for script in soup(["script", "style"]): script.extract()
        return soup.get_text(separator=' ', strip=True)[:3000]
    except:
        return None

# --- Обработчики ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧪 R&D модуль активен. Пришлите название препарата, состав или ссылку на производство.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Поиск ссылки
    url_match = re.search(r'https?://[^\s]+', user_input)
    web_data = parse_site(url_match.group(0)) if url_match else ""
    
    # Научный контекст через DuckDuckGo
    science_context = get_science_data(user_input[:100])

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"ЗАПРОС: {user_input}\nДАННЫЕ САЙТА: {web_data}\nНАУЧНЫЙ ПОИСК: {science_context}"}
            ]
        )
        await update.message.reply_text(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("⚠️ Ошибка R&D модуля. Проверьте логи.")

# --- Запуск ---
def main():
    if not TOKEN or not WEBHOOK_URL:
        logger.error("Критические переменные отсутствуют!")
        return

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()

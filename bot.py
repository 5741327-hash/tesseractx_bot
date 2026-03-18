import os
import logging
import re
import random
import requests
from functools import wraps
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# --- 1. Настройка ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

client = OpenAI(api_key=OPENAI_API_KEY)
user_conversations = {}

# --- 2. Глубокий системный промпт ---
SYSTEM_PROMPT = (
    "Ты — эксперт-технолог фармацевтического производства и специалист по микробиологической фильтрации. "
    "Твоя задача: на основе данных о препаратах заказчика составить техническое предложение.\n\n"
    "АЛГОРИТМ ТВОЕЙ РАБОТЫ:\n"
    "1. Выдели конкретные препараты и их действующие вещества (АФС).\n"
    "2. Определи физико-химические свойства: вязкость, pH, чувствительность к температуре, наличие белков (адсорбция).\n"
    "3. Предложи схему фильтрации для каждого типа продукта:\n"
    "   - Глубинная фильтрация (осветление).\n"
    "   - Предфильтрация (защита стерилизующего слоя).\n"
    "   - Финальная стерилизующая фильтрация (0.22 мкм).\n"
    "4. Обоснуй выбор материала мембраны:\n"
    "   - PES (ПЭС): для водных растворов, белков (низкая адсорбция).\n"
    "   - PVDF (ПВДФ): для агрессивных сред, органики, газов.\n"
    "   - PTFE (Фторопласт): для воздуха и сильных растворителей.\n"
    "   - Nylon (Нейлон): для спиртовых растворов и щелочей.\n\n"
    "Если данных на сайте недостаточно, используй свои знания о технологии производства аналогичных лекарственных форм."
)

# --- 3. Функции парсинга и защиты ---
def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if str(update.effective_user.id) != ADMIN_ID:
            await update.message.reply_text("⛔️ Доступ ограничен.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

def parse_website(url):
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Убираем лишнее, оставляем только смысловые блоки
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
            
        # Ищем текст в заголовках и параграфах, где обычно описаны продукты
        content = []
        for tag in soup.find_all(['h1', 'h2', 'h3', 'p', 'li']):
            content.append(tag.get_text(strip=True))
            
        return " ".join(content)[:8000] # Больше лимит для глубокого анализа
    except Exception as e:
        logger.error(f"Ошибка парсинга {url}: {e}")
        return None

# --- 4. Логика обработки сообщений ---
async def get_ai_response(user_id, content, is_url=False):
    if user_id not in user_conversations:
        user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    if is_url:
        site_data = parse_website(content)
        if not site_data:
            return "Не удалось прочитать сайт. Возможно, стоит защита от ботов."
        prompt_content = f"Изучи этот текст с сайта производителя. Найди названия препаратов, их состав и предложи решения по фильтрации:\n\n{site_data}"
    else:
        prompt_content = content

    user_conversations[user_id].append({"role": "user", "content": prompt_content})
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=user_conversations[user_id]
        )
        answer = response.choices[0].message.content
        user_conversations[user_id].append({"role": "assistant", "content": answer})
        return answer
    except Exception as e:
        return f"Ошибка AI: {str(e)}"

# --- 5. Обработчики Telegram ---
@restricted
async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await update.message.reply_text("🧬 Контекст сброшен. Пришлите ссылку на сайт или название препарата для анализа техпроцесса.")

@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    url_match = re.search(r'https?://[^\s]+', text)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if url_match:
        url = url_match.group(0)
        await update.message.reply_text(f"🚀 Анализирую продуктовый портфель на {url}...")
        res = await get_ai_response(user_id, url, is_url=True)
    else:
        res = await get_ai_response(user_id, text)

    await update.message.reply_text(res)

# --- 6. Старт ---
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("wake", wake))
    app.add_handler(CommandHandler("start", wake))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == '__main__':
    main()

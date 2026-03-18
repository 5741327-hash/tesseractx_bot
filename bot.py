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

# --- 1. Настройка и Инициализация ---

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

try:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ADMIN_ID = os.getenv("ADMIN_ID")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL") # Оставьте пустым для локального запуска (Polling)

    if not all([TOKEN, OPENAI_API_KEY, ADMIN_ID]):
        raise ValueError("Проверьте переменные окружения: TOKEN, OPENAI_API_KEY, ADMIN_ID.")

except ValueError as e:
    logger.error(f"ОШИБКА КОНФИГУРАЦИИ: {e}")
    exit()

client = OpenAI(api_key=OPENAI_API_KEY)
user_conversations = {}

# --- 2. Системный промпт ---

SYSTEM_PROMPT = (
    "Ты — ведущий эксперт по микробиологической фильтрации в фармацевтике и биотехнологиях. "
    "Твоя задача: анализировать деятельность заказчика и предлагать технические решения.\n\n"
    "ПРАВИЛА:\n"
    "1. Если тебе присылают текст с сайта компании, определи тип продукции (ГЛС, вакцины, АФС, сыворотки).\n"
    "2. Опиши стадии производства (Upstream/Downstream) и предложи конкретные типы фильтров (материал мембраны, размер пор).\n"
    "3. Если пользователь просит расчет: используй формулу Площадь A = Q / (Flux * t). "
    "Если данные неполные, запроси поток (л/м2/ч) или время.\n"
    "4. Тон общения: профессиональный, лаконичный. Используй термины: стерилизующая фильтрация, биоёмкость, целостность мембраны."
)

# --- 3. Вспомогательные функции ---

def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if str(update.effective_user.id) != ADMIN_ID:
            await update.message.reply_text("⛔️ Доступ только для администратора.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

def parse_website(url):
    """Извлекает полезный текст с сайта."""
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        # Удаляем лишние элементы
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()
            
        text = soup.get_text(separator=' ', strip=True)
        # Ограничиваем объем текста для экономии токенов (первые 6000 символов)
        return text[:6000]
    except Exception as e:
        logger.error(f"Ошибка парсинга {url}: {e}")
        return None

# --- 4. Логика ИИ ---

async def get_ai_response(user_id, content, is_url=False):
    if user_id not in user_conversations:
        user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    if is_url:
        website_text = parse_website(content)
        if not website_text:
            return "Не удалось получить данные с сайта. Попробуйте скопировать текст вручную."
        message_content = f"Проанализируй данные с сайта компании и предложи решения по фильтрации:\n\n{website_text}"
    else:
        message_content = content

    user_conversations[user_id].append({"role": "user", "content": message_content})
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=user_conversations[user_id]
        )
        ai_message = response.choices[0].message.content
        user_conversations[user_id].append({"role": "assistant", "content": ai_message})
        
        # Лимит контекста (10 сообщений + системный)
        if len(user_conversations[user_id]) > 11:
            user_conversations[user_id] = [user_conversations[user_id][0]] + user_conversations[user_id][-10:]
            
        return ai_message
    except Exception as e:
        logger.error(f"OpenAI Error: {e}")
        return "Ошибка связи с ИИ. Попробуйте позже."

# --- 5. Обработчики команд ---

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 **Бот-эксперт по фильтрации запущен!**\n\n"
        "Что я умею:\n"
        "1. **Анализ сайта:** Пришлите ссылку на сайт заказчика.\n"
        "2. **Консультация:** Задавайте вопросы по процессам ЛС.\n"
        "3. **Расчеты:** Присылайте данные для расчета площади.\n\n"
        "Команда /wake сбрасывает контекст текущей беседы."
    )

@restricted
async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await update.message.reply_text("✨ Контекст очищен. Слушаю ваш запрос.")

@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Проверка на URL
    url_match = re.search(r'https?://[^\s]+', text)
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    if url_match:
        url = url_match.group(0)
        await update.message.reply_text(f"🔍 Изучаю сайт: {url}...")
        answer = await get_ai_response(user_id, url, is_url=True)
    else:
        answer = await get_ai_response(user_id, text)
    
    await update.message.reply_text(answer)

# --- 6. Запуск ---

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("wake", wake))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    if WEBHOOK_URL:
        PORT = int(os.environ.get("PORT", "8080"))
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{WEBHOOK_URL}{TOKEN}")
    else:
        app.run_polling()

if __name__ == '__main__':
    main()

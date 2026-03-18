import os
import logging
import re
from functools import wraps

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

try:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ADMIN_ID = os.getenv("ADMIN_ID")
    # WEBHOOK_URL оставляем, если деплоите на Render
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")

    if not all([TOKEN, OPENAI_API_KEY, ADMIN_ID]):
        raise ValueError("Не установлены базовые переменные окружения (Token, API Key, Admin ID).")

except ValueError as e:
    logger.error(f"ОШИБКА КОНФИГУРАЦИИ: {e}")
    exit()

client = OpenAI(api_key=OPENAI_API_KEY)

# Хранилище контекста бесед: {user_id: [messages]}
user_conversations = {}

# --- 2. Системный промпт (Экспертиза) ---

SYSTEM_PROMPT = (
    "Ты — эксперт в области микробиологической фильтрации в фармацевтической промышленности. "
    "Твоя задача: консультировать по процессам производства лекарственных средств (ЛС), подбирать типы фильтров и считать площади фильтрации. "
    "\n\nТвои знания включают:\n"
    "1. Стадии Upstream и Downstream (осветление, стерилизующая фильтрация, вирусная фильтрация, TFF).\n"
    "2. Материалы мембран (PES, PVDF, PTFE, Nylon) и их совместимость.\n"
    "3. Расчет площади фильтрации по формуле: A = Q / (V_flux * t). Помогай пользователю с расчетами.\n"
    "4. Рекомендации по валидации фильтров и тестам на целостность (Bubble Point).\n"
    "\nСтиль ответа: профессиональный, технически точный, структурированный. Если информации о заказчике/продукте мало, задавай уточняющие вопросы."
)

# --- 3. Декораторы ---

def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = str(update.effective_user.id)
        if user_id != ADMIN_ID:
            await update.message.reply_text("⛔️ Доступ ограничен.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- 4. Логика ИИ ---

async def get_ai_response(user_id, user_text):
    """Получает ответ от ИИ с учетом истории беседы."""
    if user_id not in user_conversations:
        user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # Добавляем сообщение пользователя в историю
    user_conversations[user_id].append({"role": "user", "content": user_text})
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=user_conversations[user_id]
        )
        ai_message = response.choices[0].message.content
        
        # Добавляем ответ ИИ в историю для контекста
        user_conversations[user_id].append({"role": "assistant", "content": ai_message})
        
        # Ограничиваем историю последних 15 сообщений, чтобы не раздувать контекст
        if len(user_conversations[user_id]) > 15:
            user_conversations[user_id] = [user_conversations[user_id][0]] + user_conversations[user_id][-14:]
            
        return ai_message
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {e}")
        return "Произошла ошибка при обработке запроса ИИ."

# --- 5. Обработчики ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔬 Бот-консультант по микробиологической фильтрации готов.\n\n"
        "Команды:\n"
        "/wake — начать новую сессию (сбросить контекст)\n"
        "Просто пишите вопросы о процессах или заказчиках в чат."
    )

@restricted
async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Сброс контекста
    user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await update.message.reply_text("✨ Контекст очищен. Я готов к новой сессии! О каком процессе или заказчике пойдет речь?")

@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    if not user_text:
        return

    # Отправляем уведомление о "печатании"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    answer = await get_ai_response(user_id, user_text)
    
    # Telegram поддерживает MarkdownV2 или HTML. Используем простой текст или базовый Markdown.
    await update.message.reply_text(answer, parse_mode=None)

# --- 6. Запуск ---

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("wake", wake))
    
    # Обрабатываем любой текст как вопрос к ИИ
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    if WEBHOOK_URL:
        PORT = int(os.environ.get("PORT", "8080"))
        logger.info(f"Запуск Webhook на порту {PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f'{WEBHOOK_URL}{TOKEN}'
        )
    else:
        logger.info("Запуск Polling (локально)")
        app.run_polling()

if __name__ == '__main__':
    main()

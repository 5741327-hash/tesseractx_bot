import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логов, чтобы видеть всё в панели Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Считываем переменные
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 10000))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Система активна. Жду название препарата!")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Получил сообщение: {update.message.text}")

def main():
    # Проверка критических данных прямо при старте
    if not TOKEN:
        raise ValueError("ОШИБКА: TELEGRAM_BOT_TOKEN не задан!")
    if not WEBHOOK_URL:
        raise ValueError("ОШИБКА: WEBHOOK_URL не задан! Проверь настройки Render.")

    logger.info(f"Запуск бота на порту {PORT} с URL {WEBHOOK_URL}")

    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Прямой запуск вебхука без условий
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()

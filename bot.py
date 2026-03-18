import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Настройка логов
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 10000))

async def start(update: Update, context):
    await update.message.reply_text("🔬 Бот запущен и готов к работе!")

async def echo(update: Update, context):
    await update.message.reply_text(f"Я получил: {update.message.text}")

def main():
    if not TOKEN or not WEBHOOK_URL:
        logger.error("Критические переменные отсутствуют!")
        return

    # Создаем объект приложения
    application = Application.builder().token(TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    logger.info(f"Запуск Webhook на порту {PORT}")

    # Использование параметров, которые Render гарантированно «проглатывает»
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
        # Добавляем секретный токен для верификации запросов от Telegram
        secret_token="A1b2C3d4E5f6G7h8", 
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()

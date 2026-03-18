import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Логи
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот живой и на связи!")

def main():
    if not TOKEN or not WEBHOOK_URL:
        logger.error("Проверь TOKEN или WEBHOOK_URL в настройках Render!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    PORT = int(os.environ.get("PORT", 8080))
    
    logger.info(f"Запуск Webhook: {WEBHOOK_URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()

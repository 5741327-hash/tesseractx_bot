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

import requests
from bs4 import BeautifulSoup

from openai import OpenAI 

# --- 1. Настройка и Инициализация ---

# Настройка логирования
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Чтение переменных окружения
try:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ADMIN_ID = os.getenv("ADMIN_ID")
    CHANNEL_ID = os.getenv("CHANNEL_ID")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")

    if not all([TOKEN, OPENAI_API_KEY, ADMIN_ID, CHANNEL_ID, WEBHOOK_URL]):
        raise ValueError("Не все переменные окружения установлены.")

except ValueError as e:
    logger.error(f"ОШИБКА КОНФИГУРАЦИИ: {e}")
    exit()

# Инициализация клиента OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# --- ГЛОБАЛЬНАЯ ИНИЦИАЛИЗАЦИЯ ---
# Объект Application должен быть глобальным.
try:
    app = Application.builder().token(TOKEN).build()
except Exception as e:
    logger.error(f"Ошибка при создании объекта Application: {e}")
    exit()
# ----------------------------------

# Глобальный словарь для хранения черновика поста
draft_post = {} 

# --- 2. Декораторы и Управление Доступом ---

def restricted(func):
    """Декоратор для ограничения доступа только администратору."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = str(update.effective_user.id)
        if user_id != ADMIN_ID:
            logger.warning(f"Попытка доступа от не-администратора: {user_id}")
            await update.message.reply_text("⛔️ Вы не являетесь администратором бота. Запрос отклонен.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- 3. Функции Парсинга и AI ---

def parse_article(url):
    """Извлекает заголовок и основной текст статьи по URL."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        
        title = soup.find('h1')
        title_text = title.get_text(strip=True) if title else "Заголовок не найден"

        article_body = soup.find('article') or soup.find('main') or soup.find('div', class_=re.compile(r'(content|body|post|article)', re.I))

        if not article_body:
            return title_text, "Не удалось найти основной блок статьи."

        for script_or_style in article_body(["script", "style", "nav", "footer"]):
            script_or_style.decompose()
            
        paragraphs = article_body.find_all('p')
        text = "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

        return title_text, text
        
    except requests.exceptions.RequestException as e:
        return "Ошибка парсинга", f"Ошибка запроса или таймаут: {e}"
    except Exception as e:
        logger.error(f"Ошибка при парсинге URL {url}: {e}")
        return "Ошибка парсинга", f"Произошла непредвиденная ошибка: {e}"


def generate_ai_content(title, raw_text):
    """Обрабатывает текст через GPT-4o для создания поста и промта для DALL-E."""
    
    # КРИТИЧНОЕ ИСПРАВЛЕНИЕ: ЖЕСТКОЕ ОГРАНИЧЕНИЕ ДЛИНЫ ПОСТА ДО 700 СИМВОЛОВ
    system_prompt = (
        "Ты — ведущий научный журналист и редактор популярного Telegram-канала 'Горизонт событий'. "
        "Твоя задача — превратить сырой текст научной новости в увлекательный, легко читаемый пост. "
        "ОБЩАЯ ДЛИНА ГОТОВОГО ПОСТА НЕ ДОЛЖНА ПРЕВЫШАТЬ 700 СИМВОЛОВ (включая пробелы и эмодзи)! " 
        "Это критически важно из-за ограничения Telegram на подпись к фотографии (caption). "
        "Используй дружелюбный, но информативный тон, добавляй подходящие эмодзи и абзацы. "
        "В конце обязательно сгенерируй детализированный промт на АНГЛИЙСКОМ языке для DALL-E 3. "
        "Заголовок статьи: '{title}'.\n\n"
        "Формат ответа строго следующий:\n"
        "[ПОСТ]\n"
        "Текст готового поста...\n\n"
        "[DALL-E PROMPT]\n"
        "Текст промта на английском..."
    ).format(title=title)
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text}
            ]
        )
        full_response = response.choices[0].message.content
        
        post_match = re.search(r"\[ПОСТ\]\n(.*?)(?=\n\n\[DALL-E PROMPT\]|$)", full_response, re.DOTALL)
        prompt_match = re.search(r"\[DALL-E PROMPT\]\n(.*?)$", full_response, re.DOTALL)
        
        if post_match and prompt_match:
            return post_match.group(1).strip(), prompt_match.group(1).strip()
        else:
            logger.error(f"Ошибка парсинга ответа GPT. Ответ: {full_response}")
            return "Ошибка форматирования ответа от AI.", "A simple conceptual image for a science article."
            
    except Exception as e:
        logger.error(f"Ошибка вызова OpenAI API для текста: {e}")
        return "Произошла ошибка при обращении к GPT.", "A simple conceptual image for a science article."

def generate_image_url(dalle_prompt):
    """Генерирует изображение с помощью DALL-E 3 и возвращает URL."""
    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=dalle_prompt,
            size="1024x1024",
            quality="standard",
            n=1
        )
        return response.data[0].url
    except Exception as e:
        logger.error(f"Ошибка вызова DALL-E API: {e}")
        return "https://via.placeholder.com/1024" 

# --- 4. Обработчики Команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команду /start."""
    await update.message.reply_text(
        "✨ Бот 'Горизонт событий' активен!\n\n"
        "👉 **Ваш рабочий процесс (Free Tier):**\n"
        "1. Отправьте **/wake** (если бот долго спал).\n"
        "2. Отправьте ссылку на статью.\n"
        "3. Отправьте **/publish**."
    )

@restricted
async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки и подтверждения пробуждения сервера."""
    await update.message.reply_text("✨ Сервер активен! Теперь можно отправлять ссылку.")

@restricted
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает сообщение, содержащее URL (только для админа)."""
    text = update.message.text
    url_match = re.search(r'https?://[^\s]+', text)
    if not url_match:
        await update.message.reply_text("Пожалуйста, отправьте корректную ссылку.")
        return

    url = url_match.group(0)
    await update.message.reply_text(f"⏳ **Начинаю обработку ссылки:** `{url}`\n\n1. Парсинг статьи...", parse_mode='Markdown')
    
    # 1. Парсинг
    title, article_text = parse_article(url)
    if "Ошибка парсинга" in title:
        await update.message.reply_text(f"❌ Парсинг не удался: {article_text}")
        return
        
    await update.message.reply_text("✅ Статья спарсена. 2. Передаю текст в GPT-4o...")
    
    # 2. Генерация текста и промта
    post_text, dalle_prompt = generate_ai_content(title, article_text)
    
    if "Произошла ошибка" in post_text:
        await update.message.reply_text(f"❌ Ошибка генерации AI: {post_text}")
        return

    await update.message.reply_text("✅ Текст сгенерирован. 3. Генерирую изображение через DALL-E 3...")

    # 3. Генерация изображения
    image_url = generate_image_url(dalle_prompt)
    
    # 4. Сохраняем черновик поста и отправляем его администратору
    global draft_post
    draft_post = {'text': post_text, 'image_url': image_url}
    
    # ИСПРАВЛЕНИЕ: Более короткий черновик, чтобы не превысить лимит 1024 символа
    caption_draft = f"**[Черновик]**\n\n{post_text}\n\n/publish для публикации"
    
    try:
        await update.message.reply_photo(
            photo=image_url,
            caption=caption_draft,
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Изображение не загружено. Ошибка: {e}\n\nТекст черновика:\n{caption_draft}", parse_mode='Markdown')

@restricted
async def publish_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команду /publish и отправляет пост в канал."""
    
    global draft_post
    if not draft_post or not draft_post.get('text'):
        await update.message.reply_text("Нет активного черновика для публикации. Отправьте ссылку, чтобы создать новый.")
        return
        
    try:
        await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=draft_post['image_url'],
            caption=draft_post['text'],
            parse_mode='Markdown'
        )
        await update.message.reply_text("🚀 Новость успешно опубликована в канал 'Горизонт событий'!")
        draft_post = {}
    except Exception as e:
        await update.message.reply_text(
            f"❌ Ошибка публикации в канал. Проверьте ID канала (`{CHANNEL_ID}`) и права бота: {e}"
        )


# --- 5. Функция Запуска (Webhook для Render) ---

def main():
    """Настраивает обработчики и запускает встроенный веб-сервер."""
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("publish", publish_post))
    app.add_handler(CommandHandler("wake", wake))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'https?://[^\s]+'), handle_link))
    
    logger.info(f"Настройка Webhook по адресу: {WEBHOOK_URL}{TOKEN}")
    
    # Получаем порт, предоставленный Render
    PORT = int(os.environ.get("PORT", "8080")) 

    # Запускаем встроенный веб-сервер Python-Telegram-Bot
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f'{WEBHOOK_URL}{TOKEN}'
    )

# --- Точка входа ---
if __name__ == '__main__':
    main()

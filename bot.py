import os
import logging
import re
from functools import wraps
from urllib.parse import urljoin
import random

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

# КОНСТАНТА: Максимальная безопасная длина поста, чтобы не превысить лимит Telegram (1024 символа)
MAX_POST_LENGTH = 800

# Список актуальных User-Agent'ов для ротации
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


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

# --- 3. Функции Парсинга, AI, Изображений и Безопасности ---

def safe_html(text):
    """
    Экранирует опасные символы (<, >, &) в тексте, не трогая валидные теги <b> и <i>.
    Это предотвращает ошибку Can't parse entities: unexpected end tag.
    """
    # Экранируем амперсанд
    text = text.replace('&', '&amp;')
    
    # Временно заменяем валидные теги на плейсхолдеры
    text = text.replace('<b>', '___B_OPEN___').replace('</b>', '___B_CLOSE___')
    text = text.replace('<i>', '___I_OPEN___').replace('</i>', '___I_CLOSE___')
    
    # Экранируем все остальные угловые скобки 
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    
    # Возвращаем валидные теги обратно
    text = text.replace('___B_OPEN___', '<b>').replace('___B_CLOSE___', '</b>')
    text = text.replace('___I_OPEN___', '<i>').replace('___I_CLOSE___', '</i>')
    
    # Telegram HTML поддерживает только ограниченный набор, здесь учитываем основные
    
    return text

def parse_article(url):
    """Извлекает заголовок и основной текст статьи по URL. Усиленные заголовки для обхода 403."""
    try:
        # Ротация User-Agent
        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/',
        }
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

def find_image_in_article(url):
    """Ищет URL главного изображения статьи через мета-теги или в контенте."""
    try:
        # Ротация User-Agent
        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/',
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 1. Поиск по мета-тегу og:image (самый надежный способ)
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return og_image['content']
            
        # 2. Поиск первой большой картинки в основном контенте
        article_body = soup.find('article') or soup.find('main')
        if article_body:
            first_img = article_body.find('img', class_=re.compile(r'(main|hero|featured|post-image)', re.I))
            if first_img and first_img.get('src'):
                img_src = first_img['src']
                if img_src.startswith('http'):
                    return img_src
                
                # Обработка относительных URL
                return urljoin(url, img_src)
                
    except Exception as e:
        logger.warning(f"Ошибка при поиске изображения в статье {url}: {e}")
        return None

    return None

def generate_ai_content(title, raw_text):
    """Обрабатывает текст через GPT-4o для создания поста и промта для DALL-E. Использует HTML."""
        
    # ЖЕСТКОЕ ОГРАНИЧЕНИЕ ДЛИНЫ ПОСТА В ПРОМТЕ (850)
    system_prompt = (
        "Разбери следующий научный текст на "косточки", а затем заново собери его как понятное руководство или объяснение.

        "**План действий:**
        "1.  **Выпиши все ключевые термины.** Рядом с каждым напиши его простое определение (максимум 10 слов)."
        "2.  **Определи 3-5 ключевых фактов/открытий**, без которых нельзя понять суть."
        "3.  **Напиши новый текст,** используя только эти простые определения и ключевые факты."
        "4.  **Используй принцип "бутерброда":**"
        "   *   Сначала скажи простой тезис (например, "Ученые нашли способ замедлить старение клеток")."
        "*   Затем приведи аналогию (например, "Представьте, что в клетке есть "мусоропровод". Ученые научились чинить его засоры")."
        "*   Потом аккуратно добавь немного деталей из исходного исследования, используя упрощенные термины."
        "5.  **Проверь:** Может ли человек, далекий от науки, понять суть за 2 минуты чтения?"

        "СЛЕДУЙ СТРОГО УКАЗАННОМУ НИЖЕ ФОРМАТУ ОТВЕТА. НЕ ДОБАВЛЯЙ НИКАКИХ ДРУГИХ СИМВОЛОВ ИЛИ ПОЯСНЕНИЙ ДО ИЛИ ПОСЛЕ."
        "Заголовок статьи: '{title}'.\n\n"
        "Формат ответа строго следующий:\n"
        "[ПОСТ]\n"
        "Текст готового поста...\n\n"
        "[DALL-E PROMPT]\n"
        "Текст промта на английском."
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
        
        # УСТОЙЧИВЫЙ ПАРСИНГ
        post_match = re.search(r"\[ПОСТ\]\s*(.*?)\s*(?=\[DALL-E PROMPT\]|$)", full_response, re.DOTALL | re.IGNORECASE)
        prompt_match = re.search(r"\[DALL-E PROMPT\]\s*(.*?)\s*$", full_response, re.DOTALL | re.IGNORECASE)
        
        if post_match and prompt_match:
            post_text = post_match.group(1).strip()
            dalle_prompt = prompt_match.group(1).strip()
            return post_text, dalle_prompt
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
        "2. Отправьте ссылку на статью (автоматический режим) ИЛИ **скопированный текст статьи** (ручной режим).\n"
        "3. Отправьте **/publish**."
    )

@restricted
async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для проверки и подтверждения пробуждения сервера."""
    await update.message.reply_text("✨ Сервер активен! Теперь можно отправлять ссылку или текст.")

@restricted
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает сообщение, содержащее URL (автоматический режим)."""
    text = update.message.text
    url_match = re.search(r'https?://[^\s]+', text)
    if not url_match:
        await update.message.reply_text("Пожалуйста, отправьте корректную ссылку.")
        return

    url = url_match.group(0)
    await update.message.reply_text(f"⏳ <b>Начинаю обработку ссылки:</b> <code>{url}</code>\n\n1. Парсинг статьи...", parse_mode='HTML')
    
    # 1. Парсинг
    title, article_text = parse_article(url)
    if "Ошибка парсинга" in title:
        await update.message.reply_text(f"❌ Парсинг не удался: {article_text}")
        return
        
    await update.message.reply_text("✅ Статья спарсена. 2. Передаю текст в GPT-4o...")
    
    # 2. Генерация текста и промта
    post_text, dalle_prompt = generate_ai_content(title, article_text)
    
    if "Ошибка форматирования" in post_text or "Произошла ошибка" in post_text:
        await update.message.reply_text(f"❌ Ошибка генерации AI: {post_text}")
        return

    # --- ПРИНУДИТЕЛЬНОЕ ОБРЕЗАНИЕ ТЕКСТА ---
    if len(post_text) > MAX_POST_LENGTH:
        post_text = post_text[:MAX_POST_LENGTH] + "\n\n<b>[...Обрезано из-за лимита Telegram]</b>"
        await update.message.reply_text(f"⚠️ <b>Внимание:</b> Сгенерированный пост был <b>обрезан</b> до {MAX_POST_LENGTH} символов, чтобы соответствовать лимиту подписи Telegram (1024 символа).", parse_mode='HTML')
    # ----------------------------------------------------
    
    # !!! БЕЗОПАСНОСТЬ: Экранируем текст перед использованием !!!
    post_text = safe_html(post_text)

    # --- ИНТЕЛЛЕКТУАЛЬНЫЙ ПОИСК ИЗОБРАЖЕНИЯ ---
    image_url = find_image_in_article(url)

    if image_url:
        await update.message.reply_text("✅ Изображение найдено в статье. Пропускаю DALL-E.")
    else:
        await update.message.reply_text("⚠️ Изображение в статье не найдено. 3. Генерирую изображение через DALL-E 3...")
        # 3. Генерация изображения (используется только как запасной вариант)
        image_url = generate_image_url(dalle_prompt)
    
    # 4. Сохраняем черновик поста и отправляем его администратору
    global draft_post
    draft_post = {'text': post_text, 'image_url': image_url}
    
    caption_draft = f"<b>[Черновик]</b>\n\n{post_text}\n\n/publish для публикации"
    
    try:
        await update.message.reply_photo(
            photo=image_url,
            caption=caption_draft,
            parse_mode='HTML' # Используем HTML для форматированного текста
        )
    except Exception as e:
        logger.error(f"Ошибка отправки фото с подписью: {e}")
        await update.message.reply_text(f"❌ Изображение не загружено. Ошибка: {e}\n\nТекст черновика:\n{caption_draft}", parse_mode='HTML')

@restricted
async def handle_manual_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает длинное текстовое сообщение как сырой текст статьи (ручной режим).
    Срабатывает, если нет URL в тексте.
    """
    raw_text = update.message.text
    
    # !!! ИСПРАВЛЕНИЕ 1: Добавляем сообщение, если текст слишком короткий !!!
    if len(raw_text) < 500:
        await update.message.reply_text(
            f"⚠️ **Ручной режим:** Отправленный текст слишком короткий ({len(raw_text)} символов). "
            f"Минимальная длина для обработки статьи — 500 символов. "
            f"Отправьте полный скопированный текст статьи.",
            parse_mode='Markdown'
        )
        logger.info(f"Ручной режим: отклонен текст длиной {len(raw_text)} (менее 500)")
        return
    # ----------------------------------------------------------------------

    # Используем HTML
    await update.message.reply_text("⏳ <b>Ручной режим активирован.</b>\n\n1. Передаю текст в GPT-4o...", parse_mode='HTML')
    
    # 1. Генерация текста и промта
    title = "Ручная вставка статьи"
    post_text, dalle_prompt = generate_ai_content(title, raw_text)
    
    if "Ошибка форматирования" in post_text or "Произошла ошибка" in post_text:
        await update.message.reply_text(f"❌ Ошибка генерации AI: {post_text}")
        return

    # --- ПРИНУДИТЕЛЬНОЕ ОБРЕЗАНИЕ ТЕКСТА ---
    if len(post_text) > MAX_POST_LENGTH:
        post_text = post_text[:MAX_POST_LENGTH] + "\n\n<b>[...Обрезано из-за лимита Telegram]</b>"
        await update.message.reply_text(f"⚠️ <b>Внимание:</b> Сгенерированный пост был <b>обрезан</b> до {MAX_POST_LENGTH} символов, чтобы соответствовать лимиту подписи Telegram (1024 символа).", parse_mode='HTML')
    # ----------------------------------------------------
    
    # !!! БЕЗОПАСНОСТЬ: Экранируем текст перед использованием !!!
    post_text = safe_html(post_text)

    await update.message.reply_text("✅ Текст сгенерирован. 2. Генерирую изображение через DALL-E 3...")

    # 2. Генерация изображения (в ручном режиме всегда используем DALL-E)
    image_url = generate_image_url(dalle_prompt)
    
    # 3. Сохраняем черновик поста и отправляем его администратору
    global draft_post
    draft_post = {'text': post_text, 'image_url': image_url}
    
    caption_draft = f"<b>[Черновик]</b>\n\n{post_text}\n\n/publish для публикации"
    
    try:
        await update.message.reply_photo(
            photo=image_url,
            caption=caption_draft,
            parse_mode='HTML' # Используем HTML для форматированного текста
        )
    except Exception as e:
        logger.error(f"Ошибка отправки фото с подписью: {e}")
        await update.message.reply_text(f"❌ Изображение не загружено. Ошибка: {e}\n\nТекст черновика:\n{caption_draft}", parse_mode='HTML')


@restricted
async def publish_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команду /publish и отправляет пост в канал."""
    
    global draft_post
    if not draft_post or not draft_post.get('text'):
        await update.message.reply_text("Нет активного черновика для публикации. Отправьте ссылку или текст, чтобы создать новый.")
        return
        
    try:
        await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=draft_post['image_url'],
            caption=draft_post['text'],
            parse_mode='HTML' # Используем HTML для форматированного текста
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
    
    # Обработчик 1: Автоматический режим (содержит URL) - имеет ПРИОРИТЕТ
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'https?://[^\s]+'), 
        handle_url
    ))
    
    # Обработчик 2: Ручной режим (любой ДЛИННЫЙ текст, который НЕ является командой или URL)
    app.add_handler(MessageHandler(
        filters.TEXT 
        & ~filters.COMMAND 
        & ~filters.Regex(r'https?://[^\s]+'), 
        handle_manual_text
    ))
    
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

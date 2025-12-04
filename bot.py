import os
import logging
import re
from functools import wraps
from urllib.parse import urljoin 
import random 
import asyncio # Добавляем для использования задержки

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

# КОНСТАНТА: Максимальная безопасная длина ПЕРВОЙ ЧАСТИ поста (для подписи).
MAX_CAPTION_LENGTH_AI_TARGET = 800 

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

# --- 3. Функции Парсинга, AI и Изображений ---

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
    """
    Обрабатывает текст через GPT-4o для создания поста и промта для DALL-E.
    Запрашивает разделение поста на две части.
    """
    
    # ОБНОВЛЕНИЕ ПРОМТА: Убрано упоминание ссылки на источник
    system_prompt = (
        "Ты — ведущий научный журналист и редактор популярного Telegram-канала 'Горизонт событий'. "
        "Твоя задача — превратить сырой текст научной новости в увлекательный, легко читаемый пост. "
        "ИСПОЛЬЗУЙ ТОЛЬКО HTML-ФОРМАТИРОВАНИЕ ДЛЯ ВЫДЕЛЕНИЯ ТЕКСТА (например, <b> и <i>). "
        "Обязательно структурируй пост, используя <b>полужирные заголовки</b> или <b>ключевые фразы</b> для выделения основных идей или выводов. "
        "РАЗДЕЛИ ВЕСЬ ТЕКСТ ПОСТА НА ДВЕ ЧАСТИ, используя разделители [ПОСТ ЧАСТЬ 1] и [ПОСТ ЧАСТЬ 2]. "
        f"<b>ПЕРВАЯ ЧАСТЬ</b> должна содержать ключевую информацию (завязку) и иметь длину <b>НЕ БОЛЕЕ {MAX_CAPTION_LENGTH_AI_TARGET} СИМВОЛОВ</b>, чтобы уместиться в подпись к фото. "
        "ВТОРАЯ ЧАСТЬ должна содержать оставшийся, менее критичный, но важный текст. "
        "В конце обязательно сгенерируй детализированный промт на АНГЛИЙСКОМ языке для DALL-E 3. "
        
        "СЛЕДУЙ СТРОГО УКАЗАННОМУ НИЖЕ ФОРМАТУ ОТВЕТА. НЕ ДОБАВЛЯЙ НИКАКИХ ДРУГИХ СИМВОЛОВ ИЛИ ПОЯСНЕНИЙ ДО ИЛИ ПОСЛЕ."
        "Заголовок статьи: '{title}'.\n\n"
        "Формат ответа строго следующий:\n"
        "[ПОСТ ЧАСТЬ 1]\n"
        "Текст первой части...\n\n"
        "[ПОСТ ЧАСТЬ 2]\n"
        "Текст второй части...\n\n"
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
        
        # ОБНОВЛЕННЫЙ ПАРСИНГ: Ищем три части
        post1_match = re.search(r"\[ПОСТ ЧАСТЬ 1\]\s*(.*?)\s*(?=\[ПОСТ ЧАСТЬ 2\]|$)", full_response, re.DOTALL | re.IGNORECASE)
        post2_match = re.search(r"\[ПОСТ ЧАСТЬ 2\]\s*(.*?)\s*(?=\[DALL-E PROMPT\]|$)", full_response, re.DOTALL | re.IGNORECASE)
        prompt_match = re.search(r"\[DALL-E PROMPT\]\s*(.*?)\s*$", full_response, re.DOTALL | re.IGNORECASE)

        if post1_match and post2_match and prompt_match:
            post_part_1 = post1_match.group(1).strip()
            post_part_2 = post2_match.group(1).strip()
            dalle_prompt = prompt_match.group(1).strip()

            # Принудительное обрезание ЧАСТИ 1, если AI ошибся и текст слишком длинный
            if len(post_part_1) > MAX_CAPTION_LENGTH_AI_TARGET:
                # Перемещаем обрезанный хвост в часть 2
                overflow = post_part_1[MAX_CAPTION_LENGTH_AI_TARGET:]
                post_part_1 = post_part_1[:MAX_CAPTION_LENGTH_AI_TARGET] + "..." 
                # Добавляем переполненный текст в начало второй части
                post_part_2 = overflow.strip() + "\n\n" + post_part_2.strip()
                
                # Удаляем троеточие, если оно оказалось в самом конце обрезанного текста
                post_part_1 = post_part_1.rstrip(".").rstrip()

            return post_part_1, post_part_2, dalle_prompt
        else:
            logger.error(f"Ошибка парсинга ответа GPT. Ответ: {full_response}")
            # Возвращаем общую ошибку
            return "Ошибка форматирования ответа от AI.", "Часть 2 отсутствует.", "A simple conceptual image for a science article."
            
    except Exception as e:
        logger.error(f"Ошибка вызова OpenAI API для текста: {e}")
        return "Произошла ошибка при обращении к GPT.", "Часть 2 отсутствует.", "A simple conceptual image for a science article."

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
        "👉 <b>Ваш рабочий процесс:</b>\n"
        "1. Отправьте <code>/wake</code>.\n"
        "2. Отправьте ссылку на статью (автоматический режим) ИЛИ <b>скопированный текст статьи</b> (ручной режим).\n"
        "3. Отправьте <code>/publish</code>.", 
        parse_mode='HTML' 
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
        
    await update.message.reply_text("✅ Статья спарсена. 2. Передаю текст в GPT-4o и запрашиваю разделение поста...")
    
    # 2. Генерация текста и промта
    result = generate_ai_content(title, article_text)
    if isinstance(result, tuple) and len(result) == 3:
        post_part_1, post_part_2, dalle_prompt = result
    else:
        # Обработка ошибки
        post_text, error_part_2, dalle_prompt = result
        await update.message.reply_text(f"❌ Ошибка генерации AI: {post_text}")
        return

    # --- ПРОВЕРКА ЧАСТИ 1 ---
    if len(post_part_1) > MAX_CAPTION_LENGTH_AI_TARGET:
        await update.message.reply_text(f"⚠️ <b>Внимание:</b> Длина <b>Первой части</b> превысила целевой лимит ({MAX_CAPTION_LENGTH_AI_TARGET} символов) и была принудительно обрезана. Длина после обрезки: {len(post_part_1)}.", parse_mode='HTML')
    # ----------------------------------------------------


    # --- ИНТЕЛЛЕКТУАЛЬНЫЙ ПОИСК ИЗОБРАЖЕНИЯ ---
    image_url = find_image_in_article(url)

    if image_url:
        await update.message.reply_text("✅ Изображение найдено в статье. Пропускаю DALL-E.")
    else:
        await update.message.reply_text("⚠️ Изображение в статье не найдено. 3. Генерирую изображение через DALL-E 3...")
        # 3. Генерация изображения (используется только как запасной вариант)
        image_url = generate_image_url(dalle_prompt)
    
    # 4. Сохраняем черновик поста (УБРАН source_url) и отправляем его администратору
    global draft_post
    # УДАЛЕН 'source_url'
    draft_post = {'text_part_1': post_part_1, 'text_part_2': post_part_2, 'image_url': image_url}
    
    # Формируем подпись для ЧАСТИ 1 (УБРАН текст про источник)
    caption_draft = f"<b>[Черновик]</b>\n\n{post_part_1}\n\n<i>(Продолжение в следующем сообщении)</i>\n\n/publish для публикации"
    
    try:
        # ИСПОЛЬЗУЕМ HTML
        await update.message.reply_photo(
            photo=image_url,
            caption=caption_draft,
            parse_mode='HTML' 
        )
    except Exception as e:
        logger.error(f"Ошибка отправки фото с подписью: {e}")
        await update.message.reply_text(f"❌ Изображение не загружено. Ошибка: {e}\n\nТекст черновика:\n<code>{caption_draft}</code>", parse_mode='HTML')

@restricted
async def handle_manual_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает длинное текстовое сообщение как сырой текст статьи (ручной режим).
    Срабатывает, если нет URL в тексте.
    """
    raw_text = update.message.text
    
    # ПРОВЕРКА ДЛИНЫ: заменяет отсутствующий filters.Length
    if len(raw_text) < 500: 
        # Игнорируем короткие сообщения, которые не являются командами или URL
        return 


    await update.message.reply_text("⏳ <b>Ручной режим активирован.</b>\n\n1. Передаю текст в GPT-4o и запрашиваю разделение поста...", parse_mode='HTML')
    
    # 1. Генерация текста и промта
    title = "Ручная вставка статьи" 
    result = generate_ai_content(title, raw_text)
    if isinstance(result, tuple) and len(result) == 3:
        post_part_1, post_part_2, dalle_prompt = result
    else:
        # Обработка ошибки
        post_text, error_part_2, dalle_prompt = result
        await update.message.reply_text(f"❌ Ошибка генерации AI: {post_text}")
        return
        
    # --- ПРОВЕРКА ЧАСТИ 1 ---
    if len(post_part_1) > MAX_CAPTION_LENGTH_AI_TARGET:
        await update.message.reply_text(f"⚠️ <b>Внимание:</b> Длина <b>Первой части</b> превысила целевой лимит ({MAX_CAPTION_LENGTH_AI_TARGET} символов) и была принудительно обрезана. Длина после обрезки: {len(post_part_1)}.", parse_mode='HTML')
    # ----------------------------------------------------

    await update.message.reply_text("✅ Текст сгенерирован. 2. Генерирую изображение через DALL-E 3...")

    # 2. Генерация изображения (в ручном режиме всегда используем DALL-E)
    image_url = generate_image_url(dalle_prompt)
    
    # 3. Сохраняем черновик поста (УБРАН source_url) и отправляем его администратору
    global draft_post
    # УДАЛЕН 'source_url'
    draft_post = {'text_part_1': post_part_1, 'text_part_2': post_part_2, 'image_url': image_url}
    
    # Формируем подпись для ЧАСТИ 1 (УБРАН текст про источник)
    caption_draft = f"<b>[Черновик]</b>\n\n{post_part_1}\n\n<i>(Продолжение в следующем сообщении)</i>\n\n/publish для публикации"
    
    try:
        # ИСПОЛЬЗУЕМ HTML
        await update.message.reply_photo(
            photo=image_url,
            caption=caption_draft,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка отправки фото с подписью: {e}")
        await update.message.reply_text(f"❌ Изображение не загружено. Ошибка: {e}\n\nТекст черновика:\n<code>{caption_draft}</code>", parse_mode='HTML')


@restricted
async def publish_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команду /publish и отправляет пост в канал в два сообщения."""
    
    global draft_post
    if not draft_post or not draft_post.get('text_part_1'):
        await update.message.reply_text("Нет активного черновика для публикации. Отправьте ссылку или текст, чтобы создать новый.")
        return
        
    post_part_1 = draft_post['text_part_1']
    post_part_2 = draft_post['text_part_2']
    
    # 1. Формирование финальной подписи для фото (ЧАСТЬ 1)
    final_caption = post_part_1
    
    # Если есть вторая часть, добавляем приглашение к продолжению
    if post_part_2 and post_part_2.strip():
        final_caption += "\n\n<i>(Продолжение ниже)</i>"
        
    # Логика добавления ссылки на источник УДАЛЕНА.

    try:
        # ШАГ 1: Отправляем фото с ЧАСТЬЮ 1
        await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=draft_post['image_url'],
            caption=final_caption,
            parse_mode='HTML'
        )
        
        # ШАГ 2: Если есть ЧАСТЬ 2, отправляем ее отдельным сообщением с ЗАДЕРЖКОЙ
        if post_part_2 and post_part_2.strip():
             # ДОБАВЛЕНА ЗАДЕРЖКА В 1 СЕКУНДУ для надежного разделения сообщений
             await asyncio.sleep(1) 
             await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=post_part_2,
                parse_mode='HTML'
            )

        await update.message.reply_text("🚀 Новость успешно опубликована в канал 'Горизонт событий'!", parse_mode='HTML')
        draft_post = {}
    except Exception as e:
        await update.message.reply_text(
            f"❌ Ошибка публикации в канал. Проверьте ID канала (<code>{CHANNEL_ID}</code>) и права бота: {e}",
            parse_mode='HTML'
        )


# --- 5. Функция Запуска (Webhook для Render) ---

def main():
    """Настраивает обработчики и запускает встроенный веб-сервер."""
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("publish", publish_post))
    app.add_handler(CommandHandler("wake", wake))
    
    # Обработчик 1: Автоматический режим (содержит URL)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'https?://[^\s]+'), handle_url))
    
    # Обработчик 2: Ручной режим (любой текст, который НЕ является URL-ом)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.Regex(r'https?://[^\s]+'), handle_manual_text))
    
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

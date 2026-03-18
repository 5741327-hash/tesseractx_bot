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
from duckduckgo_search import DDGS

# --- 1. Настройка и Инициализация ---

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Чтение переменных окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

client = OpenAI(api_key=OPENAI_API_KEY)

# Хранилище контекста: {user_id: [messages]}
user_conversations = {}

# --- 2. Системный промпт (Инженерная база знаний) ---

SYSTEM_PROMPT = (
    "Ты — эксперт-технолог фармацевтической фильтрации и R&D консультант. "
    "Твоя задача: анализировать сайт заказчика, выявлять препараты и их действующие вещества (АФС), "
    "искать научные данные о проблемах их фильтрации и предлагать решения.\n\n"
    "ТВОЯ ЛОГИКА:\n"
    "1. Изучи препараты. Если это белки (антитела), учитывай риск сорбции. Если вязкие растворы — подбирай префильтры.\n"
    "2. Используй предоставленные результаты научного поиска для обоснования выбора мембраны (PES, PVDF, PTFE, Nylon).\n"
    "3. Для расчетов используй формулу: Площадь A = Q / (V_flux * t).\n"
    "4. Давай рекомендации по валидации (Extractables/Leachables) и тестам на целостность.\n"
    "\nОтвечай профессионально, структурированно, с акцентом на химическую совместимость и стерильность."
)

# --- 3. Функции поиска и парсинга ---

def search_scientific_insights(keywords):
    """Ищет научные и технические данные в сети."""
    search_results = ""
    try:
        with DDGS() as ddgs:
            # Ищем на английском для лучшего качества тех. документации
            query = f"{keywords} pharmaceutical filtration membrane adsorption compatibility"
            results = ddgs.text(query, max_results=3)
            for r in results:
                search_results += f"\n- {r['title']}: {r['body']}"
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
    return search_results

def parse_website(url):
    """Парсит текст сайта заказчика."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
            
        text = soup.get_text(separator=' ', strip=True)
        return text[:7000] # Ограничение для контекста
    except Exception as e:
        logger.error(f"Ошибка парсинга {url}: {e}")
        return None

# --- 4. Декоратор доступа ---

def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if str(update.effective_user.id) != ADMIN_ID:
            await update.message.reply_text("⛔️ Доступ запрещен.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- 5. Основная логика ИИ ---

async def handle_request(user_id, user_input, is_url=False):
    if user_id not in user_conversations:
        user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # 1. Получаем первичные данные
    context_data = ""
    if is_url:
        site_text = parse_website(user_input)
        if not site_text:
            return "Не удалось получить данные с сайта. Проверьте ссылку или вставьте текст вручную."
        context_data = f"Данные с сайта заказчика: {site_text}"
    else:
        context_data = user_input

    # 2. Извлекаем ключевые слова для научного поиска через быстрый запрос к GPT
    kw_response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"Выдели только названия лекарственных веществ (АФС) для поиска проблем их фильтрации: {context_data}"}]
    )
    keywords = kw_response.choices[0].message.content

    # 3. Ищем научные статьи/кейсы
    science_data = search_scientific_insights(keywords)
    
    # 4. Формируем финальный запрос для ИИ
    final_input = (
        f"ИСХОДНЫЕ ДАННЫЕ: {context_data}\n\n"
        f"НАУЧНЫЙ КОНТЕКСТ (из сети): {science_data}\n\n"
        "Проанализируй препараты, учти научные данные и предложи схему фильтрации."
    )

    user_conversations[user_id].append({"role": "user", "content": final_input})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=user_conversations[user_id]
        )
        answer = response.choices[0].message.content
        user_conversations[user_id].append({"role": "assistant", "content": answer})
        
        # Обрезка контекста для экономии токенов
        if len(user_conversations[user_id]) > 12:
            user_conversations[user_id] = [user_conversations[user_id][0]] + user_conversations[user_id][-10:]
            
        return answer
    except Exception as e:
        logger.error(f"OpenAI Error: {e}")
        return "Ошибка при генерации ответа."

# --- 6. Обработчики команд ---

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔬 **Система микробиологической фильтрации 2.0**\n\n"
        "Отправьте ссылку на сайт заказчика или список препаратов. "
        "Я найду научные статьи о компонентах и предложу решение.\n\n"
        "Команды:\n"
        "/wake — начать новую консультацию\n"
    )

@restricted
async def wake_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await update.message.reply_text("✨ Контекст очищен. Я готов к новому анализу!")

@restricted
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    url_match = re.search(r'https?://[^\s]+', text)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if url_match:
        url = url_match.group(0)
        await update.message.reply_text("🔎 Сканирую сайт и ищу научные статьи по компонентам...")
        answer = await handle_request(user_id, url, is_url=True)
    else:
        answer = await handle_request(user_id, text)

    await update.message.reply_text(answer)

# --- 7. Запуск ---

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("wake", wake_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("Бот запущен. Ожидание сообщений...")
    app.run_polling()

if __name__ == '__main__':
    main()

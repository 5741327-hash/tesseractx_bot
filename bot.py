# --- Новый R&D Промпт ---
SYSTEM_PROMPT = (
    "Ты — ведущий R&D эксперт по процессам разделения в фармацевтике. "
    "Твой алгоритм работы:\n"
    "1. АНАЛИЗ ОБЪЕКТА: Найди активные вещества (АФС) и вспомогательные компоненты. Определи их молекулярный вес и химическую природу.\n"
    "2. НАУЧНЫЙ КОНТЕКСТ: Найди данные о совместимости мембран (PES, PVDF, PTFE, Nylon, RC) с этими веществами.\n"
    "3. ТЕХНИЧЕСКОЕ РЕШЕНИЕ: Предложи каскад (от 10 мкм до 0.22/0.1 мкм). Укажи тип мембраны и материал корпуса.\n"
    "4. РАСЧЕТЫ: Рассчитай требуемую площадь фильтрации (A) по формуле A = V / (J * t), где J — удельный поток (flux). "
    "Оцени влияние вязкости по закону Дарси: Q = (k * A * ΔP) / (μ * L).\n"
    "5. ЭКОНОМИКА: Оцени риск преждевременной забиваемости (fouling)."
)

# --- Добавляем функцию поиска научных данных ---
def get_science_data(query):
    try:
        with DDGS() as ddgs:
            # Ищем конкретно научные статьи и тех. спецификации
            search_query = f"{query} pharmaceutical filtration technical paper compatibility"
            results = ddgs.text(search_query, max_results=4)
            return "\n".join([f"Источник: {r['body']}" for r in results])
    except:
        return "Научные данные из сети временно недоступны."

# --- Обновленный обработчик (теперь с поиском) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # 1. Поиск научной базы по запросу пользователя
    science_context = get_science_data(user_input[:100])
    
    # 2. Парсинг сайта (если есть ссылка)
    url_match = re.search(r'https?://[^\s]+', user_input)
    web_data = parse_site(url_match.group(0)) if url_match else ""

    try:
        response = client.chat.completions.create(
            model="gpt-4o", # Используем мощную модель для расчетов
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"ЗАПРОС: {user_input}\nДАННЫЕ САЙТА: {web_data}\nНАУЧНАЯ БАЗА: {science_context}"}
            ]
        )
        await update.message.reply_text(response.choices[0].message.content)
    except Exception as e:
        await update.message.reply_text(f"Ошибка R&D модуля: {e}")

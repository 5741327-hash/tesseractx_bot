# --- ЭКСПЕРТНЫЙ ПРОМПТ (БЕЗ ПРАВА НА ОТКАЗ) ---
SYSTEM_PROMPT = (
    "Ты — Технический Директор по фильтрации. Твой ответ — это инженерный аудит.\n"
    "ТЫ ОБЯЗАН: Использовать свои знания о продукции указанной компании (даже если поиск не выдал результат).\n"
    "ТВОЯ ЗАДАЧА:\n"
    "1. ПЕРЕЧЕНЬ ПРОДУКЦИИ: Выдай список препаратов компании (АФС + вспомогательные вещества).\n"
    "2. ТАБЛИЦА РАСЧЕТА: Для каждого типа продукта (глазные капли, инъекции, антисептики) укажи:\n"
    "   - Вязкость (cP);\n"
    "   - Материал мембраны (PES/PVDF/Nylon);\n"
    "   - Flux (J, л/м2/ч);\n"
    "   - Необходимую площадь (A) для 100 литров за 2 часа.\n"
    "3. РЕКОМЕНДАЦИЯ: Конкретные капсульные фильтры (напр. 10 дюймов, 0.22 мкм).\n"
    "ЗАПРЕЩЕНО: Просить данные у пользователя или писать 'я не знаю'."
)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Упрощаем: если поиск сбоит, GPT-4o вытянет на своих знаниях
    try:
        search_data = get_deep_info(user_input)
    except:
        search_data = "Поиск недоступен, используй внутреннюю базу."

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Сделай полный аудит компании: {user_input}. Данные из сети (если есть): {search_data}"}
            ],
            temperature=0.3 # Немного поднимем, чтобы он активнее лез в память
        )
        await update.message.reply_text(response.choices[0].message.content)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {str(e)}")

import logging
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import sqlite3
import pytz
from config import BOT_TOKEN as TOKEN
from admin_panel import admin_menu, process_admin_query, is_admin
from telegram.ext import CallbackQueryHandler


# Состояния для ConversationHandler
SELECT_SERVICE, SELECT_DATE, SELECT_TIME, ENTER_NAME, CONFIRMATION = range(5)

# Включаем логирование
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Часовой пояс
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# ────────────────────────────────────────────────
#  Инициализация базы данных
# ────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()

    # Таблица записей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            service TEXT,
            booking_date TEXT,
            booking_time TEXT,
            duration INTEGER,
            created_at TEXT,
            status TEXT DEFAULT 'pending'
        )
    ''')

    # Добавляем колонку duration, если её нет
    cursor.execute("PRAGMA table_info(bookings)")
    columns = {col[1] for col in cursor.fetchall()}
    if 'duration' not in columns:
        cursor.execute("ALTER TABLE bookings ADD COLUMN duration INTEGER")
        print("Добавлена колонка duration в таблицу bookings")

    # Таблица услуг
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            duration INTEGER,
            price INTEGER
        )
    ''')

    # Добавляем услуги, если таблица пустая
    cursor.execute("SELECT COUNT(*) FROM services")
    if cursor.fetchone()[0] == 0:
        services = [
            ('Консультация (30 мин)', 30, 1500),
            ('Стандартный сеанс (60 мин)', 60, 2500),
            ('Продолжительный сеанс (90 мин)', 90, 3500),
            ('Марафон (120 мин)', 120, 4500)
        ]
        cursor.executemany("INSERT INTO services (name, duration, price) VALUES (?, ?, ?)", services)

    conn.commit()
    conn.close()
    print("База данных инициализирована")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📅 Записаться на сеанс")],
        [KeyboardButton("📋 Мои записи"), KeyboardButton("ℹ️ Помощь")]
    ]
    # Если пишет админ — добавляем кнопку админки
    if is_admin(update.effective_user.id):
        keyboard.append([KeyboardButton("🛠 Админ-панель")])
        
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "👋 Добро пожаловать! Выберите действие:",
        reply_markup=reply_markup
    )
# ────────────────────────────────────────────────
#  Вспомогательная функция — свободные слоты с учётом длительности
# ────────────────────────────────────────────────
def get_available_times(date_str: str, duration: int) -> list[str]:
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT booking_time, duration 
        FROM bookings 
        WHERE booking_date = ?
    """, (date_str,))
    booked = cursor.fetchall()
    conn.close()

    def time_to_minutes(t: str) -> int:
        h, m = map(int, t.split(':'))
        return h * 60 + m

    all_times = ["10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00", "18:00"]
    available = []

    for t in all_times:
        start_min = time_to_minutes(t)
        end_min = start_min + duration

        conflict = False
        for bt, bd in booked:
            b_start = time_to_minutes(bt)
            b_duration = bd if bd is not None else 60  # старые записи — 60 мин по умолчанию
            b_end = b_start + b_duration

            # Пересечение интервалов
            if max(start_min, b_start) < min(end_min, b_end):
                conflict = True
                break

        if not conflict:
            available.append(t)

    return available


# ────────────────────────────────────────────────
#  Команды и обработчики
# ────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📅 Записаться на сеанс")],
        [KeyboardButton("📋 Мои записи"), KeyboardButton("ℹ️ Помощь")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text( # type: ignore
        "👋 Добро пожаловать! Я помогу вам записаться на сеанс.\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

def main():
    init_db()
    application = Application.builder().token(TOKEN).build()

    # Состояния ConversationHandler (оставляем как у вас)
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📅 Записаться на сеанс$"), start_booking)],
        states={
            SELECT_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_service)],
            SELECT_DATE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, select_date)],
            SELECT_TIME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, select_time)],
            ENTER_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            CONFIRMATION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_booking)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, duration, price FROM services")
    services = cursor.fetchall()
    conn.close()

    keyboard = [[KeyboardButton(f"{name} — {price} ₽")] for _, name, _, price in services]
    keyboard.append([KeyboardButton("❌ Отмена")])

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    context.user_data['services'] = services

    await update.message.reply_text("Выберите услугу:", reply_markup=reply_markup)
    return SELECT_SERVICE


async def select_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() # type: ignore

    if text == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardRemove()) # type: ignore
        return ConversationHandler.END

    for sid, name, dur, price in context.user_data.get('services', []):
        if f"{name} — {price} ₽" == text:
            context.user_data['selected_service'] = {
                'id': sid, 'name': name, 'duration': dur, 'price': price
            }
            break
    else:
        await update.message.reply_text("Пожалуйста, выберите услугу из списка.")
        return SELECT_SERVICE

    # Даты — следующие 7 дней, только будни
    today = datetime.now(MOSCOW_TZ).date()
    keyboard = []
    for i in range(7):
        d = today + timedelta(days=i)
        if d.weekday() < 5:  # пн–пт
            date_str = d.strftime("%d.%m.%Y (%A)")
            keyboard.append([KeyboardButton(date_str)])

    keyboard.append([KeyboardButton("❌ Отмена")])

    await update.message.reply_text(
        f"Выбрана услуга: {context.user_data['selected_service']['name']}\n\n"
        "Выберите дату:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return SELECT_DATE


async def select_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    context.user_data['selected_date'] = text

    duration = context.user_data['selected_service']['duration']
    available = get_available_times(text, duration)

    if not available:
        await update.message.reply_text(
            f"На {text} нет свободных окон для услуги длительностью {duration} минут.\n"
            "Выберите другую дату или отмените запись.",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)
        )
        return SELECT_DATE

    keyboard = []
    for i in range(0, len(available), 3):
        row = available[i:i+3]
        keyboard.append([KeyboardButton(t) for t in row])

    keyboard.append([KeyboardButton("❌ Отмена")])

    await update.message.reply_text(
        f"Дата: {text}\n\nВыберите время:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return SELECT_TIME


async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    context.user_data['selected_time'] = text

    service = context.user_data['selected_service']
    await update.message.reply_text(
        f"Отлично!\n\n"
        f"Услуга:   {service['name']}\n"
        f"Дата:     {context.user_data['selected_date']}\n"
        f"Время:    {text}\n\n"
        f"Введите ваше имя и фамилию:",
        reply_markup=ReplyKeyboardRemove()
    )
    return ENTER_NAME


async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['client_name'] = update.message.text.strip()

    service = context.user_data['selected_service']
    keyboard = [
        [KeyboardButton("✅ Подтвердить"), KeyboardButton("❌ Отменить")]
    ]

    await update.message.reply_text(
        f"Проверьте данные:\n\n"
        f"Имя:      {context.user_data['client_name']}\n"
        f"Услуга:   {service['name']}\n"
        f"Дата:     {context.user_data['selected_date']}\n"
        f"Время:    {context.user_data['selected_time']}\n"
        f"Цена:     {service['price']} ₽\n\n"
        f"Всё верно?",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return CONFIRMATION


async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ Отменить":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardRemove())
        context.user_data.clear()
        return ConversationHandler.END

    service = context.user_data['selected_service']
    date_str = context.user_data['selected_date']
    time_str = context.user_data['selected_time']
    duration = service['duration']

    # Проверка на случай, если слот заняли за время подтверждения
    available = get_available_times(date_str, duration)
    if time_str not in available:
        await update.message.reply_text(
            "❌ К сожалению, это время только что заняли.\n"
            "Пожалуйста, начните запись заново.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Сохраняем
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO bookings 
        (user_id, username, first_name, service, booking_date, booking_time, duration, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        update.effective_user.id,
        update.effective_user.username,
        context.user_data['client_name'],
        service['name'],
        date_str,
        time_str,
        duration,
        datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()

    keyboard = [
        [KeyboardButton("📅 Записаться на сеанс")],
        [KeyboardButton("📋 Мои записи"), KeyboardButton("ℹ️ Помощь")]
    ]

    await update.message.reply_text(
        f"✅ Запись №{booking_id} успешно создана!\n\n"
        f"Ждём вас {date_str} в {time_str}\n"
        f"{service['name']} — {service['price']} ₽\n\n"
        f"Для отмены или изменения свяжитесь с администратором.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

    context.user_data.clear()
    return ConversationHandler.END


async def view_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, service, booking_date, booking_time, created_at, status 
        FROM bookings 
        WHERE user_id = ? 
        ORDER BY booking_date DESC, booking_time DESC
        LIMIT 10
    ''', (update.effective_user.id,))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("У вас пока нет записей.")
        return

    lines = ["Ваши записи:\n"]
    for row in rows:
        rid, serv, dt, tm, created, status = row
        emoji = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌"}.get(status, "❓")
        lines.append(f"{emoji} №{rid}")
        lines.append(f"   {serv}")
        lines.append(f"   {dt}  {tm}")
        lines.append(f"   Статус: {status}")
        lines.append("")

    await update.message.reply_text("\n".join(lines))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ Помощь\n\n"
        "📅 Записаться на сеанс — начать новую запись\n"
        "📋 Мои записи — посмотреть ваши бронирования\n\n"
        "Команды:\n"
        "/start — главное меню\n"
        "/cancel — отменить текущий диалог\n\n"
        "По вопросам: +7 952 448 3814"
    )
    await update.message.reply_text(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END


# ────────────────────────────────────────────────
#  Запуск
# ────────────────────────────────────────────────
def main():
    init_db()

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📅 Записаться на сеанс$"), start_booking)],
        states={
            SELECT_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_service)],
            SELECT_DATE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, select_date)],
            SELECT_TIME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, select_time)],
            ENTER_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            CONFIRMATION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_booking)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.Regex("^📋 Мои записи$"), view_bookings))
    application.add_handler(MessageHandler(filters.Regex("^ℹ️ Помощь$"), help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu)) # Команда /admin
    application.add_handler(MessageHandler(filters.Regex("^🛠 Админ-панель$"), admin_menu)) # Кнопка
    application.add_handler(CallbackQueryHandler(process_admin_query)) # Обработка кнопок админки
    
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.Regex("^📋 Мои записи$"), view_bookings))
    application.add_handler(MessageHandler(filters.Regex("^ℹ️ Помощь$"), help_command))

    print("Бот запущен. Нажмите Ctrl+C для остановки")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
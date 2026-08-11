import logging
import sqlite3
import pytz
import secrets
import string
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler
)

# Импорты из ваших файлов
from config import BOT_TOKEN as TOKEN, ADMIN_ID
from admin_panel import admin_menu, process_admin_query, is_admin, notify_admin_new_booking, handle_admin_text

# Состояния
SELECT_SERVICE, SELECT_DATE, SELECT_TIME, ENTER_NAME, ENTER_PHONE, CONFIRMATION = range(6)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# --- БД ---
def init_db():
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, username TEXT, first_name TEXT,
            service TEXT, booking_date TEXT, booking_time TEXT,
            duration INTEGER, created_at TEXT, status TEXT DEFAULT 'pending',
            photo_file_id TEXT, review_text TEXT, review_photo TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
            duration INTEGER, price INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            user_id INTEGER PRIMARY KEY,
            token TEXT UNIQUE,
            phone TEXT,
            first_name TEXT,
            created_at TEXT
        )
    ''')
    cursor.execute("PRAGMA table_info(bookings)")
    columns = {col[1] for col in cursor.fetchall()}
    for col in ['duration', 'photo_file_id', 'review_text', 'review_photo', 'phone', 'client_token', 'review_requested']:
        if col not in columns:
            cursor.execute(f"ALTER TABLE bookings ADD COLUMN {col} TEXT")

    cursor.execute("SELECT COUNT(*) FROM services")
    if cursor.fetchone()[0] == 0:
        services = [('Консультация (30 мин)', 30, 1500), ('Стандартный сеанс (60 мин)', 60, 2500)]
        cursor.executemany("INSERT INTO services (name, duration, price) VALUES (?, ?, ?)", services)
    conn.commit()
    conn.close()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_available_times(date_str: str, duration: int) -> list[str]:
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute("SELECT booking_time, duration FROM bookings WHERE booking_date = ?", (date_str,))
    booked = cursor.fetchall()
    conn.close()
    
    def to_min(t):
        h, m = map(int, t.split(':'))
        return h * 60 + m

    all_slots = ["10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00", "18:00"]
    available = []
    for t in all_slots:
        s_min = to_min(t)
        e_min = s_min + duration
        if not any(max(s_min, to_min(bt)) < min(e_min, to_min(bt) + (bd or 60)) for bt, bd in booked):
            available.append(t)
    return available

def generate_token() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "CL-" + "".join(secrets.choice(alphabet) for _ in range(6))

def get_or_create_client(user_id: int, phone: str, name: str) -> str:
    """Находит клиента по user_id или создаёт нового с уникальным токеном-идентификатором."""
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute("SELECT token FROM clients WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        token = row[0]
        cursor.execute("UPDATE clients SET phone = ?, first_name = ? WHERE user_id = ?", (phone, name, user_id))
        conn.commit()
    else:
        while True:
            token = generate_token()
            try:
                cursor.execute(
                    "INSERT INTO clients (user_id, token, phone, first_name, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, token, phone, name, datetime.now(MOSCOW_TZ).isoformat())
                )
                conn.commit()
                break
            except sqlite3.IntegrityError:
                continue
    conn.close()
    return token

def parse_booking_datetime(date_str: str, time_str: str) -> datetime:
    """'12.08.2026 (Wednesday)' + '14:00' -> aware datetime в Europe/Moscow."""
    clean_date = date_str.split(" (")[0]
    naive = datetime.strptime(f"{clean_date} {time_str}", "%d.%m.%Y %H:%M")
    return MOSCOW_TZ.localize(naive)

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("📅 Записаться на сеанс")], [KeyboardButton("📋 Мои записи"), KeyboardButton("ℹ️ Помощь")]]
    if is_admin(update.effective_user.id):
        kb.append([KeyboardButton("🛠 Админ-панель")])
    await update.message.reply_text("👋 Добро пожаловать!", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, duration, price FROM services")
    services = cursor.fetchall()
    conn.close()
    context.user_data['services'] = services
    kb = [[KeyboardButton(f"{n} — {p} ₽")] for _, n, _, p in services]
    kb.append([KeyboardButton("❌ Отмена")])
    await update.message.reply_text("Выберите услугу:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SELECT_SERVICE

async def select_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена": return await cancel(update, context)
    for sid, name, dur, price in context.user_data.get('services', []):
        if f"{name} — {price} ₽" in text:
            context.user_data['selected_service'] = {'id': sid, 'name': name, 'duration': dur, 'price': price}
            break
    else: return SELECT_SERVICE
    
    today = datetime.now(MOSCOW_TZ).date()
    kb = [[KeyboardButton((today + timedelta(days=i)).strftime("%d.%m.%Y (%A)"))] for i in range(7) if (today + timedelta(days=i)).weekday() < 5]
    kb.append([KeyboardButton("❌ Отмена")])
    await update.message.reply_text("Выберите дату:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SELECT_DATE

async def select_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена": return await cancel(update, context)
    context.user_data['selected_date'] = text
    times = get_available_times(text, context.user_data['selected_service']['duration'])
    if not times:
        await update.message.reply_text("Нет окон, выберите другую дату.")
        return SELECT_DATE
    kb = [times[i:i+3] for i in range(0, len(times), 3)]
    kb.append(["❌ Отмена"])
    await update.message.reply_text("Выберите время:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SELECT_TIME

async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Отмена": return await cancel(update, context)
    context.user_data['selected_time'] = update.message.text
    await update.message.reply_text("Введите ваше имя:", reply_markup=ReplyKeyboardRemove())
    return ENTER_NAME

async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Отмена": return await cancel(update, context)
    context.user_data['client_name'] = update.message.text
    kb = [
        [KeyboardButton("📱 Отправить номер телефона", request_contact=True)],
        [KeyboardButton("❌ Отмена")]
    ]
    await update.message.reply_text(
        "Отправьте номер телефона кнопкой ниже или введите его вручную (например, +79991234567):",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    return ENTER_PHONE

async def enter_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        if update.message.text == "❌ Отмена": return await cancel(update, context)
        phone = update.message.text.strip()
        # Простая проверка: только цифры, +, -, пробелы и скобки, минимум 10 цифр
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) < 10:
            await update.message.reply_text("Похоже, номер введён некорректно. Попробуйте ещё раз или нажмите кнопку отправки номера.")
            return ENTER_PHONE

    context.user_data['client_phone'] = phone
    s = context.user_data['selected_service']
    msg = (f"Проверьте:\nИмя: {context.user_data['client_name']}\n"
           f"Телефон: {phone}\nУслуга: {s['name']}\n"
           f"Дата: {context.user_data['selected_date']} {context.user_data['selected_time']}")
    kb = [[KeyboardButton("✅ Подтвердить"), KeyboardButton("❌ Отменить")]]
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return CONFIRMATION

async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Отменить": return await cancel(update, context)
    
    s = context.user_data['selected_service']
    d_str, t_str = context.user_data['selected_date'], context.user_data['selected_time']
    phone = context.user_data.get('client_phone')
    name = context.user_data['client_name']

    token = get_or_create_client(update.effective_user.id, phone, name)

    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute('''INSERT INTO bookings (user_id, username, first_name, service, booking_date, booking_time, duration, created_at, phone, client_token)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                   (update.effective_user.id, update.effective_user.username, name, 
                    s['name'], d_str, t_str, s['duration'], datetime.now(MOSCOW_TZ).isoformat(),
                    phone, token))
    conn.commit()
    bid = cursor.lastrowid
    conn.close()

    schedule_review_job(context.application, bid, d_str, t_str)

    await notify_admin_new_booking(context, {'id': bid, 'name': name, 'service': s['name'], 'date': d_str, 'time': t_str, 'username': update.effective_user.username, 'phone': phone, 'token': token})
    await update.message.reply_text(
        f"✅ Запись №{bid} создана!\n🪪 Ваш ID клиента: {token} (пригодится, если будете обращаться к нам повторно)",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на сеанс")]], resize_keyboard=True)
    )
    context.user_data.clear()
    return ConversationHandler.END

async def handle_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or 'upload_photo_bid' not in context.user_data: return
    bid = context.user_data.pop('upload_photo_bid')
    conn = sqlite3.connect('bookings.db'); cursor = conn.cursor()
    cursor.execute("UPDATE bookings SET photo_file_id = ? WHERE id = ?", (update.message.photo[-1].file_id, bid))
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Фото добавлено к №{bid}")

async def handle_customer_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bid = context.user_data.get('awaiting_review_bid')
    if not bid: return
    text = update.message.text or update.message.caption
    photo = update.message.photo[-1].file_id if update.message.photo else None
    conn = sqlite3.connect('bookings.db'); cursor = conn.cursor()
    cursor.execute("UPDATE bookings SET review_text = ?, review_photo = ? WHERE id = ?", (text, photo, bid))
    conn.commit(); conn.close()
    context.user_data.pop('awaiting_review_bid')
    await update.message.reply_text("🙏 Спасибо за отзыв!")
    await context.bot.send_message(ADMIN_ID, f"🌟 Новый отзыв по №{bid}:\n{text}")

async def view_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('bookings.db'); cursor = conn.cursor()
    cursor.execute("SELECT id, service, booking_date, status FROM bookings WHERE user_id = ? LIMIT 5", (update.effective_user.id,))
    rows = cursor.fetchall(); conn.close()
    if not rows: return await update.message.reply_text("Нет записей.")
    res = "\n".join([f"№{r[0]} {r[1]} - {r[2]} ({r[3]})" for r in rows])
    await update.message.reply_text(res)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END

async def ask_for_review_job(context: ContextTypes.DEFAULT_TYPE):
    """Срабатывает через 24ч после времени записи: просит клиента оценить качество обслуживания."""
    bid = context.job.data['bid']
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, user_id, service, photo_file_id, review_requested FROM bookings WHERE id = ?", (bid,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    status, u_id, s_name, a_photo, review_requested = row
    # Отменённые записи и уже запрошенные отзывы не трогаем
    if status == 'cancelled' or review_requested:
        conn.close()
        return
    cursor.execute("UPDATE bookings SET status = 'completed', review_requested = '1' WHERE id = ?", (bid,))
    conn.commit()
    conn.close()

    feedback_msg = (f"✨ Прошли сутки после записи «{s_name}». "
                     f"Пожалуйста, оцените качество обслуживания — оставьте отзыв или пришлите фото результата.")
    try:
        if a_photo:
            await context.bot.send_photo(chat_id=u_id, photo=a_photo, caption=feedback_msg)
        else:
            await context.bot.send_message(chat_id=u_id, text=feedback_msg)
        context.application.user_data[u_id] = {'awaiting_review_bid': bid}
    except Exception:
        pass
    try:
        await context.bot.send_message(ADMIN_ID, f"ℹ️ По №{bid} автоматически запрошен отзыв (прошло 24ч с визита).")
    except Exception:
        pass

def schedule_review_job(app: Application, bid: int, date_str: str, time_str: str):
    try:
        appt_dt = parse_booking_datetime(date_str, time_str)
    except ValueError:
        return
    run_at = appt_dt + timedelta(hours=24)
    app.job_queue.run_once(ask_for_review_job, when=run_at, data={'bid': bid}, name=f"review_{bid}")

def schedule_pending_reviews(app: Application):
    """Восстанавливает отложенные напоминания после перезапуска бота."""
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, booking_date, booking_time FROM bookings
        WHERE status != 'cancelled' AND (review_requested IS NULL OR review_requested = '')
    """)
    rows = cursor.fetchall()
    conn.close()
    for bid, d_str, t_str in rows:
        schedule_review_job(app, bid, d_str, t_str)

# --- ЗАПУСК ---
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📅 Записаться на сеанс$"), start_booking)],
        states={
            SELECT_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_service)],
            SELECT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_date)],
            SELECT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_time)],
            ENTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            ENTER_PHONE: [MessageHandler((filters.TEXT | filters.CONTACT) & ~filters.COMMAND, enter_phone)],
            CONFIRMATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_booking)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(MessageHandler(filters.Regex("^🛠 Админ-панель$"), admin_menu))
    app.add_handler(CallbackQueryHandler(process_admin_query))
    app.add_handler(MessageHandler(filters.PHOTO & filters.Chat(ADMIN_ID), handle_admin_photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.Chat(ADMIN_ID) & ~filters.COMMAND, handle_admin_text), group=-1)
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_customer_review), group=-1)
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Regex("^📋 Мои записи$"), view_bookings))

    schedule_pending_reviews(app)

    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
import logging
import sqlite3
import pytz
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler
)

# Импорты из ваших файлов
from config import BOT_TOKEN as TOKEN, ADMIN_ID
from admin_panel import admin_menu, process_admin_query, is_admin, notify_admin_new_booking

# Состояния
SELECT_SERVICE, SELECT_DATE, SELECT_TIME, ENTER_NAME, CONFIRMATION = range(5)

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
    cursor.execute("PRAGMA table_info(bookings)")
    columns = {col[1] for col in cursor.fetchall()}
    for col in ['duration', 'photo_file_id', 'review_text', 'review_photo']:
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
    context.user_data['client_name'] = update.message.text
    s = context.user_data['selected_service']
    msg = f"Проверьте:\nИмя: {update.message.text}\nУслуга: {s['name']}\nДата: {context.user_data['selected_date']} {context.user_data['selected_time']}"
    kb = [[KeyboardButton("✅ Подтвердить"), KeyboardButton("❌ Отменить")]]
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return CONFIRMATION

async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Отменить": return await cancel(update, context)
    
    s = context.user_data['selected_service']
    d_str, t_str = context.user_data['selected_date'], context.user_data['selected_time']
    
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute('''INSERT INTO bookings (user_id, username, first_name, service, booking_date, booking_time, duration, created_at)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                   (update.effective_user.id, update.effective_user.username, context.user_data['client_name'], 
                    s['name'], d_str, t_str, s['duration'], datetime.now(MOSCOW_TZ).isoformat()))
    conn.commit()
    bid = cursor.lastrowid
    conn.close()

    await notify_admin_new_booking(context, {'id': bid, 'name': context.user_data['client_name'], 'service': s['name'], 'date': d_str, 'time': t_str, 'username': update.effective_user.username})
    await update.message.reply_text(f"✅ Запись №{bid} создана!", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📅 Записаться на сеанс")]], resize_keyboard=True))
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
            CONFIRMATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_booking)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(MessageHandler(filters.Regex("^🛠 Админ-панель$"), admin_menu))
    app.add_handler(CallbackQueryHandler(process_admin_query))
    app.add_handler(MessageHandler(filters.PHOTO & filters.Chat(ADMIN_ID), handle_admin_photo))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_customer_review), group=-1)
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Regex("^📋 Мои записи$"), view_bookings))

    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
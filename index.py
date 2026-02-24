import logging
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import sqlite3
import pytz
from aiogram import Bot, dispatcher
from aiogram.types import message

# Токен вашего бота
TOKEN = "8364305489:AAGaNNx1lc77a43Z41QGCXXfLxOi1OeVQy4"

# Состояния для ConversationHandler
SELECT_SERVICE, SELECT_DATE, SELECT_TIME, ENTER_NAME, CONFIRMATION = range(5)

dp = dispatcher

# Включаем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Часовой пояс (измените на ваш)
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            service TEXT,
            booking_date TEXT,
            booking_time TEXT,
            created_at TEXT,
            status TEXT DEFAULT 'pending'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            duration INTEGER, -- в минутах
            price INTEGER
        )
    ''')
    
    # Добавляем базовые услуги, если их нет
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

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📅 Записаться на сеанс")],
        [KeyboardButton("📋 Мои записи"), KeyboardButton("ℹ️ Помощь")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "👋 Добро пожаловать! Я помогу вам записаться на сеанс.\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

# Начало записи
async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Получаем список услуг из базы
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, duration, price FROM services")
    services = cursor.fetchall()
    conn.close()
    
    # Создаем кнопки с услугами
    keyboard = []
    for service_id, name, duration, price in services:
        button_text = f"{name} - {price}₽"
        keyboard.append([KeyboardButton(button_text)])
    
    keyboard.append([KeyboardButton("❌ Отмена")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['services'] = services
    await update.message.reply_text(
        "Выберите услугу:",
        reply_markup=reply_markup
    )
    
    return SELECT_SERVICE

# Выбор услуги
async def select_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_choice = update.message.text
    
    if user_choice == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    # Ищем выбранную услугу
    for service_id, name, duration, price in context.user_data['services']:
        if f"{name} - {price}₽" in user_choice:
            context.user_data['selected_service'] = {
                'id': service_id,
                'name': name,
                'duration': duration,
                'price': price
            }
            break
    
    # Создаем кнопки с датами (следующие 7 дней)
    today = datetime.now(MOSCOW_TZ)
    keyboard = []
    
    for i in range(7):
        date = today + timedelta(days=i)
        if date.weekday() < 5:  # Только будни (0-4)
            date_str = date.strftime("%d.%m.%Y (%A)")
            keyboard.append([KeyboardButton(date_str)])
    
    keyboard.append([KeyboardButton("❌ Отмена")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Вы выбрали: {context.user_data['selected_service']['name']}\n"
        "Выберите дату:",
        reply_markup=reply_markup
    )
    
    return SELECT_DATE

# Выбор даты
async def select_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_choice = update.message.text
    
    if user_choice == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    context.user_data['selected_date'] = user_choice
    
    # Создаем кнопки с временем
    keyboard = []
    times = ["09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00", "18:00"]
    
    for i in range(0, len(times), 3):
        row = []
        for j in range(3):
            if i + j < len(times):
                row.append(KeyboardButton(times[i + j]))
        keyboard.append(row)
    
    keyboard.append([KeyboardButton("❌ Отмена")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Дата: {user_choice}\n"
        "Выберите время:",
        reply_markup=reply_markup
    )
    
    return SELECT_TIME

# Выбор времени
async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_choice = update.message.text
    
    if user_choice == "❌ Отмена":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    context.user_data['selected_time'] = user_choice
    
    await update.message.reply_text(
        f"Отлично! \n"
        f"Услуга: {context.user_data['selected_service']['name']}\n"
        f"Дата: {context.user_data['selected_date']}\n"
        f"Время: {user_choice}\n\n"
        f"Теперь введите ваше имя и фамилию:",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return ENTER_NAME

# Ввод имени
async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['client_name'] = update.message.text
    
    # Подтверждение
    keyboard = [
        [KeyboardButton("✅ Подтвердить запись"), KeyboardButton("❌ Отменить")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📋 Проверьте данные:\n\n"
        f"👤 Имя: {context.user_data['client_name']}\n"
        f"📅 Услуга: {context.user_data['selected_service']['name']}\n"
        f"📅 Дата: {context.user_data['selected_date']}\n"
        f"⏰ Время: {context.user_data['selected_time']}\n"
        f"💰 Стоимость: {context.user_data['selected_service']['price']}₽\n\n"
        f"Всё верно?",
        reply_markup=reply_markup
    )
    
    return CONFIRMATION

# Подтверждение записи
async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_choice = update.message.text
    
    if user_choice == "❌ Отменить":
        await update.message.reply_text("Запись отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    # Сохраняем запись в базу данных
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO bookings 
        (user_id, username, first_name, service, booking_date, booking_time, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        update.effective_user.id,
        update.effective_user.username,
        context.user_data['client_name'],
        context.user_data['selected_service']['name'],
        context.user_data['selected_date'],
        context.user_data['selected_time'],
        datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    ))
    
    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()
    
    # Возвращаем главное меню
    keyboard = [
        [KeyboardButton("📅 Записаться на сеанс")],
        [KeyboardButton("📋 Мои записи"), KeyboardButton("ℹ️ Помощь")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"✅ Запись #{booking_id} успешно создана!\n\n"
        f"Мы ждём вас {context.user_data['selected_date']} в {context.user_data['selected_time']}.\n"
        f"Услуга: {context.user_data['selected_service']['name']}\n"
        f"Стоимость: {context.user_data['selected_service']['price']}₽\n\n"
        f"Для отмены или переноса свяжитесь с администратором.",
        reply_markup=reply_markup
    )
    
    # Очищаем временные данные
    context.user_data.clear()
    
    return ConversationHandler.END

# Просмотр своих записей
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
    
    bookings = cursor.fetchall()
    conn.close()
    
    if not bookings:
        await update.message.reply_text("У вас нет активных записей.")
        return
    
    response = "📋 Ваши последние записи:\n\n"
    for booking in bookings:
        booking_id, service, date, time, created_at, status = booking
        status_emoji = "✅" if status == 'confirmed' else "⏳" if status == 'pending' else "❌"
        response += f"{status_emoji} #{booking_id}\n"
        response += f"   Услуга: {service}\n"
        response += f"   Дата: {date}\n"
        response += f"   Время: {time}\n"
        response += f"   Статус: {status}\n\n"
    
    await update.message.reply_text(response)

# Помощь
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
ℹ️ **Помощь по боту:**

📅 **Записаться на сеанс** - начать процесс записи
📋 **Мои записи** - просмотреть ваши бронирования

⚙️ **Команды:**
/start - главное меню
/cancel - отменить текущее действие
/admin - административные функции (если вы админ)

📞 **Контакты:**
Для срочных вопросов: +7 952 448 3814
    """
    await update.message.reply_text(help_text)

# Отмена
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.clear()
    return ConversationHandler.END

# Основная функция
def main():
    # Инициализируем базу данных
    init_db()
    print("📁 База данных инициализирована")
    
    # Создаём приложение
    app = Application.builder().token(TOKEN).build()
    
    # Создаём ConversationHandler для записи
    conv_handler = ConversationHandler(
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
    
    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex("^📋 Мои записи$"), view_bookings))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Помощь$"), help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # Запускаем бота
    print("🚀 Бот запущен! Нажмите Ctrl+C для остановки")
    app.run_polling()

if __name__ == "__main__":
    main()
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import ADMIN_ID

# Проверка на админа
def is_admin(user_id):
    return user_id == ADMIN_ID

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    keyboard = [
        [InlineKeyboardButton("📩 Новые заявки", callback_data="admin_view_pending")],
        [InlineKeyboardButton("📅 Все записи", callback_data="admin_view_all")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠 Панель администратора:", reply_markup=reply_markup)

async def process_admin_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()

    if data.startswith("admin_view"):
        status_filter = "WHERE status = 'pending'" if "pending" in data else ""
        cursor.execute(f"SELECT id, first_name, service, booking_date, booking_time, username FROM bookings {status_filter} ORDER BY id DESC LIMIT 10")
        rows = cursor.fetchall()
        
        if not rows:
            await query.edit_message_text("Записей не найдено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="admin_main")]]))
        else:
            for row in rows:
                bid, name, serv, date, time, user = row
                msg = f"🆔 Запись №{bid}\n👤 Клиент: {name}\n🛠 Услуга: {serv}\n📅 {date} в {time}\n🔗 Контакт: @{user if user else 'скрыт'}"
                
                kb = [
                    [InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{bid}"),
                     InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{bid}")]
                ]
                await context.bot.send_message(chat_id=ADMIN_ID, text=msg, reply_markup=InlineKeyboardMarkup(kb))
    
    elif data.startswith("confirm_"):
        bid = data.split("_")[1]
        cursor.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (bid,))
        conn.commit()
        await query.edit_message_text(f"✅ Запись №{bid} подтверждена!")

    elif data.startswith("delete_"):
        bid = data.split("_")[1]
        cursor.execute("DELETE FROM bookings WHERE id = ?", (bid,))
        conn.commit()
        await query.edit_message_text(f"🗑 Запись №{bid} удалена.")

    conn.close()
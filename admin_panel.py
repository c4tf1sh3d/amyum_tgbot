import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import ADMIN_ID

def is_admin(user_id):
    return user_id == ADMIN_ID

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    keyboard = [
        [InlineKeyboardButton("⏳ Активные записи", callback_data="adm_list_active")],
        [InlineKeyboardButton("✅ Архив (Выполнено)", callback_data="adm_list_archive")],
        [InlineKeyboardButton("❌ Отказы", callback_data="adm_list_cancelled")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📂 Управление записями:", reply_markup=reply_markup)

async def process_admin_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()

    # 1. Просмотр списков по категориям
    if data.startswith("adm_list_"):
        status_type = data.split("_")[2]
        # Маппинг для БД
        db_status = 'confirmed' if status_type == 'active' else ('completed' if status_type == 'archive' else 'cancelled')
        
        cursor.execute("SELECT id, first_name, service, booking_date, booking_time, photo_file_id FROM bookings WHERE status = ? ORDER BY id DESC LIMIT 10", (db_status,))
        rows = cursor.fetchall()
        
        if not rows:
            await query.message.reply_text(f"Список '{status_type}' пуст.")
        else:
            for row in rows:
                bid, name, serv, date, time, photo = row
                text = f"🆔 №{bid} | {name}\n🛠 {serv}\n📅 {date} {time}\nСтатус: {db_status}"
                
                kb = []
                if db_status != 'completed':
                    kb.append([InlineKeyboardButton("✅ Выполнено", callback_data=f"st_completed_{bid}")])
                if db_status != 'cancelled':
                    kb.append([InlineKeyboardButton("❌ Отказ", callback_data=f"st_cancelled_{bid}")])
                
                kb.append([InlineKeyboardButton("📸 Добавить фото", callback_data=f"st_photo_{bid}")])
                
                if photo:
                    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await context.bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb))

    # 2. Смена статуса на "Подтверждено" (из уведомления)
    elif data.startswith("st_confirmed_"):
        bid = data.split("_")[2]
        cursor.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (bid,))
        conn.commit()
        await query.edit_message_text(f"✅ Запись №{bid} подтверждена и перенесена в Активные.")

    # 3. Смена статуса на "Выполнено" + Запрос отзыва у клиента
    elif data.startswith("st_completed_"):
        bid = data.split("_")[2]
        cursor.execute("SELECT user_id, service, photo_file_id FROM bookings WHERE id = ?", (bid,))
        res = cursor.fetchone()
        
        if res:
            u_id, s_name, a_photo = res
            cursor.execute("UPDATE bookings SET status = 'completed' WHERE id = ?", (bid,))
            conn.commit()
            
            feedback_msg = f"✨ Ваша запись «{s_name}» завершена! Пожалуйста, оставьте отзыв или пришлите фото результата."
            try:
                if a_photo:
                    await context.bot.send_photo(chat_id=u_id, photo=a_photo, caption=feedback_msg)
                else:
                    await context.bot.send_message(chat_id=u_id, text=feedback_msg)
                # Ставим метку, что ждем отзыв
                context.application.user_data[u_id] = {'awaiting_review_bid': bid}
            except:
                pass
            
            await query.message.reply_text(f"✅ №{bid} выполнена. Клиенту отправлен запрос отзыва.")

    # 4. Отказ
    elif data.startswith("st_cancelled_"):
        bid = data.split("_")[2]
        cursor.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (bid,))
        conn.commit()
        await query.message.reply_text(f"❌ Запись №{bid} отменена.")

    # 5. Подготовка к загрузке фото
    elif data.startswith("st_photo_"):
        bid = data.split("_")[2]
        context.user_data['upload_photo_bid'] = bid
        await query.message.reply_text(f"📸 Отправьте фото для записи №{bid}")

    conn.close()

async def notify_admin_new_booking(context: ContextTypes.DEFAULT_TYPE, booking_data: dict):
    bid = booking_data.get('id')
    msg = (f"🔔 **Новая заявка №{bid}**\n👤 {booking_data.get('name')}\n"
           f"🛠 {booking_data.get('service')}\n📅 {booking_data.get('date')} {booking_data.get('time')}\n"
           f"🔗 @{booking_data.get('username', 'скрыт')}")
    
    kb = [[InlineKeyboardButton("✅ Подтвердить", callback_data=f"st_confirmed_{bid}"),
           InlineKeyboardButton("❌ Отказ", callback_data=f"st_cancelled_{bid}")]]
    
    await context.bot.send_message(chat_id=ADMIN_ID, text=msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
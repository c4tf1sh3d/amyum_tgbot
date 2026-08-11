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
        [InlineKeyboardButton("❌ Отказы", callback_data="adm_list_cancelled")],
        [InlineKeyboardButton("👥 Клиенты", callback_data="adm_clients")],
        [InlineKeyboardButton("🔎 Найти клиента (ID/тел./имя)", callback_data="adm_client_search")],
        [InlineKeyboardButton("🔗 Объединить клиентов", callback_data="adm_merge_start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📂 Управление записями:", reply_markup=reply_markup)

# --- КЛИЕНТЫ: карточка, поиск, редактирование, объединение ---

def resolve_client_uid(cursor, query_text: str):
    """Ищет user_id клиента по токену, телефону или имени (частичное совпадение)."""
    cursor.execute('''
        SELECT user_id FROM clients
        WHERE token = ? OR phone LIKE ? OR first_name LIKE ?
        LIMIT 1
    ''', (query_text, f"%{query_text}%", f"%{query_text}%"))
    row = cursor.fetchone()
    return row[0] if row else None

def build_client_card(cursor, user_id) -> str:
    """Текстовая карточка клиента: контакты, токен, сумма чека, история визитов."""
    cursor.execute("SELECT token, phone, first_name FROM clients WHERE user_id = ?", (user_id,))
    client_row = cursor.fetchone()
    if not client_row:
        return None
    token, phone, name = client_row

    cursor.execute('''
        SELECT COALESCE(SUM(s.price), 0)
        FROM bookings b JOIN services s ON b.service = s.name
        WHERE b.user_id = ? AND b.status = 'completed'
    ''', (user_id,))
    total_spent = cursor.fetchone()[0]

    cursor.execute("SELECT id, service, booking_date, booking_time, status FROM bookings WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user_id,))
    visits = cursor.fetchall()

    status_emoji = {'confirmed': '⏳', 'completed': '✅', 'cancelled': '❌', 'pending': '🕓'}
    lines = [
        f"👤 {name}",
        f"📞 {phone or 'не указан'}",
        f"🪪 ID клиента: {token}",
        f"💰 Общий чек (завершено): {total_spent} ₽",
        f"📋 Всего визитов: {len(visits)}",
        ""
    ]
    for vid, serv, date, time, status in visits:
        lines.append(f"{status_emoji.get(status, '•')} №{vid} {serv} — {date} {time}")

    return "\n".join(lines)

def build_client_keyboard(user_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Имя", callback_data=f"cl_edit_name_{user_id}"),
         InlineKeyboardButton("📞 Телефон", callback_data=f"cl_edit_phone_{user_id}")],
        [InlineKeyboardButton("🔗 Объединить с другим клиентом", callback_data=f"cl_merge_{user_id}")]
    ])

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единая точка входа для многошаговых сценариев админа: редактирование, объединение, поиск."""
    if not is_admin(update.effective_user.id):
        return

    editing = context.user_data.get('admin_editing_client')
    if editing:
        await _process_client_edit(update, context, editing)
        return

    merge_step = context.user_data.get('admin_merge_step')
    if merge_step == 'first':
        await _process_merge_pick_target(update, context)
        return
    if merge_step == 'second':
        await _process_client_merge(update, context)
        return

    if context.user_data.get('admin_awaiting_client_search'):
        await _process_client_search(update, context)
        return

async def _process_client_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('admin_awaiting_client_search', None)
    query_text = update.message.text.strip()

    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    uid = resolve_client_uid(cursor, query_text)

    if not uid:
        conn.close()
        await update.message.reply_text("Клиент не найден. Проверьте ID, номер телефона или имя и попробуйте снова.")
        return

    card = build_client_card(cursor, uid)
    conn.close()
    await update.message.reply_text(card, reply_markup=build_client_keyboard(uid))

async def _process_client_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, editing: dict):
    context.user_data.pop('admin_editing_client', None)
    uid, field = editing['uid'], editing['field']
    new_value = update.message.text.strip()

    if field == 'phone':
        digits = "".join(ch for ch in new_value if ch.isdigit())
        if len(digits) < 10:
            await update.message.reply_text("Похоже, номер введён некорректно. Изменения не сохранены, попробуйте ещё раз через карточку клиента.")
            return

    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    # 'phone' и 'first_name' — общие названия колонок и в clients, и в bookings
    cursor.execute(f"UPDATE clients SET {field} = ? WHERE user_id = ?", (new_value, uid))
    cursor.execute(f"UPDATE bookings SET {field} = ? WHERE user_id = ?", (new_value, uid))
    conn.commit()

    card = build_client_card(cursor, uid)
    conn.close()
    field_label = "Телефон" if field == 'phone' else "Имя"
    await update.message.reply_text(f"✅ {field_label} обновлён(о).\n\n{card}", reply_markup=build_client_keyboard(uid))

async def _process_merge_pick_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text.strip()
    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    uid = resolve_client_uid(cursor, query_text)
    conn.close()

    if not uid:
        context.user_data.pop('admin_merge_step', None)
        await update.message.reply_text("Клиент не найден. Объединение отменено.")
        return

    context.user_data['admin_merge_target_uid'] = uid
    context.user_data['admin_merge_step'] = 'second'
    await update.message.reply_text(
        "Теперь введите ID, телефон или имя ВТОРОГО клиента — его записи перенесутся в первый профиль, "
        "а второй профиль будет удалён:"
    )

async def _process_client_merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_uid = context.user_data.get('admin_merge_target_uid')
    context.user_data.pop('admin_merge_step', None)
    context.user_data.pop('admin_merge_target_uid', None)
    query_text = update.message.text.strip()

    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()
    source_uid = resolve_client_uid(cursor, query_text)

    if not source_uid:
        conn.close()
        await update.message.reply_text("Второй клиент не найден. Объединение отменено.")
        return
    if str(source_uid) == str(target_uid):
        conn.close()
        await update.message.reply_text("Это один и тот же клиент. Объединение отменено.")
        return

    cursor.execute("SELECT token, phone FROM clients WHERE user_id = ?", (target_uid,))
    target = cursor.fetchone()
    cursor.execute("SELECT token, phone FROM clients WHERE user_id = ?", (source_uid,))
    source = cursor.fetchone()
    if not target or not source:
        conn.close()
        await update.message.reply_text("Не удалось найти обоих клиентов. Объединение отменено.")
        return

    target_token, target_phone = target
    source_token, source_phone = source

    # Переносим все записи (визиты) второго клиента на профиль первого
    cursor.execute("UPDATE bookings SET user_id = ?, client_token = ? WHERE user_id = ?", (target_uid, target_token, source_uid))
    # Если у целевого клиента не был указан телефон — берём от объединяемого
    if not target_phone and source_phone:
        cursor.execute("UPDATE clients SET phone = ? WHERE user_id = ?", (source_phone, target_uid))
    cursor.execute("DELETE FROM clients WHERE user_id = ?", (source_uid,))
    conn.commit()

    card = build_client_card(cursor, target_uid)
    conn.close()
    await update.message.reply_text(
        f"🔗 Объединено: записи клиента {source_token} перенесены в карточку {target_token}, профиль {source_token} удалён.\n"
        f"⚠️ Уведомления и запросы отзывов по этим записям теперь будут приходить в Telegram-чат, привязанный к {target_token}.\n\n{card}",
        reply_markup=build_client_keyboard(target_uid)
    )

async def process_admin_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    conn = sqlite3.connect('bookings.db')
    cursor = conn.cursor()

    # 1. Просмотр списков по категориям
    if data.startswith("adm_list_"):
        status_type = data.split("_")[2]
        db_status = 'confirmed' if status_type == 'active' else ('completed' if status_type == 'archive' else 'cancelled')
        
        cursor.execute('''
            SELECT b.id, b.first_name, b.service, b.booking_date, b.booking_time, b.photo_file_id, b.phone, c.token
            FROM bookings b LEFT JOIN clients c ON b.user_id = c.user_id
            WHERE b.status = ? ORDER BY b.id DESC LIMIT 10
        ''', (db_status,))
        rows = cursor.fetchall()
        
        if not rows:
            await query.message.reply_text(f"Список '{status_type}' пуст.")
        else:
            for row in rows:
                bid, name, serv, date, time, photo, phone, token = row
                phone_line = f"\n📞 {phone}" if phone else "\n📞 не указан"
                token_line = f"\n🪪 {token}" if token else ""
                text = f"🆔 №{bid} | {name}{phone_line}{token_line}\n🛠 {serv}\n📅 {date} {time}\nСтатус: {db_status}"
                
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

    # 3. Ручная отметка "Выполнено". Запрос отзыва клиенту приходит автоматически через
    #    24ч после времени записи (см. schedule_review_job в index.py) — здесь только статус.
    elif data.startswith("st_completed_"):
        bid = data.split("_")[2]
        cursor.execute("UPDATE bookings SET status = 'completed' WHERE id = ?", (bid,))
        conn.commit()
        await query.message.reply_text(
            f"✅ №{bid} отмечена выполненной. Через 24ч после времени записи клиенту автоматически придёт запрос отзыва."
        )

    # 4. Список клиентов (кликабельно, последние по активности)
    elif data == "adm_clients":
        cursor.execute('''
            SELECT c.user_id, c.first_name, c.token, COUNT(b.id) AS visits, MAX(b.id) AS last_id
            FROM clients c LEFT JOIN bookings b ON b.user_id = c.user_id
            GROUP BY c.user_id
            ORDER BY last_id DESC
            LIMIT 15
        ''')
        clients = cursor.fetchall()
        if not clients:
            await query.message.reply_text("Клиентов пока нет.")
        else:
            kb = [[InlineKeyboardButton(f"{name or 'Без имени'} · {token} ({visits} визитов)", callback_data=f"cl_view_{uid}")]
                  for uid, name, token, visits, _ in clients]
            await query.message.reply_text("👥 Клиенты (последние по активности):", reply_markup=InlineKeyboardMarkup(kb))

    # 5. Карточка клиента: контакты, ID-токен, все визиты, общий чек + кнопки редактирования
    elif data.startswith("cl_view_"):
        uid = data.split("_", 2)[2]
        card = build_client_card(cursor, uid)
        if card:
            await query.message.reply_text(card, reply_markup=build_client_keyboard(uid))
        else:
            await query.message.reply_text("Клиент не найден.")

    # 5a. Редактирование имени
    elif data.startswith("cl_edit_name_"):
        uid = data[len("cl_edit_name_"):]
        context.user_data['admin_editing_client'] = {'uid': uid, 'field': 'first_name'}
        await query.message.reply_text("Введите новое имя клиента:")

    # 5b. Редактирование телефона
    elif data.startswith("cl_edit_phone_"):
        uid = data[len("cl_edit_phone_"):]
        context.user_data['admin_editing_client'] = {'uid': uid, 'field': 'phone'}
        await query.message.reply_text("Введите новый номер телефона клиента:")

    # 5c. Объединение, начатое из карточки конкретного клиента
    elif data.startswith("cl_merge_"):
        uid = data[len("cl_merge_"):]
        context.user_data['admin_merge_target_uid'] = uid
        context.user_data['admin_merge_step'] = 'second'
        await query.message.reply_text(
            "Введите ID, телефон или имя ВТОРОГО клиента — его записи перенесутся в этот профиль, "
            "а второй профиль будет удалён:"
        )

    # 6. Запрос на поиск клиента по ID/телефону/имени
    elif data == "adm_client_search":
        context.user_data['admin_awaiting_client_search'] = True
        await query.message.reply_text("Введите ID клиента (например CL-A1B2C3), номер телефона или имя для поиска:")

    # 6b. Объединение, начатое из главного меню
    elif data == "adm_merge_start":
        context.user_data['admin_merge_step'] = 'first'
        await query.message.reply_text("Введите ID, телефон или имя ПЕРВОГО клиента (профиль, который останется):")

    # 7. Отказ
    elif data.startswith("st_cancelled_"):
        bid = data.split("_")[2]
        cursor.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (bid,))
        conn.commit()
        await query.message.reply_text(f"❌ Запись №{bid} отменена.")

    # 8. Подготовка к загрузке фото
    elif data.startswith("st_photo_"):
        bid = data.split("_")[2]
        context.user_data['upload_photo_bid'] = bid
        await query.message.reply_text(f"📸 Отправьте фото для записи №{bid}")

    conn.close()

async def notify_admin_new_booking(context: ContextTypes.DEFAULT_TYPE, booking_data: dict):
    bid = booking_data.get('id')
    phone = booking_data.get('phone') or 'не указан'
    token = booking_data.get('token') or '—'
    msg = (f"🔔 **Новая заявка №{bid}**\n👤 {booking_data.get('name')}\n"
           f"📞 {phone}\n🪪 {token}\n"
           f"🛠 {booking_data.get('service')}\n📅 {booking_data.get('date')} {booking_data.get('time')}\n"
           f"🔗 @{booking_data.get('username', 'скрыт')}")
    
    kb = [[InlineKeyboardButton("✅ Подтвердить", callback_data=f"st_confirmed_{bid}"),
           InlineKeyboardButton("❌ Отказ", callback_data=f"st_cancelled_{bid}")]]
    
    await context.bot.send_message(chat_id=ADMIN_ID, text=msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
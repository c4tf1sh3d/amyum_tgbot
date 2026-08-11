import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import ADMIN_ID

PAGE_SIZE = 10

def is_admin(user_id):
    return user_id == ADMIN_ID

def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ Активные записи", callback_data="adm_list_active_0")],
        [InlineKeyboardButton("✅ Архив (Выполнено)", callback_data="adm_list_archive_0")],
        [InlineKeyboardButton("❌ Отказы", callback_data="adm_list_cancelled_0")],
        [InlineKeyboardButton("👥 Клиенты", callback_data="adm_clients_0")],
        [InlineKeyboardButton("🔎 Найти клиента (ID/тел./имя)", callback_data="adm_client_search")],
        [InlineKeyboardButton("🔗 Объединить клиентов", callback_data="adm_merge_start")]
    ])

BACK_BUTTON = InlineKeyboardButton("⬅️ В меню", callback_data="adm_back")
CANCEL_KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="adm_cancel_flow")]])

def with_back(extra_rows=None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append([BACK_BUTTON])
    return InlineKeyboardMarkup(rows)

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("📂 Управление записями:", reply_markup=admin_menu_keyboard())

async def _clear_admin_flow(context: ContextTypes.DEFAULT_TYPE):
    for key in ('admin_editing_client', 'admin_merge_step', 'admin_merge_target_uid', 'admin_awaiting_client_search', 'upload_photo_bid'):
        context.user_data.pop(key, None)

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
        [InlineKeyboardButton("🔗 Объединить с другим клиентом", callback_data=f"cl_merge_{user_id}")],
        [BACK_BUTTON]
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
        await update.message.reply_text("Клиент не найден. Проверьте ID, номер телефона или имя и попробуйте снова.", reply_markup=with_back())
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
            await update.message.reply_text(
                "Похоже, номер введён некорректно. Изменения не сохранены, попробуйте ещё раз через карточку клиента.",
                reply_markup=with_back()
            )
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
        await update.message.reply_text("Клиент не найден. Объединение отменено.", reply_markup=with_back())
        return

    context.user_data['admin_merge_target_uid'] = uid
    context.user_data['admin_merge_step'] = 'second'
    await update.message.reply_text(
        "Теперь введите ID, телефон или имя ВТОРОГО клиента — его записи перенесутся в первый профиль, "
        "а второй профиль будет удалён:",
        reply_markup=CANCEL_KEYBOARD
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
        await update.message.reply_text("Второй клиент не найден. Объединение отменено.", reply_markup=with_back())
        return
    if str(source_uid) == str(target_uid):
        conn.close()
        await update.message.reply_text("Это один и тот же клиент. Объединение отменено.", reply_markup=with_back())
        return

    cursor.execute("SELECT token, phone FROM clients WHERE user_id = ?", (target_uid,))
    target = cursor.fetchone()
    cursor.execute("SELECT token, phone FROM clients WHERE user_id = ?", (source_uid,))
    source = cursor.fetchone()
    if not target or not source:
        conn.close()
        await update.message.reply_text("Не удалось найти обоих клиентов. Объединение отменено.", reply_markup=with_back())
        return

    target_token, target_phone = target
    source_token, source_phone = source

    cursor.execute("UPDATE bookings SET user_id = ?, client_token = ? WHERE user_id = ?", (target_uid, target_token, source_uid))
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

    # 0. Навигация: в меню / отмена текущего сценария
    if data == "adm_back":
        await _clear_admin_flow(context)
        await query.message.reply_text("📂 Управление записями:", reply_markup=admin_menu_keyboard())

    elif data == "adm_cancel_flow":
        await _clear_admin_flow(context)
        await query.message.reply_text("Отменено.", reply_markup=admin_menu_keyboard())

    # 1. Просмотр списков по категориям (с пагинацией)
    elif data.startswith("adm_list_"):
        parts = data.split("_")
        status_type = parts[2]
        offset = int(parts[3]) if len(parts) > 3 else 0
        db_status = 'confirmed' if status_type == 'active' else ('completed' if status_type == 'archive' else 'cancelled')

        cursor.execute('''
            SELECT b.id, b.first_name, b.service, b.booking_date, b.booking_time, b.photo_file_id, b.phone, c.token
            FROM bookings b LEFT JOIN clients c ON b.user_id = c.user_id
            WHERE b.status = ? ORDER BY b.id DESC LIMIT ? OFFSET ?
        ''', (db_status, PAGE_SIZE, offset))
        rows = cursor.fetchall()

        if not rows:
            msg = f"Список '{status_type}' пуст." if offset == 0 else "Больше записей нет."
            nav = [[InlineKeyboardButton("◀️ Назад", callback_data=f"adm_list_{status_type}_{max(offset - PAGE_SIZE, 0)}")]] if offset > 0 else []
            await query.message.reply_text(msg, reply_markup=with_back(nav))
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

            nav_row = []
            if offset > 0:
                nav_row.append(InlineKeyboardButton("◀️ Назад", callback_data=f"adm_list_{status_type}_{max(offset - PAGE_SIZE, 0)}"))
            if len(rows) == PAGE_SIZE:
                nav_row.append(InlineKeyboardButton("Дальше ▶️", callback_data=f"adm_list_{status_type}_{offset + PAGE_SIZE}"))
            nav = [nav_row] if nav_row else []
            await query.message.reply_text(f"Показаны записи {offset + 1}–{offset + len(rows)}.", reply_markup=with_back(nav))

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
            f"✅ №{bid} отмечена выполненной. Через 24ч после времени записи клиенту автоматически придёт запрос отзыва.",
            reply_markup=with_back()
        )

    # 4. Список клиентов (кликабельно, с пагинацией)
    elif data.startswith("adm_clients_"):
        offset = int(data.split("_")[2])
        cursor.execute('''
            SELECT c.user_id, c.first_name, c.token, COUNT(b.id) AS visits, MAX(b.id) AS last_id
            FROM clients c LEFT JOIN bookings b ON b.user_id = c.user_id
            GROUP BY c.user_id
            ORDER BY last_id DESC
            LIMIT ? OFFSET ?
        ''', (PAGE_SIZE, offset))
        clients = cursor.fetchall()

        if not clients:
            msg = "Клиентов пока нет." if offset == 0 else "Больше клиентов нет."
            nav = [[InlineKeyboardButton("◀️ Назад", callback_data=f"adm_clients_{max(offset - PAGE_SIZE, 0)}")]] if offset > 0 else []
            await query.message.reply_text(msg, reply_markup=with_back(nav))
        else:
            kb = [[InlineKeyboardButton(f"{name or 'Без имени'} · {token} ({visits} визитов)", callback_data=f"cl_view_{uid}")]
                  for uid, name, token, visits, _ in clients]
            nav_row = []
            if offset > 0:
                nav_row.append(InlineKeyboardButton("◀️ Назад", callback_data=f"adm_clients_{max(offset - PAGE_SIZE, 0)}"))
            if len(clients) == PAGE_SIZE:
                nav_row.append(InlineKeyboardButton("Дальше ▶️", callback_data=f"adm_clients_{offset + PAGE_SIZE}"))
            if nav_row:
                kb.append(nav_row)
            kb.append([BACK_BUTTON])
            await query.message.reply_text("👥 Клиенты (последние по активности):", reply_markup=InlineKeyboardMarkup(kb))

    # 5. Карточка клиента: контакты, ID-токен, все визиты, общий чек + кнопки редактирования
    elif data.startswith("cl_view_"):
        uid = data.split("_", 2)[2]
        card = build_client_card(cursor, uid)
        if card:
            await query.message.reply_text(card, reply_markup=build_client_keyboard(uid))
        else:
            await query.message.reply_text("Клиент не найден.", reply_markup=with_back())

    # 5a. Редактирование имени
    elif data.startswith("cl_edit_name_"):
        uid = data[len("cl_edit_name_"):]
        context.user_data['admin_editing_client'] = {'uid': uid, 'field': 'first_name'}
        await query.message.reply_text("Введите новое имя клиента:", reply_markup=CANCEL_KEYBOARD)

    # 5b. Редактирование телефона
    elif data.startswith("cl_edit_phone_"):
        uid = data[len("cl_edit_phone_"):]
        context.user_data['admin_editing_client'] = {'uid': uid, 'field': 'phone'}
        await query.message.reply_text("Введите новый номер телефона клиента:", reply_markup=CANCEL_KEYBOARD)

    # 5c. Объединение, начатое из карточки конкретного клиента
    elif data.startswith("cl_merge_"):
        uid = data[len("cl_merge_"):]
        context.user_data['admin_merge_target_uid'] = uid
        context.user_data['admin_merge_step'] = 'second'
        await query.message.reply_text(
            "Введите ID, телефон или имя ВТОРОГО клиента — его записи перенесутся в этот профиль, "
            "а второй профиль будет удалён:",
            reply_markup=CANCEL_KEYBOARD
        )

    # 6. Запрос на поиск клиента по ID/телефону/имени
    elif data == "adm_client_search":
        context.user_data['admin_awaiting_client_search'] = True
        await query.message.reply_text("Введите ID клиента (например CL-A1B2C3), номер телефона или имя для поиска:", reply_markup=CANCEL_KEYBOARD)

    # 6b. Объединение, начатое из главного меню
    elif data == "adm_merge_start":
        context.user_data['admin_merge_step'] = 'first'
        await query.message.reply_text("Введите ID, телефон или имя ПЕРВОГО клиента (профиль, который останется):", reply_markup=CANCEL_KEYBOARD)

    # 7. Отказ
    elif data.startswith("st_cancelled_"):
        bid = data.split("_")[2]
        cursor.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (bid,))
        conn.commit()
        await query.message.reply_text(f"❌ Запись №{bid} отменена.", reply_markup=with_back())

    # 8. Подготовка к загрузке фото
    elif data.startswith("st_photo_"):
        bid = data.split("_")[2]
        context.user_data['upload_photo_bid'] = bid
        await query.message.reply_text(f"📸 Отправьте фото для записи №{bid}", reply_markup=CANCEL_KEYBOARD)

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
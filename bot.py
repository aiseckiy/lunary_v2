import re
import logging
from ai_engine import process_ai_message, get_user_log
from kaspi import get_kaspi_orders, format_orders_text, sync_kaspi_products
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler,
    ConversationHandler
)
from sqlalchemy import func
from database import SessionLocal, Movement
import crud

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import os
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Состояния ConversationHandler
(ADD_NAME, ADD_SKU, ADD_CATEGORY, ADD_UNIT, ADD_MIN_STOCK,
 ADD_INITIAL_STOCK, ADD_BARCODE, BARCODE_PRODUCT) = range(8)


def get_db():
    return SessionLocal()


# ══════════════════════════════════════════════════════
# /start — Главное меню
# ══════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📦 Товары по категориям", callback_data="menu:list"),
         InlineKeyboardButton("⚠️ Заканчивается", callback_data="menu:low")],
        [InlineKeyboardButton("➕ Добавить товар", callback_data="menu:add"),
         InlineKeyboardButton("📊 Статистика", callback_data="menu:stats")],
        [InlineKeyboardButton("📋 Все команды", callback_data="menu:help")],
    ]
    await update.message.reply_text(
        "👋 *Lunary OS* — система управления складом\n\nВыбери действие или пиши команду:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ══════════════════════════════════════════════════════
# Утилита: определить бренд по названию
# ══════════════════════════════════════════════════════
BRANDS = ["TYTAN", "AKFIX", "TULEX", "ЭКСПЕРТ"]

def detect_brand(name: str) -> str:
    n = name.upper()
    for b in BRANDS:
        if b in n:
            return b
    return "Другое"


# ══════════════════════════════════════════════════════
# /list — Главное меню фильтрации
# ══════════════════════════════════════════════════════
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    try:
        stocks = crud.get_all_stocks(db)
        if not stocks:
            await _reply(update, "📭 Товаров нет. Добавь командой /add")
            return

        # Считаем категории с количеством
        cat_count: dict = {}
        for s in stocks:
            cat = s["product"].category or "Общее"
            cat_count[cat] = cat_count.get(cat, 0) + 1

        # Считаем бренды
        brand_count: dict = {}
        for s in stocks:
            b = detect_brand(s["product"].name)
            brand_count[b] = brand_count.get(b, 0) + 1

        total = len(stocks)
        low = sum(1 for s in stocks if s["stock"] <= s["product"].min_stock)

        text = (
            f"📦 *Склад: {total} позиций*\n"
            f"{'⚠️ Заканчивается: ' + str(low) + ' позиций' if low else '✅ Все в норме'}\n\n"
            f"Выбери фильтр:"
        )

        # Кнопки категорий
        cat_buttons = []
        for cat, cnt in sorted(cat_count.items(), key=lambda x: -x[1]):
            cat_buttons.append(InlineKeyboardButton(f"{cat} ({cnt})", callback_data=f"cat:{cat}"))

        # Разбиваем по 2 в ряд
        keyboard = [cat_buttons[i:i+2] for i in range(0, len(cat_buttons), 2)]

        # Кнопки брендов
        brand_buttons = []
        for brand, cnt in sorted(brand_count.items(), key=lambda x: -x[1]):
            brand_buttons.append(InlineKeyboardButton(f"{brand} ({cnt})", callback_data=f"brand:{brand}"))
        keyboard.extend([brand_buttons[i:i+2] for i in range(0, len(brand_buttons), 2)])

        # Доп кнопки
        keyboard.append([
            InlineKeyboardButton("🔴 Заканчивается", callback_data="menu:low"),
            InlineKeyboardButton("📊 Статистика", callback_data="menu:stats"),
        ])

        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown",
                                                           reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await msg.reply_text(text, parse_mode="Markdown",
                                 reply_markup=InlineKeyboardMarkup(keyboard))
    finally:
        db.close()


async def _show_filtered(query, db, title: str, stocks: list):
    """Показать отфильтрованный список товаров"""
    if not stocks:
        await query.edit_message_text(f"📭 {title}: ничего не найдено")
        return

    lines = [f"📦 *{title}* ({len(stocks)} позиций):\n"]
    for s in stocks[:40]:
        p, stock = s["product"], s["stock"]
        icon = "🔴" if stock <= p.min_stock else "🟢"
        lines.append(f"{icon} {p.name[:45]}\n    {stock} {p.unit} | `{p.sku}`")

    if len(stocks) > 40:
        lines.append(f"\n_...и ещё {len(stocks) - 40}. Используй /find для поиска_")

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="menu:list")]]
    await query.edit_message_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ══════════════════════════════════════════════════════
# /low — Что заканчивается
# ══════════════════════════════════════════════════════
async def cmd_low(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    try:
        items = crud.get_low_stock_products(db)
        if not items:
            text = "✅ Всё в норме, низких остатков нет"
        else:
            lines = [f"⚠️ *Заканчивается ({len(items)} позиций):*\n"]
            for p, stock in items:
                lines.append(
                    f"🔴 *{p.name}*\n"
                    f"    Остаток: {stock} {p.unit} (мин: {p.min_stock})\n"
                    f"    ❗ Докупи: {max(p.min_stock - stock + 10, 10)} {p.unit}"
                )
            text = "\n".join(lines)
        await _reply(update, text)
    finally:
        db.close()


# ══════════════════════════════════════════════════════
# /stats — Статистика
# ══════════════════════════════════════════════════════
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    try:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0)
        week_start = now - timedelta(days=7)

        today_sales = abs(db.query(func.sum(Movement.quantity)).filter(
            Movement.type == "sale", Movement.created_at >= today_start
        ).scalar() or 0)

        week_sales = abs(db.query(func.sum(Movement.quantity)).filter(
            Movement.type == "sale", Movement.created_at >= week_start
        ).scalar() or 0)

        week_income = db.query(func.sum(Movement.quantity)).filter(
            Movement.type == "income", Movement.created_at >= week_start,
            Movement.note != "initial"
        ).scalar() or 0

        all_stocks = crud.get_all_stocks(db)
        total_items = len(all_stocks)
        low_items = sum(1 for s in all_stocks if s["stock"] <= s["product"].min_stock)
        total_units = sum(s["stock"] for s in all_stocks)

        top_raw = (
            db.query(Movement.product_id, func.sum(Movement.quantity).label("sold"))
            .filter(Movement.type == "sale", Movement.created_at >= week_start)
            .group_by(Movement.product_id)
            .order_by(func.sum(Movement.quantity)).limit(3).all()
        )
        top_lines = []
        for pid, sold in top_raw:
            p = crud.get_product_by_id(pid, db)
            if p:
                top_lines.append(f"  • {p.name}: {abs(sold)} {p.unit}")

        text = (
            f"📊 *Статистика склада*\n\n"
            f"*Сегодня:*\n🛒 Продано: {today_sales} шт\n\n"
            f"*За 7 дней:*\n🛒 Продано: {week_sales} шт\n📦 Поступило: {week_income} шт\n\n"
            f"*Склад сейчас:*\n"
            f"📋 Позиций: {total_items}\n📦 Единиц: {total_units}\n🔴 Заканчивается: {low_items}"
        )
        if top_lines:
            text += "\n\n*Топ продаж (7 дней):*\n" + "\n".join(top_lines)

        await _reply(update, text)
    finally:
        db.close()


# ══════════════════════════════════════════════════════
# /history — История движений
# ══════════════════════════════════════════════════════
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши: `/history герметик белый`", parse_mode="Markdown")
        return

    query = " ".join(context.args)
    db = get_db()
    try:
        products = crud.find_product(query, db)
        if not products:
            await update.message.reply_text(f"❌ *{query}* не найден", parse_mode="Markdown")
            return

        p = products[0]
        movements = crud.get_movements(p.id, db, limit=15)
        stock = crud.get_stock(p.id, db)
        icons = {"income": "📦+", "sale": "🛒-", "writeoff": "🗑-", "return": "↩️+", "adjustment": "✏️"}

        lines = [f"📋 *{p.name}*\nОстаток: *{stock} {p.unit}*\n\n*История:*\n"]
        for m in movements:
            icon = icons.get(m.type, "•")
            sign = "+" if m.quantity > 0 else ""
            date = m.created_at.strftime("%d.%m %H:%M")
            note = f" — {m.note}" if m.note and m.note != "initial" else ""
            lines.append(f"{icon} `{date}` {sign}{m.quantity}{note}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    finally:
        db.close()


# ══════════════════════════════════════════════════════
# /find — Поиск
# ══════════════════════════════════════════════════════
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Напиши: `/find герметик`", parse_mode="Markdown")
        return

    query = " ".join(context.args)
    db = get_db()
    try:
        products = crud.find_product(query, db)
        if not products:
            await update.message.reply_text(f"❌ Ничего не нашёл по *{query}*", parse_mode="Markdown")
            return

        lines = [f"🔍 *{query}* — найдено {len(products)}:\n"]
        keyboard = []
        for p in products[:10]:
            stock = crud.get_stock(p.id, db)
            icon = "🔴" if stock <= p.min_stock else "🟢"
            lines.append(f"{icon} {p.name} — *{stock} {p.unit}*")
            keyboard.append([
                InlineKeyboardButton(f"📋 {p.sku}", callback_data=f"detail:{p.id}"),
                InlineKeyboardButton("🛒-1", callback_data=f"qs:{p.id}"),
                InlineKeyboardButton("📦+1", callback_data=f"qi:{p.id}"),
            ])

        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    finally:
        db.close()


# ══════════════════════════════════════════════════════
# /barcode — Привязать штрихкод
# ══════════════════════════════════════════════════════
async def cmd_barcode_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) >= 2:
        sku, barcode = context.args[0], context.args[1]
        db = get_db()
        try:
            products = crud.find_product(sku, db)
            if not products:
                await update.message.reply_text(f"❌ Артикул `{sku}` не найден", parse_mode="Markdown")
                return ConversationHandler.END
            p = products[0]
            p.barcode = barcode
            db.commit()
            await update.message.reply_text(
                f"✅ Штрихкод привязан!\n*{p.name}*\n`{barcode}`", parse_mode="Markdown"
            )
        finally:
            db.close()
        return ConversationHandler.END

    await update.message.reply_text(
        "Введи *артикул* товара:\nПример: `TYT_SIL_010`\n\n"
        "Или сразу: `/barcode TYT_SIL_010 4607101830123`",
        parse_mode="Markdown"
    )
    return BARCODE_PRODUCT


async def barcode_get_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    try:
        products = crud.find_product(update.message.text.strip(), db)
        if not products:
            await update.message.reply_text("❌ Не нашёл. Попробуй ещё раз или /cancel")
            return BARCODE_PRODUCT
        context.user_data["bc_pid"] = products[0].id
        await update.message.reply_text(
            f"✅ *{products[0].name}*\n\nТеперь введи штрихкод (цифры с упаковки):",
            parse_mode="Markdown"
        )
        return ADD_BARCODE
    finally:
        db.close()


async def barcode_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    try:
        p = crud.get_product_by_id(context.user_data["bc_pid"], db)
        p.barcode = update.message.text.strip()
        db.commit()
        await update.message.reply_text(
            f"✅ Штрихкод сохранён!\n*{p.name}*\n`{p.barcode}`", parse_mode="Markdown"
        )
    finally:
        db.close()
    return ConversationHandler.END


# ══════════════════════════════════════════════════════
# /add — Добавить товар пошагово
# ══════════════════════════════════════════════════════
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    msg = update.message or update.callback_query.message
    await msg.reply_text(
        "➕ *Добавление товара*\n\nШаг 1/6 — Введи *название*:\n_(или /cancel для отмены)_",
        parse_mode="Markdown"
    )
    return ADD_NAME


async def add_get_name(update, context):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ *{context.user_data['name']}*\n\nШаг 2/6 — Введи *артикул*:\nПример: `TYT_SIL_012`",
        parse_mode="Markdown"
    )
    return ADD_SKU


async def add_get_sku(update, context):
    sku = update.message.text.strip().upper().replace(" ", "_")
    db = get_db()
    try:
        from database import Product as _P
        existing = db.query(_P).filter(_P.sku == sku).first()
        if existing:
            await update.message.reply_text(f"⚠️ Артикул `{sku}` занят. Введи другой:", parse_mode="Markdown")
            return ADD_SKU
    finally:
        db.close()

    context.user_data["sku"] = sku
    from telegram import ReplyKeyboardMarkup
    keyboard = ReplyKeyboardMarkup(
        [["Герметики", "Пены монтажные"], ["Дюбели и крепёж", "Инструменты"], ["Химия", "Другое"]],
        one_time_keyboard=True, resize_keyboard=True
    )
    await update.message.reply_text(
        f"✅ `{sku}`\n\nШаг 3/6 — Выбери *категорию*:", parse_mode="Markdown", reply_markup=keyboard
    )
    return ADD_CATEGORY


async def add_get_category(update, context):
    from telegram import ReplyKeyboardMarkup
    context.user_data["category"] = update.message.text.strip()
    keyboard = ReplyKeyboardMarkup([["шт", "кг", "л", "м", "уп"]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(
        f"✅ {context.user_data['category']}\n\nШаг 4/6 — *Единица измерения*:",
        parse_mode="Markdown", reply_markup=keyboard
    )
    return ADD_UNIT


async def add_get_unit(update, context):
    context.user_data["unit"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ {context.user_data['unit']}\n\nШаг 5/6 — *Минимальный остаток* (порог уведомлений):\nПример: `10`",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
    )
    return ADD_MIN_STOCK


async def add_get_min_stock(update, context):
    try:
        context.user_data["min_stock"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Введи число, например `10`", parse_mode="Markdown")
        return ADD_MIN_STOCK
    await update.message.reply_text(
        f"✅ Мин: {context.user_data['min_stock']}\n\nШаг 6/6 — *Текущий остаток* на складе:\nПример: `50`",
        parse_mode="Markdown"
    )
    return ADD_INITIAL_STOCK


async def add_get_initial_stock(update, context):
    try:
        context.user_data["initial_stock"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Введи число, например `50`", parse_mode="Markdown")
        return ADD_INITIAL_STOCK

    d = context.user_data
    await update.message.reply_text(
        f"📋 *Проверь:*\n\n"
        f"Название: {d['name']}\nАртикул: `{d['sku']}`\n"
        f"Категория: {d['category']}\nЕдиница: {d['unit']}\n"
        f"Мин. остаток: {d['min_stock']}\nНачальный остаток: {d['initial_stock']}\n\n"
        "Введи *штрихкод* с упаковки или напиши `нет`:",
        parse_mode="Markdown"
    )
    return ADD_BARCODE


async def add_save(update, context):
    text = update.message.text.strip()
    barcode = None if text.lower() == "нет" else text
    d = context.user_data
    db = get_db()
    try:
        p = crud.create_product(
            name=d["name"], sku=d["sku"], db=db,
            barcode=barcode, category=d["category"],
            unit=d["unit"], min_stock=d["min_stock"]
        )
        crud.set_initial_stock(p.id, d["initial_stock"], db)
        bc = f"\nШтрихкод: `{barcode}`" if barcode else ""
        await update.message.reply_text(
            f"✅ *Товар добавлен!*\n\n*{d['name']}*\n`{d['sku']}`\n"
            f"Остаток: {d['initial_stock']} {d['unit']}{bc}",
            parse_mode="Markdown"
        )
    finally:
        db.close()
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ Отменено", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════
# /help
# ══════════════════════════════════════════════════════
async def cmd_kaspi_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await _reply(update, "⏳ Загружаю заказы Kaspi...")
    result = get_kaspi_orders(state="ACCEPTED")
    if result["error"]:
        text = f"❌ {result['error']}\n\n_Kaspi API работает только с сервера (нужен белый IP)_"
    else:
        text = format_orders_text(result["orders"])
        if result["total"] > 10:
            text += f"\n_Показаны 10 из {result['total']}_"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_kaspi_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Синхронизирую товары с Kaspi...")
    result = sync_kaspi_products()
    await update.message.reply_text(result, parse_mode="Markdown")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    try:
        from database import Movement, Product

        # Фильтр по типу если передан аргумент: /log sale | income | ai
        arg = (context.args[0].lower() if context.args else "all")
        query = db.query(Movement, Product).join(Product, Movement.product_id == Product.id)

        if arg == "sale":
            query = query.filter(Movement.type == "sale")
        elif arg == "income":
            query = query.filter(Movement.type == "income")
        elif arg == "ai":
            query = query.filter(Movement.source == "ai_bot")
        elif arg != "all":
            # Поиск по товару
            products = crud.find_product(arg, db)
            if products:
                pids = [p.id for p in products]
                query = query.filter(Movement.product_id.in_(pids))

        rows = query.order_by(Movement.created_at.desc()).limit(30).all()

        if not rows:
            await _reply(update, "📋 История пуста")
            return

        type_icons = {
            "sale": "🛒", "income": "📦", "writeoff": "🗑",
            "return": "↩️", "adjustment": "✏️"
        }
        source_tag = {"ai_bot": " 🤖", "manual": ""}

        lines = ["📋 *История изменений* (последние 30):\n"]
        prev_date = None
        for m, p in rows:
            date_str = m.created_at.strftime("%d.%m")
            if date_str != prev_date:
                lines.append(f"\n*{date_str}*")
                prev_date = date_str
            icon = type_icons.get(m.type, "•")
            sign = "+" if m.quantity > 0 else ""
            time_str = m.created_at.strftime("%H:%M")
            tag = source_tag.get(m.source, "")
            note = f" _{m.note}_" if m.note and m.note not in ("initial",) else ""
            lines.append(f"`{time_str}` {icon} {sign}{m.quantity} — {p.name}{tag}{note}")

        lines.append(f"\n_Фильтры: /log sale | income | ai | <товар>_")
        await _reply(update, "\n".join(lines))
    finally:
        db.close()


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *Все команды Lunary OS*\n\n"
        "*Остатки:*\n"
        "`остаток <товар>` — узнать остаток\n"
        "`/list` — все товары\n"
        "`/low` — что заканчивается\n"
        "`/find <запрос>` — поиск\n\n"
        "*Движения:*\n"
        "`минус <N> <товар>` — продажа\n"
        "`плюс <N> <товар>` — приход\n"
        "`списать <N> <товар>` — списание\n"
        "`вернуть <N> <товар>` — возврат\n\n"
        "*Товары:*\n"
        "`/add` — добавить товар\n"
        "`/barcode <артикул> <код>` — привязать штрихкод\n"
        "`/history <товар>` — история\n\n"
        "*Аналитика:*\n"
        "`/stats` — статистика\n"
        "`/log` — все последние изменения\n"
        "`/log sale` — только продажи\n"
        "`/log income` — только приходы\n"
        "`/log ai` — только изменения через AI\n"
        "`/log <товар>` — история по товару\n"
        "`/notify` — отчёт по остаткам (что заказывать)\n\n"
        "*Примеры:*\n"
        "`остаток герметик белый`\n"
        "`минус 3 TYT_SIL_009`\n"
        "`плюс 20 герметик черный`\n"
        "`/barcode TYT_SIL_010 4607101830123`"
    )
    await _reply(update, text)


# ══════════════════════════════════════════════════════
# Текстовые сообщения
# ══════════════════════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    text_lower = text.lower()

    # Режим редактирования товара
    if "editing" in context.user_data:
        ed = context.user_data.pop("editing")
        db = get_db()
        try:
            p = crud.get_product_by_id(ed["pid"], db)
            field = ed["field"]
            val = text.strip()
            if field == "min_stock":
                try:
                    val = int(val)
                except ValueError:
                    await update.message.reply_text("❌ Введи число")
                    context.user_data["editing"] = ed
                    return
            elif field == "sku":
                val = val.upper()
            crud.update_product(ed["pid"], db, **{field: val})
            field_names = {
                "name": "Название", "sku": "Артикул", "category": "Категория",
                "unit": "Единица", "min_stock": "Мин. остаток", "barcode": "Штрихкод"
            }
            await update.message.reply_text(
                f"✅ *{p.name}*\n{field_names[field]} изменён на: `{val}`",
                parse_mode="Markdown"
            )
        finally:
            db.close()
        return

    db = get_db()
    try:
        # Быстрые команды без AI (мгновенно)
        if m := re.match(r"^минус\s+(\d+)\s+(.+)$", text_lower):
            await _movement(update, m.group(2), int(m.group(1)), "sale", db)
        elif m := re.match(r"^плюс\s+(\d+)\s+(.+)$", text_lower):
            await _movement(update, m.group(2), int(m.group(1)), "income", db)
        elif m := re.match(r"^списать\s+(\d+)\s+(.+)$", text_lower):
            await _movement(update, m.group(2), int(m.group(1)), "writeoff", db)
        elif m := re.match(r"^вернуть\s+(\d+)\s+(.+)$", text_lower):
            await _movement(update, m.group(2), int(m.group(1)), "return", db)
        else:
            # Всё остальное — через AI
            db.close()
            user_id = update.effective_user.id
            thinking = await update.message.reply_text("🤔 Думаю...")
            response = await process_ai_message(text, user_id)
            await thinking.delete()
            await update.message.reply_text(response, parse_mode="Markdown")
            return
    finally:
        db.close()


async def _stock_query(update, query, db):
    products = crud.find_product(query, db)
    if not products:
        await update.message.reply_text(f"❌ *{query}* не найден", parse_mode="Markdown")
        return
    if len(products) == 1:
        p = products[0]
        stock = crud.get_stock(p.id, db)
        icon = "🔴" if stock <= p.min_stock else "🟢"
        kb = [[
            InlineKeyboardButton("🛒-1", callback_data=f"qs:{p.id}"),
            InlineKeyboardButton("📦+1", callback_data=f"qi:{p.id}"),
            InlineKeyboardButton("📋 Детали", callback_data=f"detail:{p.id}"),
        ]]
        await update.message.reply_text(
            f"{icon} *{p.name}*\nОстаток: *{stock} {p.unit}* | `{p.sku}`",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        kb = [[InlineKeyboardButton(
            f"{'🔴' if crud.get_stock(p.id,db) <= p.min_stock else '🟢'} {p.name} — {crud.get_stock(p.id,db)}",
            callback_data=f"detail:{p.id}"
        )] for p in products[:8]]
        await update.message.reply_text(
            f"🔍 Найдено {len(products)}:", reply_markup=InlineKeyboardMarkup(kb)
        )


async def _movement(update, query, qty, move_type, db):
    products = crud.find_product(query, db)
    if not products:
        await update.message.reply_text(f"❌ *{query}* не найден", parse_mode="Markdown")
        return
    labels = {"income": "📦 Приход", "sale": "🛒 Продажа", "writeoff": "🗑 Списание", "return": "↩️ Возврат"}

    if len(products) == 1:
        p = products[0]
        crud.add_movement(p.id, qty, move_type, db)
        new_stock = crud.get_stock(p.id, db)
        icon = "🔴" if new_stock <= p.min_stock else "🟢"
        warn = "\n⚠️ *Ниже минимума! Закупи.*" if new_stock <= p.min_stock else ""
        await update.message.reply_text(
            f"{labels[move_type]}: {p.name}\n"
            f"Кол-во: {qty} {p.unit}\n"
            f"{icon} Остаток: *{new_stock} {p.unit}*{warn}",
            parse_mode="Markdown"
        )
    else:
        kb = [[InlineKeyboardButton(
            f"{p.name} ({crud.get_stock(p.id,db)})",
            callback_data=f"mv:{p.id}:{qty}:{move_type}"
        )] for p in products[:8]]
        await update.message.reply_text("Выбери товар:", reply_markup=InlineKeyboardMarkup(kb))


# ══════════════════════════════════════════════════════
# Callbacks
# ══════════════════════════════════════════════════════
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    db = get_db()
    try:
        if data == "menu:list":
            await cmd_list(update, context)
        elif data == "menu:low":
            await cmd_low(update, context)
        elif data == "menu:stats":
            await cmd_stats(update, context)
        elif data == "menu:help":
            await cmd_help(update, context)
        elif data == "menu:add":
            await q.edit_message_text("Напиши команду /add чтобы добавить товар пошагово")

        elif data.startswith("cat:"):
            cat_name = data[4:]
            stocks = crud.get_all_stocks(db)
            filtered = [s for s in stocks if (s["product"].category or "Общее") == cat_name]
            await _show_filtered(q, db, cat_name, filtered)

        elif data.startswith("brand:"):
            brand_name = data[6:]
            stocks = crud.get_all_stocks(db)
            filtered = [s for s in stocks if detect_brand(s["product"].name) == brand_name]
            await _show_filtered(q, db, f"Бренд: {brand_name}", filtered)

        elif data.startswith("detail:"):
            pid = int(data.split(":")[1])
            p = crud.get_product_by_id(pid, db)
            stock = crud.get_stock(pid, db)
            icon = "🔴" if stock <= p.min_stock else "🟢"
            mvs = crud.get_movements(pid, db, 3)
            hist = "  ".join([f"{'📦+' if m.quantity>0 else '🛒-'}{abs(m.quantity)}({m.created_at.strftime('%d.%m')})" for m in mvs]) or "—"
            kb = [
                [
                    InlineKeyboardButton("🛒-1", callback_data=f"qs:{pid}"),
                    InlineKeyboardButton("📦+1", callback_data=f"qi:{pid}"),
                    InlineKeyboardButton("🗑-1", callback_data=f"qw:{pid}"),
                ],
                [
                    InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_prod:{pid}"),
                    InlineKeyboardButton("🗑 Удалить", callback_data=f"del_prod:{pid}"),
                ],
            ]
            await q.edit_message_text(
                f"{icon} *{p.name}*\nАрт: `{p.sku}` | Кат: {p.category}\n"
                f"Остаток: *{stock} {p.unit}* (мин: {p.min_stock})\n"
                f"Штрихкод: `{p.barcode or 'не задан'}`\n\n"
                f"Последние: {hist}",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
            )

        elif data.startswith("edit_prod:"):
            pid = int(data.split(":")[1])
            p = crud.get_product_by_id(pid, db)
            kb = [
                [InlineKeyboardButton("📝 Название", callback_data=f"ef:{pid}:name"),
                 InlineKeyboardButton("🔖 Артикул", callback_data=f"ef:{pid}:sku")],
                [InlineKeyboardButton("🗂 Категория", callback_data=f"ef:{pid}:category"),
                 InlineKeyboardButton("📏 Единица", callback_data=f"ef:{pid}:unit")],
                [InlineKeyboardButton("⚠️ Мин. остаток", callback_data=f"ef:{pid}:min_stock"),
                 InlineKeyboardButton("📷 Штрихкод", callback_data=f"ef:{pid}:barcode")],
                [InlineKeyboardButton("🗑 Удалить товар", callback_data=f"del_prod:{pid}")],
                [InlineKeyboardButton("◀️ Назад", callback_data=f"detail:{pid}")],
            ]
            await q.edit_message_text(
                f"✏️ *{p.name}*\n\nЧто изменить?",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
            )

        elif data.startswith("del_prod:"):
            pid = int(data.split(":")[1])
            p = crud.get_product_by_id(pid, db)
            kb = [[
                InlineKeyboardButton("✅ Да, удалить", callback_data=f"del_confirm:{pid}"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"edit_prod:{pid}"),
            ]]
            await q.edit_message_text(
                f"🗑 Удалить *{p.name}*?\n\nВся история движений тоже удалится.",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
            )

        elif data.startswith("del_confirm:"):
            pid = int(data.split(":")[1])
            p = crud.get_product_by_id(pid, db)
            name = p.name if p else "Товар"
            from database import Movement as _Mov, Product as _Prod
            db.query(_Mov).filter(_Mov.product_id == pid).delete()
            db.query(_Prod).filter(_Prod.id == pid).delete()
            db.commit()
            await q.edit_message_text(f"🗑 *{name}* удалён", parse_mode="Markdown")

        elif data.startswith("ef:"):
            _, pid, field = data.split(":")
            pid = int(pid)
            p = crud.get_product_by_id(pid, db)
            field_names = {
                "name": "название", "sku": "артикул", "category": "категорию",
                "unit": "единицу измерения", "min_stock": "минимальный остаток", "barcode": "штрихкод"
            }
            current = getattr(p, field, "—") or "—"
            context.user_data["editing"] = {"pid": pid, "field": field}
            await q.edit_message_text(
                f"✏️ *{p.name}*\n\nСейчас *{field_names[field]}*: `{current}`\n\nВведи новое значение:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data=f"edit_prod:{pid}")]])
            )

        elif data.startswith("qs:"):
            await _quick(q, int(data.split(":")[1]), 1, "sale", db)
        elif data.startswith("qi:"):
            await _quick(q, int(data.split(":")[1]), 1, "income", db)
        elif data.startswith("qw:"):
            await _quick(q, int(data.split(":")[1]), 1, "writeoff", db)

        elif data.startswith("mv:"):
            _, pid, qty, mt = data.split(":")
            p = crud.get_product_by_id(int(pid), db)
            crud.add_movement(int(pid), int(qty), mt, db)
            ns = crud.get_stock(int(pid), db)
            lb = {"income": "Приход", "sale": "Продажа", "writeoff": "Списание"}.get(mt, mt)
            icon = "🔴" if ns <= p.min_stock else "🟢"
            await q.edit_message_text(
                f"✅ {lb}: {p.name}\nКол-во: {qty}\n{icon} Остаток: *{ns}*", parse_mode="Markdown"
            )
    finally:
        db.close()


async def _quick(q, pid, qty, move_type, db):
    p = crud.get_product_by_id(pid, db)
    crud.add_movement(pid, qty, move_type, db)
    ns = crud.get_stock(pid, db)
    icon = "🔴" if ns <= p.min_stock else "🟢"
    labels = {"sale": "🛒-1 Продажа", "income": "📦+1 Приход", "writeoff": "🗑-1 Списание"}
    warn = " ⚠️ Мало!" if ns <= p.min_stock else ""
    kb = [[
        InlineKeyboardButton("🛒-1", callback_data=f"qs:{pid}"),
        InlineKeyboardButton("📦+1", callback_data=f"qi:{pid}"),
        InlineKeyboardButton("🗑-1", callback_data=f"qw:{pid}"),
    ]]
    await q.edit_message_text(
        f"✅ {labels[move_type]}\n*{p.name}*\n{icon} Остаток: *{ns} {p.unit}*{warn}",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )


async def _reply(update, text):
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")


# ══════════════════════════════════════════════════════
# Запуск
# ══════════════════════════════════════════════════════
async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"Твой Chat ID: <code>{chat_id}</code>\n\n"
        f"Добавь в Railway переменную:\n<code>ADMIN_CHAT_ID={chat_id}</code>",
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════
# Уведомления по остаткам: ежедневный отчёт + /notify
# ══════════════════════════════════════════════════════
def _get_urgent_products(db, lead_time_days: int = 14):
    """Сканирует товары, считает недельный расход по Kaspi-заказам за 28 дней
    и возвращает список тех, по которым пора заказывать (days_left < lead_time*2).
    Отсортирован по возрастанию days_left."""
    from database import KaspiOrder, Product
    from collections import defaultdict
    from datetime import datetime as dt, timedelta
    import re

    COMPLETED = {"Выдан", "ARCHIVE"}
    cutoff = dt.now() - timedelta(days=28)

    def _parse_d(s):
        if not s:
            return None
        s = str(s)[:10]
        try:
            return dt.strptime(s, "%Y-%m-%d")
        except Exception:
            try:
                return dt.strptime(s, "%d.%m.%Y")
            except Exception:
                return None

    orders = db.query(KaspiOrder).filter(
        KaspiOrder.state.in_(COMPLETED),
        KaspiOrder.product_name.isnot(None),
    ).all()

    sold_last_28 = defaultdict(int)
    for o in orders:
        d = _parse_d(o.order_date)
        if d and d >= cutoff:
            sold_last_28[o.product_name] += o.quantity or 1

    stocks = crud.get_all_stocks(db)
    results = []
    for s in stocks:
        p = s["product"]
        stock = s["stock"]
        if stock <= 0:
            continue
        sold = sold_last_28.get(p.name, 0)
        if sold <= 0:
            continue  # нет продаж — пропуск, незачем заказывать
        weekly_rate = sold / 4.0
        daily_rate = weekly_rate / 7.0
        days_left = int(stock / daily_rate) if daily_rate > 0 else None
        if days_left is None:
            continue

        if days_left < lead_time_days:
            urgency = "urgent"
        elif days_left < lead_time_days * 2:
            urgency = "order"
        else:
            continue

        # Рекомендуемый объём заказа: покрыть 4 недели + safety stock 1.5×недельный расход
        order_qty = max(0, round(weekly_rate * 4 + weekly_rate * 1.5 - stock))

        results.append({
            "name": p.name,
            "unit": p.unit or "шт",
            "stock": stock,
            "weekly_rate": round(weekly_rate, 1),
            "days_left": days_left,
            "urgency": urgency,
            "order_qty": order_qty,
        })

    results.sort(key=lambda x: (0 if x["urgency"] == "urgent" else 1, x["days_left"]))
    return results


def _format_stock_report(items: list) -> str:
    if not items:
        return "✅ *Всё ок!*\n\nНет товаров по которым нужно срочно заказать.\nОстатки в норме на ближайшие 4 недели."

    urgent = [x for x in items if x["urgency"] == "urgent"]
    order = [x for x in items if x["urgency"] == "order"]

    lines = ["📦 *Отчёт по остаткам*", ""]
    if urgent:
        lines.append(f"🔴 *Срочно заказать ({len(urgent)}):*")
        for x in urgent[:15]:
            name = x["name"][:42] + ("…" if len(x["name"]) > 42 else "")
            lines.append(
                f"• {name}\n"
                f"  _{x['stock']} {x['unit']} · ~{x['days_left']} дн · заказать {x['order_qty']} {x['unit']}_"
            )
        if len(urgent) > 15:
            lines.append(f"  _…и ещё {len(urgent)-15}_")
        lines.append("")

    if order:
        lines.append(f"🟡 *Можно заказать ({len(order)}):*")
        for x in order[:10]:
            name = x["name"][:42] + ("…" if len(x["name"]) > 42 else "")
            lines.append(
                f"• {name} — _{x['stock']} {x['unit']}, ~{x['days_left']} дн_"
            )
        if len(order) > 10:
            lines.append(f"  _…и ещё {len(order)-10}_")

    return "\n".join(lines)


async def daily_stock_report(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневная рассылка отчёта админу."""
    chat_id = os.getenv("ADMIN_CHAT_ID", "")
    if not chat_id:
        logger.info("ADMIN_CHAT_ID не задан, ежедневный отчёт пропущен")
        return
    # Проверка включены ли уведомления (по настройке в БД)
    db = get_db()
    try:
        from database import SiteSetting
        row = db.query(SiteSetting).filter(SiteSetting.key == "notify_stock_enabled").first()
        if row and str(row.value) == "0":
            logger.info("Уведомления о остатках отключены в настройках")
            return
        items = _get_urgent_products(db)
        text = _format_stock_report(items)
    finally:
        db.close()
    try:
        await context.bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="Markdown",
        )
        logger.info(f"Отчёт по остаткам отправлен ({len(items)} товаров)")
    except Exception as e:
        logger.error(f"Не удалось отправить отчёт: {e}")


async def cmd_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручной запуск отчёта по остаткам — для теста."""
    db = get_db()
    try:
        items = _get_urgent_products(db)
        text = _format_stock_report(items)
    finally:
        db.close()
    await update.message.reply_text(text, parse_mode="Markdown")


def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_name)],
            ADD_SKU: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_sku)],
            ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_category)],
            ADD_UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_unit)],
            ADD_MIN_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_min_stock)],
            ADD_INITIAL_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_get_initial_stock)],
            ADD_BARCODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    bc_conv = ConversationHandler(
        entry_points=[CommandHandler("barcode", cmd_barcode_start)],
        states={
            BARCODE_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, barcode_get_product)],
            ADD_BARCODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, barcode_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(add_conv)
    app.add_handler(bc_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CommandHandler("kaspi", cmd_kaspi_orders))
    app.add_handler(CommandHandler("kaspi_sync", cmd_kaspi_sync))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("low", cmd_low))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("notify", cmd_notify))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # ── Ежедневный отчёт по остаткам (09:00 Алматы = 04:00 UTC) ──
    if app.job_queue:
        from datetime import time as dt_time
        app.job_queue.run_daily(
            daily_stock_report,
            time=dt_time(hour=4, minute=0),  # UTC; Алматы +5 → 09:00 местного
            name="daily_stock_report",
        )
        logger.info("📅 Ежедневный отчёт по остаткам запланирован на 09:00 Алматы")
    else:
        logger.warning("job_queue недоступен — ежедневный отчёт не запустится")

    logger.info("🤖 Бот запущен v2")
    app.run_polling(drop_pending_updates=True, stop_signals=None)

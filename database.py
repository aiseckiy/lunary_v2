from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool
from datetime import datetime

import os
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL не задан. Добавь переменную в Railway.")
# Railway отдаёт postgres://, SQLAlchemy требует postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

try:
    import psycopg2  # noqa
    engine = create_engine(DATABASE_URL, poolclass=NullPool)
except ImportError:
    engine = create_engine(
        DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1),
        poolclass=NullPool,
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    barcode = Column(String, nullable=True)
    category = Column(String, default="Общее", index=True)
    unit = Column(String, default="шт")
    min_stock = Column(Integer, default=5)
    brand = Column(String, nullable=True, index=True)
    price = Column(Integer, nullable=True)  # цена в тенге
    kaspi_sku = Column(String, nullable=True, index=True)   # ID для матчинга заказов (101602457_xxx)
    kaspi_article = Column(String, nullable=True)  # Артикул в Kaspi кабинете (KSP_xxx)
    cost_price = Column(Integer, nullable=True)  # закупочная цена
    supplier = Column(String, nullable=True)  # поставщик
    image_url = Column(Text, nullable=True)  # первое изображение (устаревшее, оставлено для совместимости)
    images = Column(Text, nullable=True)  # JSON-массив URL/base64
    description = Column(Text, nullable=True)  # описание товара
    specs = Column(Text, nullable=True)  # JSON-массив [{key, value}] характеристики
    supplier_article = Column(String, nullable=True)  # артикул производителя/поставщика
    verified = Column(Integer, default=0)  # 1 = проверен, 0 = не проверен
    linked_ref_id = Column(Integer, nullable=True)  # ID привязанного PriceListItem
    show_in_shop = Column(Boolean, default=False)   # показывать в публичном магазине
    meta_title = Column(String, nullable=True)       # SEO: заголовок страницы (50-60 символов)
    meta_description = Column(Text, nullable=True)   # SEO: описание для поиска (150-160 символов)
    meta_keywords = Column(Text, nullable=True)      # SEO: ключевые слова через запятую
    link_master_id = Column(Integer, nullable=True, index=True)  # FK→products.id; если задан — этот товар slave в группе с общими stock/price
    created_at = Column(DateTime, default=datetime.utcnow)


class KaspiOrder(Base):
    __tablename__ = "kaspi_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String, unique=True, nullable=False, index=True)
    state = Column(String, nullable=False, index=True)
    total = Column(Integer, default=0)
    customer = Column(String, nullable=True)
    entries = Column(String, nullable=True)  # JSON
    order_date = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Расширенные поля из XML / Kaspi
    product_name = Column(String, nullable=True)
    sku = Column(String, nullable=True)
    quantity = Column(Integer, nullable=True)
    category = Column(String, nullable=True)
    address = Column(String, nullable=True)
    status_date = Column(String, nullable=True)
    cancel_reason = Column(String, nullable=True)
    payment_method = Column(String, nullable=True)
    delivery_method = Column(String, nullable=True)
    courier = Column(String, nullable=True)
    delivery_cost_seller = Column(Integer, default=0)
    delivery_compensation = Column(Integer, default=0)
    source = Column(String, default="kaspi_api")  # kaspi_api | xml_import
    stock_deducted = Column(Integer, default=0)  # 1 если остатки уже списаны
    last_synced_at = Column(DateTime, nullable=True)  # когда последний раз обновился из Kaspi API


class Movement(Base):
    __tablename__ = "movements"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    # quantity: positive = приход/возврат, negative = продажа/списание
    quantity = Column(Integer, nullable=False)
    type = Column(String, nullable=False)  # income, sale, writeoff, return, adjustment
    source = Column(String, default="manual")  # manual, kaspi, offline
    note = Column(String, nullable=True)
    user_id = Column(Integer, nullable=True)
    user_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)
    avatar = Column(String, nullable=True)
    google_id = Column(String, unique=True, nullable=True)
    password_hash = Column(String, nullable=True)  # None если только Google
    phone = Column(String, nullable=True)
    role = Column(String, default="customer")  # admin | customer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ShopOrder(Base):
    __tablename__ = "shop_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)  # None если гость
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    address = Column(String, nullable=True)
    comment = Column(String, nullable=True)
    items = Column(String, nullable=False)  # JSON: [{product_id, name, qty, price}]
    total = Column(Integer, default=0)
    status = Column(String, default="new")  # new | confirmed | ready | delivered | cancelled
    created_at = Column(DateTime, default=datetime.utcnow)


class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, index=True)
    synced_at = Column(DateTime, default=datetime.utcnow)
    total_found = Column(Integer, default=0)   # всего заказов от Kaspi API
    added = Column(Integer, default=0)         # новых добавлено
    updated = Column(Integer, default=0)       # обновлено статусов
    returns = Column(Integer, default=0)       # возвратов остатков (отмены)
    deducted = Column(Integer, default=0)      # списаний остатков
    error = Column(String, nullable=True)      # текст ошибки если была


class PriceListItem(Base):
    """Справочник накладных/прайс-листов — только для поиска, не товары на складе."""
    __tablename__ = "price_list_items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    article = Column(String, nullable=True, index=True)   # артикул производителя
    supplier = Column(String, nullable=True, index=True)  # поставщик
    cost_price = Column(Integer, nullable=True)           # закупочная цена
    unit = Column(String, default="шт")
    source_file = Column(String, nullable=True)           # имя файла откуда импортировали
    is_new = Column(Boolean, default=True)                # True = новый, не был в предыдущем импорте
    created_at = Column(DateTime, default=datetime.utcnow)


class BrandAlias(Base):
    """Нормализация брендов: raw (как пришло из Kaspi) → shop_name (чистое имя для магазина).

    Пример: Tehnonikol-, TehnoNikol-Kz, Tehnonikol_stroi → "ТехноНиколь".
    Несколько raw могут резолвиться в один shop_name — это и есть объединение
    дубликатов одного бренда под чистым именем.

    shop_name = NULL значит бренд ещё не причёсан — в магазине показывается raw.
    hidden = True значит скрыть этот бренд в фильтрах магазина.
    """
    __tablename__ = "brand_aliases"

    id         = Column(Integer, primary_key=True, index=True)
    raw_name   = Column(String, unique=True, nullable=False, index=True)
    shop_name  = Column(String, nullable=True, index=True)
    hidden     = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CategoryAlias(Base):
    """Нормализация категорий аналогично BrandAlias."""
    __tablename__ = "category_aliases"

    id         = Column(Integer, primary_key=True, index=True)
    raw_name   = Column(String, unique=True, nullable=False, index=True)
    shop_name  = Column(String, nullable=True, index=True)
    icon       = Column(String, nullable=True)   # emoji или URL
    sort_order = Column(Integer, default=0)
    hidden     = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UploadedFile(Base):
    """История загруженных файлов."""
    __tablename__ = "uploaded_files"

    id = Column(Integer, primary_key=True, index=True)
    original_name = Column(String, nullable=False)       # оригинальное имя файла
    saved_name    = Column(String, nullable=True)        # имя сохранённого файла (с timestamp)
    file_type     = Column(String, nullable=False)       # kaspi_active | kaspi_archive | price_list | pricelist_ref
    size_bytes    = Column(Integer, nullable=True)
    records       = Column(Integer, nullable=True)       # сколько записей импортировано
    uploaded_at   = Column(DateTime, default=datetime.utcnow)


class SiteSetting(Base):
    __tablename__ = "site_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)
    label = Column(String, nullable=True)   # человекочитаемое название
    group = Column(String, default="general")  # general, contacts, shop, integrations


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    print("[init_db] Запуск миграций...", flush=True)
    try:
        Base.metadata.create_all(bind=engine)
        print("[init_db] create_all выполнен", flush=True)
    except Exception as e:
        print(f"[init_db] ОШИБКА create_all: {e}", flush=True)
    new_columns = [
        ("products", "price", "INTEGER"),
        ("products", "kaspi_sku", "TEXT"),
        ("products", "cost_price", "INTEGER"),
        ("products", "supplier", "TEXT"),
        ("products", "kaspi_article", "TEXT"),
        ("kaspi_orders", "product_name", "TEXT"),
        ("kaspi_orders", "sku", "TEXT"),
        ("kaspi_orders", "quantity", "INTEGER"),
        ("kaspi_orders", "category", "TEXT"),
        ("kaspi_orders", "address", "TEXT"),
        ("kaspi_orders", "status_date", "TEXT"),
        ("kaspi_orders", "cancel_reason", "TEXT"),
        ("kaspi_orders", "payment_method", "TEXT"),
        ("kaspi_orders", "delivery_method", "TEXT"),
        ("kaspi_orders", "courier", "TEXT"),
        ("kaspi_orders", "delivery_cost_seller", "INTEGER DEFAULT 0"),
        ("kaspi_orders", "delivery_compensation", "INTEGER DEFAULT 0"),
        ("kaspi_orders", "source", "TEXT DEFAULT 'kaspi_api'"),
        ("kaspi_orders", "stock_deducted", "INTEGER DEFAULT 0"),
        ("kaspi_orders", "last_synced_at", "TIMESTAMP"),
        ("users", "password_hash", "TEXT"),
        ("users", "phone", "TEXT"),
        ("movements", "user_id", "INTEGER"),
        ("movements", "user_name", "TEXT"),
        ("products", "image_url", "TEXT"),
        ("products", "images", "TEXT"),
        ("products", "description", "TEXT"),
        ("products", "specs", "TEXT"),
        ("products", "verified", "INTEGER DEFAULT 0"),
        ("products", "supplier_article", "TEXT"),
        ("products", "linked_ref_id", "INTEGER"),
        ("products", "show_in_shop", "BOOLEAN DEFAULT FALSE"),
        ("products", "meta_title", "TEXT"),
        ("products", "meta_description", "TEXT"),
        ("products", "meta_keywords", "TEXT"),
        ("products", "link_master_id", "INTEGER"),
        ("price_list_items", "is_new", "BOOLEAN DEFAULT TRUE"),
    ]
    with engine.connect() as conn:
        for table, col, col_type in new_columns:
            # IF NOT EXISTS — PostgreSQL 9.6+, не бросает исключение если колонка уже есть
            col_def = col_type.split()[0]  # берём только тип без DEFAULT для IF NOT EXISTS
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                conn.commit()
                print(f"[init_db] OK {table}.{col}", flush=True)
            except Exception as e:
                print(f"[init_db] ОШИБКА {table}.{col}: {str(e)[:200]}", flush=True)
                try:
                    conn.rollback()
                except Exception:
                    pass

    # Снять NOT NULL с legacy колонок которые в текущей модели nullable.
    # PostgreSQL: DROP NOT NULL идемпотентно (не падает если уже nullable).
    nullable_relax = [
        ("products", "sku"),
    ]
    with engine.connect() as conn:
        for table, col in nullable_relax:
            try:
                conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {col} DROP NOT NULL"))
                conn.commit()
                print(f"[init_db] OK drop NOT NULL {table}.{col}", flush=True)
            except Exception as e:
                # На SQLite этот синтаксис не работает — молча пропускаем
                msg = str(e)[:150]
                if "does not exist" in msg or "no such column" in msg:
                    pass
                else:
                    print(f"[init_db] drop NOT NULL {table}.{col}: {msg}", flush=True)
                try:
                    conn.rollback()
                except Exception:
                    pass

    # Индексы: create_all не создаёт индексы на уже существующих таблицах,
    # поэтому создаём вручную через CREATE INDEX IF NOT EXISTS
    new_indexes = [
        ("ix_products_kaspi_sku",       "products",     "kaspi_sku"),
        ("ix_products_link_master_id",  "products",     "link_master_id"),
        ("ix_kaspi_orders_order_id",    "kaspi_orders", "order_id"),
        ("ix_movements_product_id",     "movements",    "product_id"),
    ]
    with engine.connect() as conn:
        for idx_name, table, col in new_indexes:
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({col})"))
                conn.commit()
                print(f"[init_db] OK index {idx_name}", flush=True)
            except Exception as e:
                print(f"[init_db] ОШИБКА индекса {idx_name}: {str(e)[:200]}", flush=True)
                try:
                    conn.rollback()
                except Exception:
                    pass
    # Дефолтные настройки сайта
    default_settings = [
        ("shop_name",        "LUNARY",                                    "Название магазина",       "general"),
        ("shop_tagline",     "Строительные материалы и инструменты",       "Подзаголовок магазина",   "general"),
        ("banner_title",     "Строительные материалы и инструменты",       "Баннер: заголовок",       "shop"),
        ("banner_subtitle",  "Более 1000 товаров · Самовывоз и доставка", "Баннер: подзаголовок",    "shop"),
        ("banner_show",      "1",                                          "Показывать баннер",       "shop"),
        ("about_address",    "г. Алматы, ул. Строителей 1",               "Адрес",                   "contacts"),
        ("about_phone",      "+7 (700) 123-45-67",                        "Телефон",                 "contacts"),
        ("about_email",      "info@lunary.kz",                            "Email",                   "contacts"),
        ("about_hours_wd",   "9:00 — 18:00",                              "Часы работы: Пн–Пт",      "contacts"),
        ("about_hours_sat",  "10:00 — 16:00",                             "Часы работы: Суббота",    "contacts"),
        ("about_hours_sun",  "Выходной",                                  "Часы работы: Воскресенье","contacts"),
        ("about_description","Строительные материалы и инструменты оптом и в розницу. Широкий ассортимент, доступные цены.", "Описание компании", "general"),
        ("tg_bot_token",     "",                                           "Telegram Bot Token",      "integrations"),
        ("tg_chat_id",       "",                                           "Telegram Chat ID",        "integrations"),
        ("kaspi_api_key",    "",                                           "Kaspi API Key (Token)",   "integrations"),
        ("kaspi_shop_id",    "",                                           "Kaspi Shop ID",           "integrations"),
        ("kaspi_merchant_id","30409502",                                   "Kaspi Merchant ID",       "integrations"),
        ("kaspi_store_id",   "30409502_PP1",                               "Kaspi Store ID",          "integrations"),
        ("kaspi_city_id",    "750000000",                                  "Kaspi City ID (Алматы)",  "integrations"),
        ("kaspi_feed_token",      "",    "Kaspi Feed Token (защита URL)", "integrations"),
        ("kaspi_commission_pct",  "8",   "Комиссия Kaspi (%)",            "integrations"),
        ("tax_pct",               "4",   "Налог (%)",                     "integrations"),
        ("openai_api_key",        "",    "OpenAI API Key (AI описания)",  "integrations"),
        ("notify_stock_enabled",  "1",   "Telegram: ежедневный отчёт по остаткам", "notifications"),
    ]
    db2 = SessionLocal()
    try:
        for key, value, label, group in default_settings:
            existing = db2.query(SiteSetting).filter(SiteSetting.key == key).first()
            if not existing:
                db2.add(SiteSetting(key=key, value=value, label=label, group=group))
        db2.commit()
        print("[init_db] Настройки инициализированы", flush=True)
    except Exception as e:
        print(f"[init_db] Ошибка настроек: {e}", flush=True)
        db2.rollback()
    finally:
        db2.close()

    # Миграция: проставить show_in_shop=True для существующих Kaspi-товаров
    db3 = SessionLocal()
    try:
        updated = db3.execute(
            text("UPDATE products SET show_in_shop = TRUE WHERE (category = 'Kaspi' OR kaspi_sku IS NOT NULL) AND (show_in_shop IS NULL OR show_in_shop = FALSE)")
        )
        db3.commit()
        print(f"[init_db] show_in_shop миграция: {updated.rowcount} товаров", flush=True)
    except Exception as e:
        print(f"[init_db] show_in_shop миграция ошибка: {e}", flush=True)
        db3.rollback()
    finally:
        db3.close()

    # Авто-сид BrandAlias и CategoryAlias из существующих distinct значений
    # Если раньше импортировали товары и уже есть brands/categories — создаём
    # для них записи с shop_name=NULL (пока не причёсаны). Идемпотентно.
    db4 = SessionLocal()
    try:
        existing_brands = {b.raw_name for b in db4.query(BrandAlias).all()}
        raw_brands = db4.execute(text(
            "SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL AND brand != ''"
        )).fetchall()
        added_brands = 0
        for (raw,) in raw_brands:
            if raw and raw not in existing_brands:
                db4.add(BrandAlias(raw_name=raw))
                added_brands += 1
        if added_brands:
            db4.commit()
            print(f"[init_db] BrandAlias seeded: +{added_brands} raw brands", flush=True)

        existing_cats = {c.raw_name for c in db4.query(CategoryAlias).all()}
        raw_cats = db4.execute(text(
            "SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != ''"
        )).fetchall()
        added_cats = 0
        for (raw,) in raw_cats:
            if raw and raw not in existing_cats:
                db4.add(CategoryAlias(raw_name=raw))
                added_cats += 1
        if added_cats:
            db4.commit()
            print(f"[init_db] CategoryAlias seeded: +{added_cats} raw categories", flush=True)
    except Exception as e:
        print(f"[init_db] alias seed ошибка: {e}", flush=True)
        db4.rollback()
    finally:
        db4.close()

    print("[init_db] Готово", flush=True)

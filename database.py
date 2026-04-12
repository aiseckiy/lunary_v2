from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, text
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
    sku = Column(String, unique=True, nullable=False)
    barcode = Column(String, nullable=True)
    category = Column(String, default="Общее")
    unit = Column(String, default="шт")
    min_stock = Column(Integer, default=5)
    brand = Column(String, nullable=True)
    price = Column(Integer, nullable=True)  # цена в тенге
    kaspi_sku = Column(String, nullable=True)   # ID для матчинга заказов (101602457_xxx)
    kaspi_article = Column(String, nullable=True)  # Артикул в Kaspi кабинете (KSP_xxx)
    cost_price = Column(Integer, nullable=True)  # закупочная цена
    supplier = Column(String, nullable=True)  # поставщик
    created_at = Column(DateTime, default=datetime.utcnow)


class KaspiOrder(Base):
    __tablename__ = "kaspi_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String, unique=True, nullable=False)
    state = Column(String, nullable=False)
    total = Column(Integer, default=0)
    customer = Column(String, nullable=True)
    entries = Column(String, nullable=True)  # JSON
    order_date = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

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


class Movement(Base):
    __tablename__ = "movements"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, nullable=False)
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
        ("users", "password_hash", "TEXT"),
        ("users", "phone", "TEXT"),
        ("movements", "user_id", "INTEGER"),
        ("movements", "user_name", "TEXT"),
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
        ("kaspi_api_key",    "",                                           "Kaspi API Key",           "integrations"),
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
    print("[init_db] Готово", flush=True)

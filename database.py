from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker
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
    engine = create_engine(DATABASE_URL)
except ImportError:
    engine = create_engine(DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1))
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


class Movement(Base):
    __tablename__ = "movements"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, nullable=False)
    # quantity: positive = приход/возврат, negative = продажа/списание
    quantity = Column(Integer, nullable=False)
    type = Column(String, nullable=False)  # income, sale, writeoff, return, adjustment
    source = Column(String, default="manual")  # manual, kaspi, offline
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    new_columns = [
        ("products", "price", "INTEGER"),
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
    ]
    with engine.connect() as conn:
        for table, col, col_type in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                pass

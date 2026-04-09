from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

import os
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/lunary.db" if os.path.isdir("/data") else "sqlite:///./lunary.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
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
    created_at = Column(DateTime, default=datetime.utcnow)


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

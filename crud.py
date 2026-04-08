from sqlalchemy.orm import Session
from sqlalchemy import func
from database import Product, Movement
from datetime import datetime


# ─── Остаток ────────────────────────────────────────────────
def get_stock(product_id: int, db: Session) -> int:
    result = db.query(func.sum(Movement.quantity)).filter(
        Movement.product_id == product_id
    ).scalar()
    return result or 0


# ─── Поиск товара ────────────────────────────────────────────
def find_product(query: str, db: Session):
    """Поиск по части названия, артикулу или штрихкоду"""
    q = query.strip()

    # Точное совпадение по штрихкоду
    by_barcode = db.query(Product).filter(Product.barcode == q).all()
    if by_barcode:
        return by_barcode

    # Точное совпадение по артикулу (ASCII — SQLite lower() работает)
    by_sku = db.query(Product).filter(Product.sku == q.upper()).all()
    if by_sku:
        return by_sku

    # Поиск по названию в Python (кириллица — SQLite lower() не работает)
    all_products = db.query(Product).all()
    q_lower = q.lower()
    words = [w for w in q_lower.split() if len(w) >= 2]

    # AND-поиск: все слова должны быть в названии
    if words:
        results = [
            p for p in all_products
            if all(w in p.name.lower() for w in words)
        ]
        if results:
            return results

    # OR-поиск: хотя бы одно слово
    results = [
        p for p in all_products
        if any(w in p.name.lower() or w in p.sku.lower() for w in words)
    ]
    return results


def get_product_by_barcode(barcode: str, db: Session):
    return db.query(Product).filter(Product.barcode == barcode).first()


def get_product_by_id(product_id: int, db: Session):
    return db.query(Product).filter(Product.id == product_id).first()


def get_all_products(db: Session):
    return db.query(Product).all()


# ─── Добавить товар ──────────────────────────────────────────
def create_product(name: str, sku: str, db: Session, barcode=None, category="Общее", unit="шт", min_stock=5):
    product = Product(name=name, sku=sku, barcode=barcode, category=category, unit=unit, min_stock=min_stock)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


# ─── Движение склада ─────────────────────────────────────────
def add_movement(product_id: int, quantity: int, move_type: str, db: Session, source="manual", note=None):
    """
    quantity: передаётся как положительное число
    move_type: income(+), return(+), sale(-), writeoff(-)
    """
    signed_qty = quantity if move_type in ("income", "return", "adjustment") else -quantity
    movement = Movement(
        product_id=product_id,
        quantity=signed_qty,
        type=move_type,
        source=source,
        note=note
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)
    return movement


# ─── Установить начальный остаток ────────────────────────────
def set_initial_stock(product_id: int, quantity: int, db: Session):
    """Используется при первичной загрузке данных"""
    # Сначала обнуляем если есть
    existing = db.query(Movement).filter(
        Movement.product_id == product_id,
        Movement.type == "income",
        Movement.note == "initial"
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
    movement = Movement(
        product_id=product_id,
        quantity=quantity,
        type="income",
        source="manual",
        note="initial"
    )
    db.add(movement)
    db.commit()


# ─── Товары с низким остатком ────────────────────────────────
def get_low_stock_products(db: Session):
    products = get_all_products(db)
    result = []
    for p in products:
        stock = get_stock(p.id, db)
        if stock <= p.min_stock:
            result.append((p, stock))
    return result


# ─── Все остатки ─────────────────────────────────────────────
def get_all_stocks(db: Session):
    products = get_all_products(db)
    result = []
    for p in products:
        stock = get_stock(p.id, db)
        result.append({"product": p, "stock": stock})
    return result


# ─── История движений ────────────────────────────────────────
def get_movements(product_id: int, db: Session, limit=10):
    return db.query(Movement).filter(
        Movement.product_id == product_id
    ).order_by(Movement.created_at.desc()).limit(limit).all()

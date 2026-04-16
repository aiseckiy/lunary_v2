from sqlalchemy.orm import Session
from sqlalchemy import func
from database import Product, Movement
from datetime import datetime

# Известные бренды — порядок важен (проверяем сверху вниз)
KNOWN_BRANDS = ["TYTAN", "AKFIX", "TULEX", "ЭКСПЕРТ", "CAPSTONE"]


def detect_brand(name: str) -> str:
    """Определяет бренд по названию товара"""
    n = name.upper()
    for b in KNOWN_BRANDS:
        if b in n:
            return b
    return ""


def resolve_master_id(product_id: int, db: Session) -> int:
    """Если товар — slave в link-группе, возвращает id мастера. Иначе сам id.
    Используется для роутинга stock/price операций на мастера группы."""
    p = db.query(Product.link_master_id).filter(Product.id == product_id).first()
    if p and p[0]:
        return p[0]
    return product_id


# ─── Остаток ────────────────────────────────────────────────
def get_stock(product_id: int, db: Session) -> int:
    """Возвращает остаток товара. Для slave'ов возвращает остаток мастера."""
    effective_id = resolve_master_id(product_id, db)
    result = db.query(func.sum(Movement.quantity)).filter(
        Movement.product_id == effective_id
    ).scalar()
    return result or 0


# ─── Поиск товара ────────────────────────────────────────────
def find_product(query: str, db: Session):
    """Поиск по части названия, артикулу производителя, kaspi_sku или штрихкоду"""
    q = query.strip()

    # Точное совпадение по штрихкоду
    by_barcode = db.query(Product).filter(Product.barcode == q, Product.category != "Накладные").all()
    if by_barcode:
        return by_barcode

    # Точное совпадение по kaspi_sku
    by_kaspi = db.query(Product).filter(Product.kaspi_sku == q, Product.category != "Накладные").all()
    if by_kaspi:
        return by_kaspi

    # Точное совпадение по артикулу производителя
    by_article = db.query(Product).filter(Product.supplier_article == q.upper(), Product.category != "Накладные").all()
    if by_article:
        return by_article

    # Поиск по названию
    all_products = db.query(Product).filter(Product.category != "Накладные").all()
    q_lower = q.lower()
    words = [w for w in q_lower.split() if len(w) >= 2]

    if words:
        results = [p for p in all_products if all(w in p.name.lower() for w in words)]
        if results:
            return results

    results = [p for p in all_products if any(w in p.name.lower() for w in words)]
    return results


def get_product_by_barcode(barcode: str, db: Session):
    return db.query(Product).filter(Product.barcode == barcode).first()


def get_product_by_id(product_id: int, db: Session):
    return db.query(Product).filter(Product.id == product_id).first()


def get_all_products(db: Session):
    return db.query(Product).all()


# ─── Добавить товар ──────────────────────────────────────────
def create_product(name: str, db: Session, barcode=None, category="Общее", unit="шт", min_stock=5, brand=None, price=None, kaspi_sku=None, supplier_article=None):
    product = Product(
        name=name, barcode=barcode, category=category,
        unit=unit, min_stock=min_stock,
        brand=brand if brand is not None else detect_brand(name),
        price=price, kaspi_sku=kaspi_sku, supplier_article=supplier_article
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


# ─── Движение склада ─────────────────────────────────────────
def add_movement(product_id: int, quantity: int, move_type: str, db: Session, source="manual", note=None, user_id=None, user_name=None):
    # Slave → пишем движение на мастера группы. Аналитика и история всегда
    # хранятся у одного физического товара.
    effective_id = resolve_master_id(product_id, db)
    signed_qty = quantity if move_type in ("income", "return", "adjustment") else -quantity
    movement = Movement(
        product_id=effective_id,
        quantity=signed_qty,
        type=move_type,
        source=source,
        note=note,
        user_id=user_id,
        user_name=user_name,
    )
    db.add(movement)
    db.commit()
    return movement


# ─── Установить начальный остаток ────────────────────────────
def set_initial_stock(product_id: int, quantity: int, db: Session):
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
    stocks = get_all_stocks(db)
    return [(s["product"], s["stock"]) for s in stocks if s["stock"] <= s["product"].min_stock]


# ─── Все остатки (один запрос вместо N+1) ────────────────────
def get_all_stocks(db: Session):
    """Возвращает список {product, stock} для всех товаров (кроме накладных).
    Для slave'ов в link-группе stock берётся у мастера группы."""
    rows = (
        db.query(Product, func.coalesce(func.sum(Movement.quantity), 0).label("stock"))
        .outerjoin(Movement, Movement.product_id == Product.id)
        .filter(Product.category != "Накладные")
        .group_by(Product.id)
        .all()
    )
    # Индекс master_id → stock для быстрого lookup
    stock_by_id = {p.id: int(stock) for p, stock in rows}
    result = []
    for p, stock in rows:
        effective = stock_by_id.get(p.link_master_id, int(stock)) if p.link_master_id else int(stock)
        result.append({"product": p, "stock": effective})
    return result


# ─── Обновить товар ──────────────────────────────────────────
def update_product(product_id: int, db: Session, **kwargs):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        return None
    _nullable_ok = {"meta_title", "meta_description", "meta_keywords", "description", "specs"}
    for key, val in kwargs.items():
        if val is not None and hasattr(p, key):
            setattr(p, key, val)
        elif val is None and key in _nullable_ok and hasattr(p, key):
            setattr(p, key, None)
        elif val is False and hasattr(p, key):  # allow explicit False (e.g. show_in_shop)
            setattr(p, key, val)
    if "name" in kwargs and "brand" not in kwargs:
        auto = detect_brand(kwargs["name"])
        if auto:
            p.brand = auto
    db.commit()
    db.refresh(p)
    return p


# ─── История движений ────────────────────────────────────────
def get_movements(product_id: int, db: Session, limit=10):
    return db.query(Movement).filter(
        Movement.product_id == product_id
    ).order_by(Movement.created_at.desc()).limit(limit).all()

"""
Скрипт для загрузки начальных данных
Запусти: python seed.py
"""
from database import init_db, SessionLocal
import crud

PRODUCTS = [
    {
        "name": "Герметик TYTAN Универсальный, цвет белый 280 мл",
        "sku": "TYT_SIL_008",
        "category": "Герметики",
        "unit": "шт",
        "min_stock": 10,
        "initial_stock": 49
    },
    {
        "name": "Герметик TYTAN Универсальный, цвет серый 280 мл",
        "sku": "TYT_SIL_009",
        "category": "Герметики",
        "unit": "шт",
        "min_stock": 10,
        "initial_stock": 50
    },
    {
        "name": "Герметик TYTAN Универсальный, цвет черный 280 мл",
        "sku": "TYT_SIL_010",
        "category": "Герметики",
        "unit": "шт",
        "min_stock": 10,
        "initial_stock": 37
    },
    {
        "name": "Герметик TYTAN Универсальный, цвет коричневый 280 мл",
        "sku": "TYT_SIL_011",
        "category": "Герметики",
        "unit": "шт",
        "min_stock": 10,
        "initial_stock": 20
    },
]


def seed():
    init_db()
    db = SessionLocal()
    try:
        for data in PRODUCTS:
            # Проверяем, нет ли уже такого товара
            existing = crud.find_product(data["sku"], db)
            if existing:
                print(f"⏭  Уже есть: {data['name']}")
                continue

            product = crud.create_product(
                name=data["name"],
                sku=data["sku"],
                db=db,
                category=data.get("category", "Общее"),
                unit=data.get("unit", "шт"),
                min_stock=data.get("min_stock", 5)
            )
            crud.set_initial_stock(product.id, data["initial_stock"], db)
            print(f"✅ Добавлен: {data['name']} — {data['initial_stock']} шт")

        print("\n🎉 Готово!")
    finally:
        db.close()


if __name__ == "__main__":
    seed()

"""
Скрипт импорта товаров из export_products.json
Запустить один раз: python import_from_json.py
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv; load_dotenv()
from database import SessionLocal, Base, engine, Product
import crud

Base.metadata.create_all(bind=engine)

with open('export_products.json', encoding='utf-8') as f:
    data = json.load(f)

db = SessionLocal()
added = skipped = 0

for p in data:
    sku = p['sku'].upper()
    if db.query(Product).filter(Product.sku == sku).first():
        skipped += 1
        continue
    new_p = crud.create_product(
        name=p['name'], sku=sku, db=db,
        barcode=p.get('barcode'), category=p.get('category', 'Общее'),
        unit=p.get('unit', 'шт'), min_stock=p.get('min_stock', 5),
        brand=p.get('brand')
    )
    if p.get('stock', 0) > 0:
        crud.set_initial_stock(new_p.id, p['stock'], db)
    added += 1

db.close()
print(f'✅ Добавлено: {added} | Пропущено: {skipped}')

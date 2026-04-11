"""
Импорт исторических заказов из XML-выгрузки Kaspi.
Запуск: python import_xml.py "lunary_all_orders (1).xml"
"""
import sys
import xml.etree.ElementTree as ET
from database import SessionLocal, KaspiOrder, init_db

FIELD_MAP = {
    "N_заказа": "order_id",
    "Дата_поступления_заказа": "order_date",
    "Название_товара_в_Kaspi_Магазине": "product_name",
    "Артикул": "sku",
    "Количество": "quantity",
    "Сумма": "total",
    "Категория": "category",
    "Адрес_самовывоза_доставки": "address",
    "Дата_изменения_статуса": "status_date",
    "Статус": "state",
    "Причина_отмены": "cancel_reason",
    "Способ_оплаты": "payment_method",
    "Способ_доставки": "delivery_method",
    "Курьерская_служба": "courier",
    "Стоимость_доставки_для_продавца": "delivery_cost_seller",
    "Компенсация_за_доставку": "delivery_compensation",
}

INT_FIELDS = {"quantity", "total", "delivery_cost_seller", "delivery_compensation"}


def parse_orders(xml_path: str):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    orders = []
    for order_el in root.findall("Заказ"):
        row = {"source": "xml_import"}
        for xml_tag, db_field in FIELD_MAP.items():
            el = order_el.find(xml_tag)
            val = el.text.strip() if el is not None and el.text else None
            if val and db_field in INT_FIELDS:
                try:
                    val = int(float(val))
                except ValueError:
                    val = 0
            row[db_field] = val
        orders.append(row)
    return orders


def upsert_orders(orders: list):
    db = SessionLocal()
    inserted = 0
    updated = 0
    try:
        for row in orders:
            order_id = row.get("order_id")
            if not order_id:
                continue
            existing = db.query(KaspiOrder).filter_by(order_id=order_id).first()
            if existing:
                for k, v in row.items():
                    if k != "order_id":
                        setattr(existing, k, v)
                updated += 1
            else:
                db.add(KaspiOrder(**row))
                inserted += 1
        db.commit()
    finally:
        db.close()
    return inserted, updated


def main():
    xml_path = sys.argv[1] if len(sys.argv) > 1 else "lunary_all_orders (1).xml"
    print(f"Читаю {xml_path}...")
    init_db()
    orders = parse_orders(xml_path)
    print(f"Найдено заказов: {len(orders)}")
    inserted, updated = upsert_orders(orders)
    print(f"Готово: добавлено {inserted}, обновлено {updated}")


if __name__ == "__main__":
    main()

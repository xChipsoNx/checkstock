"""Идемпотентный сид: склады, товары и стартовые остатки.

Запускается в docker compose перед стартом приложения.
Повторный запуск не создаёт дублей.
"""
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Balance, Location, Product
from app.services import stock

WAREHOUSES = ["Север", "Юг"]
TRANSIT = [
    ("Транзит ФФ", "TRANSIT_FF"),
    ("Транзит FBO", "TRANSIT_FBO"),
    ("Транзит FBS", "TRANSIT_FBS"),
]
PRODUCTS = [
    ("SKU-101", "Органайзер"),
    ("SKU-102", "Контейнер"),
    ("SKU-103", "Корзина"),
]
STOCK = [("SKU-101", "Север", 10), ("SKU-102", "Север", 5),
         ("SKU-101", "Юг", 6), ("SKU-103", "Юг", 4)]


def main() -> None:
    db = SessionLocal()
    try:
        for name in WAREHOUSES:
            if not db.execute(select(Location).where(Location.name == name)).scalar_one_or_none():
                db.add(Location(name=name, type="WAREHOUSE"))
        for name, type_ in TRANSIT:
            if not db.execute(select(Location).where(Location.name == name)).scalar_one_or_none():
                db.add(Location(name=name, type=type_))
        for sku, name in PRODUCTS:
            if not db.execute(select(Product).where(Product.sku == sku)).scalar_one_or_none():
                db.add(Product(sku=sku, name=name))
        db.commit()

        if db.execute(select(func.count()).select_from(Balance)).scalar_one() == 0:
            locations = {l.name: l for l in db.execute(select(Location)).scalars()}
            products = {p.sku: p for p in db.execute(select(Product)).scalars()}
            for sku, warehouse, qty in STOCK:
                stock.income(
                    db,
                    products[sku].id,
                    locations[warehouse].id,
                    qty,
                    key=f"seed-{sku}-{warehouse}",
                    note="стартовый остаток",
                )
        print("Seed: ok")
    finally:
        db.close()


if __name__ == "__main__":
    main()

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Location, Product
from app.services import stock


def main() -> None:
    db = SessionLocal()
    try:
        north = db.execute(select(Location).where(Location.name == "Север")).scalar_one()
        sku101 = db.execute(select(Product).where(Product.sku == "SKU-101")).scalar_one()
        sku102 = db.execute(select(Product).where(Product.sku == "SKU-102")).scalar_one()

        income_id = stock.income(db, sku102.id, north.id, 3, "income-102-1")
        reserve_id = stock.reserve(db, sku101.id, north.id, 3, "reserve-101-1")
        ship_id = stock.ship_fbs(db, sku101.id, north.id, 3, "ship-101-1")

        # повторные вызовы не должны ничего менять
        stock.reserve(db, sku101.id, north.id, 3, "reserve-101-1")
        stock.ship_fbs(db, sku101.id, north.id, 3, "ship-101-1")

        print("операции:", income_id, reserve_id, ship_id)
        for row in stock.stock_table(db):
            print(row)
        print("итого SKU-101:", stock.stock_total(db, sku101.id))
    finally:
        db.close()


if __name__ == "__main__":
    main()
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Ledger, Location, Product, Transfer
from app.services import stock

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def back(url: str, *, ok: str | None = None, err: str | None = None) -> RedirectResponse:
    params = []
    if ok:
        params.append(f"ok={quote(ok)}")
    if err:
        params.append(f"err={quote(err)}")
    query = "?" + "&".join(params) if params else ""
    return RedirectResponse(url + query, status_code=303)


def refs(db: Session) -> dict:
    return {
        "products": db.execute(select(Product).order_by(Product.sku)).scalars().all(),
        "warehouse_refs": db.execute(
            select(Location)
            .where(Location.type == "WAREHOUSE", Location.is_deleted.is_(False))
            .order_by(Location.name)
        ).scalars().all(),
    }


@router.get("/")
def index():
    return RedirectResponse("/stock", status_code=303)


@router.get("/stock")
def stock_page(request: Request, db: Session = Depends(get_db)):
    rows = stock.stock_table(db)
    return templates.TemplateResponse(request, "stock.html", {
        "stock_rows": [r for r in rows if r.type == "WAREHOUSE"],
        "transit_rows": [r for r in rows if r.type.startswith("TRANSIT")],
        "new_key": uuid4().hex,
        **refs(db),
    })


@router.get("/movements")
def movements_page(request: Request, db: Session = Depends(get_db)):
    history = db.execute(
        select(Ledger).order_by(Ledger.created_at.desc()).limit(50)
    ).scalars().all()
    transfers = db.execute(
        select(Transfer).order_by(Transfer.created_at.desc()).limit(20)
    ).scalars().all()

    locations = db.execute(select(Location)).scalars().all()
    products = db.execute(select(Product)).scalars().all()

    return templates.TemplateResponse(request, "movements.html", {
        "history": history,
        "transfers": transfers,
        "loc_names": {l.id: l.name for l in locations},
        "product_names": {p.id: p.sku for p in products},
        "new_key": uuid4().hex,
        **refs(db),
    })


@router.get("/warehouses")
def warehouses_page(request: Request, db: Session = Depends(get_db)):
    locations = db.execute(select(Location).order_by(Location.id)).scalars().all()
    return templates.TemplateResponse(request, "warehouses.html", {"locations": locations})


@router.post("/actions/income")
def action_income(
    product_id: int = Form(...), location_id: int = Form(...),
    qty: int = Form(...), key: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        stock.income(db, product_id, location_id, qty, key)
        return back("/movements", ok="Приход оформлен")
    except stock.StockError as e:
        return back("/movements", err=str(e))


@router.post("/actions/reserve")
def action_reserve(
    product_id: int = Form(...), location_id: int = Form(...),
    qty: int = Form(...), key: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        stock.reserve(db, product_id, location_id, qty, key)
        return back("/stock", ok="Резерв создан")
    except stock.InsufficientStock as e:
        hint = "Создайте перемещение с другого склада." if e.available_total >= e.qty \
            else "Товара не хватает на всех складах — нужно поступление."
        return back("/stock", err=(
            f"Не хватает: на складе {e.available_here}, всего {e.available_total}. {hint}"
        ))
    except stock.StockError as e:
        return back("/stock", err=str(e))


@router.post("/actions/ship")
def action_ship(
    product_id: int = Form(...), location_id: int = Form(...),
    qty: int = Form(...), key: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        stock.ship_fbs(db, product_id, location_id, qty, key)
        return back("/stock", ok="Отгрузка FBS оформлена")
    except stock.CannotShip as e:
        return back("/stock", err=(
            f"Нельзя отгрузить: физически {e.physical}, зарезервировано {e.reserved}. "
            "Сначала оформите резерв под заказ FBS на этом складе — на странице «Остатки»."
        ))
    except stock.StockError as e:
        return back("/stock", err=str(e))


@router.post("/actions/transfer")
def action_transfer(
    product_id: int = Form(...), from_id: int = Form(...), to_id: int = Form(...),
    qty: int = Form(...), key: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        stock.create_transfer(db, product_id, from_id, to_id, qty, key)
        return back("/movements", ok="Перемещение создано, товар в пути")
    except stock.StockError as e:
        return back("/movements", err=str(e))


@router.post("/actions/transfer/{transfer_id}/receive")
def action_receive(transfer_id: int, key: str = Form(...), db: Session = Depends(get_db)):
    try:
        stock.receive_transfer(db, transfer_id, key)
        return back("/movements", ok="Перемещение принято")
    except stock.StockError as e:
        return back("/movements", err=str(e))


@router.post("/actions/warehouse")
def action_warehouse_add(name: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        return back("/warehouses", err="Название не может быть пустым")
    db.add(Location(name=name, type="WAREHOUSE"))
    try:
        db.commit()
    except Exception:
        db.rollback()
        return back("/warehouses", err="Склад с таким названием уже существует")
    return back("/warehouses", ok="Склад добавлен")


@router.post("/actions/warehouse/{location_id}/rename")
def action_warehouse_rename(location_id: int, name: str = Form(...), db: Session = Depends(get_db)):
    location = db.get(Location, location_id)
    if location is None:
        return back("/warehouses", err="Склад не найден")
    location.name = name.strip()
    db.commit()
    return back("/warehouses", ok="Склад переименован")


@router.post("/actions/warehouse/{location_id}/delete")
def action_warehouse_delete(location_id: int, db: Session = Depends(get_db)):
    location = db.get(Location, location_id)
    if location is None:
        return back("/warehouses", err="Склад не найден")
    location.is_deleted = True
    db.commit()
    return back("/warehouses", ok="Склад в архиве, остатки и история сохранены")
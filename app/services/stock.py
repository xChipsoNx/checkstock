from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Balance, Ledger, Location, Operation, Product, Transfer


class StockError(Exception):
    """Базовая ошибка складских операций."""


class NotFound(StockError):
    pass


class InsufficientStock(StockError):
    def __init__(self, qty: int, available_here: int, available_total: int):
        self.qty = qty
        self.available_here = available_here
        self.available_total = available_total
        super().__init__(
            f"Запрошено {qty}, на складе {available_here}, всего {available_total}"
        )


class CannotShip(StockError):
    def __init__(self, reason: str, physical: int, reserved: int):
        self.reason = reason
        self.physical = physical
        self.reserved = reserved
        super().__init__(reason)


# ---------- вспомогательное ----------

def _start_operation(db: Session, kind: str, key: str) -> int | None:
    """Возвращает id новой операции или None, если ключ уже использован."""
    return db.execute(
        pg_insert(Operation)
        .values(kind=kind, idempotency_key=key)
        .on_conflict_do_nothing(index_elements=["kind", "idempotency_key"])
        .returning(Operation.id)
    ).scalar_one_or_none()


def _repeat_of(db: Session, kind: str, key: str) -> int:
    """Повторный запрос: возвращаем результат первой операции, ничего не меняя."""
    op = db.execute(
        select(Operation).where(
            Operation.kind == kind,
            Operation.idempotency_key == key,
        )
    ).scalar_one()
    db.rollback()
    return op.ref_id or op.id


def _finish(db: Session, op_id: int) -> int:
    db.execute(update(Operation).where(Operation.id == op_id).values(ref_id=op_id))
    db.commit()
    return op_id


def _get_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise NotFound(f"Товар {product_id} не найден")
    return product


def _get_warehouse(db: Session, location_id: int) -> Location:
    location = db.get(Location, location_id)
    if location is None or location.is_deleted:
        raise NotFound(f"Склад {location_id} не найден")
    if location.type != "WAREHOUSE":
        raise StockError(f"{location.name}: это не склад")
    return location


def _get_transit(db: Session, transit_type: str) -> Location:
    location = db.execute(
        select(Location).where(Location.type == transit_type)
    ).scalar_one_or_none()
    if location is None:
        raise NotFound(f"Транзитная локация {transit_type} не найдена")
    return location


def _upsert_balance(db: Session, product_id: int, location_id: int, delta: int) -> None:
    db.execute(
        pg_insert(Balance)
        .values(
            product_id=product_id,
            location_id=location_id,
            physical=delta,
            reserved_fbs=0,
        )
        .on_conflict_do_update(
            index_elements=["product_id", "location_id"],
            set_={"physical": Balance.physical + delta},
        )
    )


# ---------- операции ----------

def income(db: Session, product_id: int, location_id: int, qty: int, key: str,
           note: str | None = None) -> int:
    """Приход на склад."""
    if qty <= 0:
        raise StockError("Количество прихода должно быть больше нуля")

    _get_product(db, product_id)
    _get_warehouse(db, location_id)

    op_id = _start_operation(db, "INCOME", key)
    if op_id is None:
        return _repeat_of(db, "INCOME", key)

    _upsert_balance(db, product_id, location_id, qty)
    db.add(Ledger(
        product_id=product_id, location_id=location_id,
        qty_delta=qty, reserve_delta=0,
        kind="INCOME", ref_type="operation", ref_id=op_id, note=note,
    ))
    return _finish(db, op_id)


def reserve(db: Session, product_id: int, location_id: int, qty: int, key: str) -> int:
    """Резерв под заказ FBS. Товар остаётся на складе, но становится недоступным."""
    if qty <= 0:
        raise StockError("Количество резерва должно быть больше нуля")

    _get_product(db, product_id)
    _get_warehouse(db, location_id)

    op_id = _start_operation(db, "FBS_RESERVE", key)
    if op_id is None:
        return _repeat_of(db, "FBS_RESERVE", key)

    row = db.execute(
        update(Balance)
        .where(
            Balance.product_id == product_id,
            Balance.location_id == location_id,
            Balance.physical - Balance.reserved_fbs >= qty,
        )
        .values(reserved_fbs=Balance.reserved_fbs + qty)
        .returning(Balance.physical, Balance.reserved_fbs)
    ).first()

    if row is None:
        available_here = db.execute(
            select(func.coalesce(Balance.physical - Balance.reserved_fbs, 0))
            .where(Balance.product_id == product_id, Balance.location_id == location_id)
        ).scalar_one()
        available_total = db.execute(
            select(func.coalesce(func.sum(Balance.physical - Balance.reserved_fbs), 0))
            .join(Location, Location.id == Balance.location_id)
            .where(
                Balance.product_id == product_id,
                Location.type == "WAREHOUSE",
                Location.is_deleted.is_(False),
            )
        ).scalar_one()
        db.rollback()
        raise InsufficientStock(qty, int(available_here), int(available_total))

    db.add(Ledger(
        product_id=product_id, location_id=location_id,
        qty_delta=0, reserve_delta=qty,
        kind="FBS_RESERVE", ref_type="operation", ref_id=op_id,
    ))
    return _finish(db, op_id)


def ship_fbs(db: Session, product_id: int, location_id: int, qty: int, key: str) -> int:
    """Отгрузка FBS: снимаем резерв, убираем товар со склада, отправляем в путь."""
    if qty <= 0:
        raise StockError("Количество отгрузки должно быть больше нуля")

    _get_product(db, product_id)
    _get_warehouse(db, location_id)
    transit = _get_transit(db, "TRANSIT_FBS")

    op_id = _start_operation(db, "FBS_SHIP", key)
    if op_id is None:
        return _repeat_of(db, "FBS_SHIP", key)

    row = db.execute(
        update(Balance)
        .where(
            Balance.product_id == product_id,
            Balance.location_id == location_id,
            Balance.reserved_fbs >= qty,
            Balance.physical >= qty,
        )
        .values(
            reserved_fbs=Balance.reserved_fbs - qty,
            physical=Balance.physical - qty,
        )
        .returning(Balance.physical, Balance.reserved_fbs)
    ).first()

    if row is None:
        balance = db.execute(
            select(Balance).where(
                Balance.product_id == product_id,
                Balance.location_id == location_id,
            )
        ).scalar_one_or_none()
        db.rollback()
        if balance is None:
            raise CannotShip("Нет остатка на складе", 0, 0)
        raise CannotShip(
            "Недостаточно зарезервированного или физического остатка",
            balance.physical,
            balance.reserved_fbs,
        )

    _upsert_balance(db, product_id, transit.id, qty)
    db.add(Ledger(
        product_id=product_id, location_id=location_id,
        qty_delta=-qty, reserve_delta=-qty,
        kind="FBS_SHIP", ref_type="operation", ref_id=op_id,
    ))
    db.add(Ledger(
        product_id=product_id, location_id=transit.id,
        qty_delta=qty, reserve_delta=0,
        kind="FBS_SHIP", ref_type="operation", ref_id=op_id,
    ))
    return _finish(db, op_id)


# ---------- чтение ----------

def create_transfer(db: Session, product_id: int, from_id: int, to_id: int,
                    qty: int, key: str) -> int:
    """Перемещение между ФФ: товар уходит со склада и едет в транзит."""
    if qty <= 0:
        raise StockError("Количество перемещения должно быть больше нуля")
    if from_id == to_id:
        raise StockError("Нельзя переместить товар на тот же склад")

    _get_product(db, product_id)
    _get_warehouse(db, from_id)
    _get_warehouse(db, to_id)
    transit = _get_transit(db, "TRANSIT_FF")

    op_id = _start_operation(db, "TRANSFER", key)
    if op_id is None:
        return _repeat_of(db, "TRANSFER", key)

    row = db.execute(
        update(Balance)
        .where(
            Balance.product_id == product_id,
            Balance.location_id == from_id,
            Balance.physical - Balance.reserved_fbs >= qty,
        )
        .values(physical=Balance.physical - qty)
        .returning(Balance.physical)
    ).first()

    if row is None:
        available_here = db.execute(
            select(func.coalesce(Balance.physical - Balance.reserved_fbs, 0))
            .where(Balance.product_id == product_id, Balance.location_id == from_id)
        ).scalar_one()
        available_total = db.execute(
            select(func.coalesce(func.sum(Balance.physical - Balance.reserved_fbs), 0))
            .join(Location, Location.id == Balance.location_id)
            .where(
                Balance.product_id == product_id,
                Location.type == "WAREHOUSE",
                Location.is_deleted.is_(False),
            )
        ).scalar_one()
        db.rollback()
        raise InsufficientStock(qty, int(available_here), int(available_total))

    db.add(Transfer(
        product_id=product_id,
        from_location_id=from_id,
        to_location_id=to_id,
        qty=qty,
        status="IN_TRANSIT",
        operation_id=op_id,
    ))
    _upsert_balance(db, product_id, transit.id, qty)
    db.add(Ledger(product_id=product_id, location_id=from_id, qty_delta=-qty,
                  kind="TRANSFER_OUT", ref_type="operation", ref_id=op_id))
    db.add(Ledger(product_id=product_id, location_id=transit.id, qty_delta=qty,
                  kind="TRANSFER_OUT", ref_type="operation", ref_id=op_id))
    return _finish(db, op_id)


def receive_transfer(db: Session, transfer_id: int, key: str) -> int:
    """Приёмка перемещения: товар приходит с транзита на склад назначения."""
    transfer = db.get(Transfer, transfer_id)
    if transfer is None:
        raise NotFound(f"Перемещение {transfer_id} не найдено")
    if transfer.status != "IN_TRANSIT":
        raise StockError("Перемещение уже принято")

    op_id = _start_operation(db, "TRANSFER_RECEIVE", key)
    if op_id is None:
        return _repeat_of(db, "TRANSFER_RECEIVE", key)

    transit = _get_transit(db, "TRANSIT_FF")
    row = db.execute(
        update(Balance)
        .where(
            Balance.product_id == transfer.product_id,
            Balance.location_id == transit.id,
            Balance.physical >= transfer.qty,
        )
        .values(physical=Balance.physical - transfer.qty)
        .returning(Balance.physical)
    ).first()

    if row is None:
        db.rollback()
        raise StockError("В транзите не хватает товара для приёмки")

    _upsert_balance(db, transfer.product_id, transfer.to_location_id, transfer.qty)

    transfer.status = "RECEIVED"
    transfer.received_at = func.now()
    db.add(Ledger(product_id=transfer.product_id, location_id=transit.id,
                  qty_delta=-transfer.qty, kind="TRANSFER_IN",
                  ref_type="operation", ref_id=op_id))
    db.add(Ledger(product_id=transfer.product_id, location_id=transfer.to_location_id,
                  qty_delta=transfer.qty, kind="TRANSFER_IN",
                  ref_type="operation", ref_id=op_id))
    return _finish(db, op_id)


def stock_table(db: Session):
    """Остатки по складам и транзитным локациям."""
    return db.execute(
        select(
            Product.sku,
            Location.name,
            Location.type,
            Balance.physical,
            Balance.reserved_fbs,
            (Balance.physical - Balance.reserved_fbs).label("available"),
        )
        .join(Balance, Balance.product_id == Product.id)
        .join(Location, Location.id == Balance.location_id)
        .order_by(Product.sku, Location.id)
    ).all()


def stock_total(db: Session, product_id: int) -> int:
    return db.execute(
        select(func.coalesce(func.sum(Balance.physical), 0))
        .where(Balance.product_id == product_id)
    ).scalar_one()


def reconcile(db: Session) -> list[dict]:
    """Сверка: сумма проводок журнала против остатков в balances."""
    ledger_sq = (
        select(
            Ledger.product_id.label("product_id"),
            func.coalesce(func.sum(Ledger.qty_delta), 0).label("qty"),
            func.coalesce(func.sum(Ledger.reserve_delta), 0).label("reserved"),
        )
        .group_by(Ledger.product_id)
        .subquery()
    )
    balance_sq = (
        select(
            Balance.product_id.label("product_id"),
            func.coalesce(func.sum(Balance.physical), 0).label("physical"),
            func.coalesce(func.sum(Balance.reserved_fbs), 0).label("reserved"),
        )
        .group_by(Balance.product_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Product.sku,
            func.coalesce(ledger_sq.c.qty, 0).label("ledger_qty"),
            func.coalesce(balance_sq.c.physical, 0).label("balance_qty"),
            func.coalesce(ledger_sq.c.reserved, 0).label("ledger_reserved"),
            func.coalesce(balance_sq.c.reserved, 0).label("balance_reserved"),
        )
        .outerjoin(ledger_sq, ledger_sq.c.product_id == Product.id)
        .outerjoin(balance_sq, balance_sq.c.product_id == Product.id)
        .order_by(Product.sku)
    ).all()

    return [
        {
            "sku": r.sku,
            "ledger_qty": int(r.ledger_qty),
            "balance_qty": int(r.balance_qty),
            "ledger_reserved": int(r.ledger_reserved),
            "balance_reserved": int(r.balance_reserved),
            "ok": int(r.ledger_qty) == int(r.balance_qty)
            and int(r.ledger_reserved) == int(r.balance_reserved),
        }
        for r in rows
    ]
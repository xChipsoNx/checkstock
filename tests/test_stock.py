import pytest
from sqlalchemy import func, select

from app.models import Balance, Ledger
from app.services import stock


def get_balance(db, product_id: int, location_id: int) -> Balance:
    db.expire_all()
    return db.execute(
        select(Balance).where(
            Balance.product_id == product_id,
            Balance.location_id == location_id,
        )
    ).scalar_one()


def ledger_sum(db, product_id: int) -> int:
    return db.execute(
        select(func.coalesce(func.sum(Ledger.qty_delta), 0))
        .where(Ledger.product_id == product_id)
    ).scalar_one()


def balance_sum(db, product_id: int) -> int:
    return db.execute(
        select(func.coalesce(func.sum(Balance.physical), 0))
        .where(Balance.product_id == product_id)
    ).scalar_one()


def test_income_increases_physical(db, seed):
    stock.income(db, seed.organizer.id, seed.north.id, 10, "inc-1")

    balance = get_balance(db, seed.organizer.id, seed.north.id)
    assert balance.physical == 10
    assert balance.reserved_fbs == 0
    assert ledger_sum(db, seed.organizer.id) == 10


def test_reserve_does_not_change_physical(db, seed):
    stock.income(db, seed.organizer.id, seed.north.id, 10, "inc-2")
    stock.reserve(db, seed.organizer.id, seed.north.id, 3, "res-2")

    balance = get_balance(db, seed.organizer.id, seed.north.id)
    assert balance.physical == 10
    assert balance.reserved_fbs == 3
    assert balance.physical - balance.reserved_fbs == 7


def test_ship_fbs_moves_goods_to_transit(db, seed):
    stock.income(db, seed.organizer.id, seed.north.id, 10, "inc-3")
    stock.reserve(db, seed.organizer.id, seed.north.id, 3, "res-3")
    stock.ship_fbs(db, seed.organizer.id, seed.north.id, 3, "ship-3")

    north = get_balance(db, seed.organizer.id, seed.north.id)
    transit = get_balance(db, seed.organizer.id, seed.transit_fbs.id)

    assert north.physical == 7
    assert north.reserved_fbs == 0
    assert transit.physical == 3
    assert balance_sum(db, seed.organizer.id) == 10  # товар не потерялся


def test_reserve_more_than_available_raises(db, seed):
    stock.income(db, seed.organizer.id, seed.north.id, 5, "inc-4")

    with pytest.raises(stock.InsufficientStock) as exc_info:
        stock.reserve(db, seed.organizer.id, seed.north.id, 8, "res-4")

    error = exc_info.value
    assert error.qty == 8
    assert error.available_here == 5
    assert error.available_total == 5

    # остатки не изменились
    balance = get_balance(db, seed.organizer.id, seed.north.id)
    assert balance.physical == 5
    assert balance.reserved_fbs == 0


def test_same_idempotency_key_does_not_double_reserve(db, seed):
    stock.income(db, seed.organizer.id, seed.north.id, 10, "inc-5")

    first = stock.reserve(db, seed.organizer.id, seed.north.id, 3, "res-5")
    second = stock.reserve(db, seed.organizer.id, seed.north.id, 3, "res-5")

    assert first == second

    balance = get_balance(db, seed.organizer.id, seed.north.id)
    assert balance.reserved_fbs == 3


def test_ledger_matches_balances_after_full_scenario(db, seed):
    stock.income(db, seed.organizer.id, seed.north.id, 10, "inc-6")
    stock.income(db, seed.organizer.id, seed.south.id, 6, "inc-6b")
    stock.reserve(db, seed.organizer.id, seed.north.id, 3, "res-6")
    stock.ship_fbs(db, seed.organizer.id, seed.north.id, 3, "ship-6")

    balance = get_balance(db, seed.organizer.id, seed.south.id)
    assert balance.physical == 6

    assert ledger_sum(db, seed.organizer.id) == balance_sum(db, seed.organizer.id) == 16



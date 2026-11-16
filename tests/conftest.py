from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Base, Location, Product


TEST_URL = make_url(settings.database_url).set(database="checkstock_test")


def _ensure_test_database() -> None:
    """Создаёт checkstock_test, если её ещё нет."""
    admin = TEST_URL.set(database="postgres")
    dsn = admin.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://"
    )
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_URL.database,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{TEST_URL.database}"')


@pytest.fixture(scope="session")
def engine():
    _ensure_test_database()
    eng = create_engine(
        TEST_URL, pool_pre_ping=True, connect_args={"connect_timeout": 5}
    )
    with eng.begin() as conn:
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    connection = engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def seed(db: Session):
    """Базовый набор: 2 склада, 3 транзитные локации, 2 товара."""
    north = Location(name="Север", type="WAREHOUSE")
    south = Location(name="Юг", type="WAREHOUSE")
    transit_ff = Location(name="Транзит ФФ", type="TRANSIT_FF")
    transit_fbo = Location(name="Транзит FBO", type="TRANSIT_FBO")
    transit_fbs = Location(name="Транзит FBS", type="TRANSIT_FBS")
    organizer = Product(sku="SKU-101", name="Органайзер")
    container = Product(sku="SKU-102", name="Контейнер")

    db.add_all([north, south, transit_ff, transit_fbo, transit_fbs, organizer, container])
    db.commit()

    return SimpleNamespace(
        north=north, south=south,
        transit_ff=transit_ff, transit_fbo=transit_fbo, transit_fbs=transit_fbs,
        organizer=organizer, container=container,
    )
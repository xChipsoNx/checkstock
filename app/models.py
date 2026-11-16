# app/models.py
from datetime import datetime
from sqlalchemy import (
    String, Integer, BigInteger, Boolean, ForeignKey, CheckConstraint, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime, Index, desc, false, text

class Base(DeclarativeBase):
    pass

class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("type IN ('WAREHOUSE','TRANSIT_FF','TRANSIT_FBO','TRANSIT_FBS')", name="location_type_chk"),
        UniqueConstraint("name", name="locations_name_uq"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("sku", name="products_sku_uq"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))

class Ledger(Base):
    __tablename__ = "ledger"
    __table_args__ = (
        CheckConstraint("qty_delta <> 0 OR reserve_delta <> 0", name="ledger_change_chk"),
        Index("ledger_product_created_idx", "product_id", desc("created_at")),
        Index("ledger_ref_idx", "ref_type", "ref_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    qty_delta: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    reserve_delta: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    kind: Mapped[str] = mapped_column(String(30))
    ref_type: Mapped[str | None] = mapped_column(String(30))
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (
        CheckConstraint("physical >= 0", name="physical_non_negative"),
        CheckConstraint("reserved_fbs >= 0", name="reserved_non_negative"),
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    physical: Mapped[int] = mapped_column(Integer, default=0,server_default=text("0"))
    reserved_fbs: Mapped[int] = mapped_column(Integer, default=0,server_default=text("0"))

class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("kind", "idempotency_key", name="operations_idempotency_uq"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("qty > 0", name="transfer_qty_positive"),
        CheckConstraint("status IN ('IN_TRANSIT','RECEIVED')", name="transfer_status_chk"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    from_location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    to_location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="IN_TRANSIT")
    operation_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
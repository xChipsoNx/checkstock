from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import OperationRequest, OperationResponse, StockRow
from app.services import stock


router = APIRouter(prefix="/api", tags=["stock"])


def idempotency_key(
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Передайте заголовок Idempotency-Key — он защищает от двойного списания",
        )
    return key


@router.get("/stock", response_model=list[StockRow])
def get_stock(db: Session = Depends(get_db)):
    rows = stock.stock_table(db)
    return [
        StockRow(
            sku=r.sku, location=r.name, type=r.type,
            physical=r.physical, reserved_fbs=r.reserved_fbs, available=r.available,
        )
        for r in rows
    ]


@router.post("/income", response_model=OperationResponse, status_code=status.HTTP_201_CREATED)
def income(
    payload: OperationRequest,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
):
    return OperationResponse(operation_id=stock.income(
        db, payload.product_id, payload.location_id, payload.qty, key))

@router.post("/reserve", response_model=OperationResponse, status_code=status.HTTP_201_CREATED)
def reserve(
    payload: OperationRequest,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
):
    return OperationResponse(operation_id=stock.reserve(
        db, payload.product_id, payload.location_id, payload.qty, key))

@router.post("/ship-fbs", response_model=OperationResponse, status_code=status.HTTP_201_CREATED)
def ship_fbs(
    payload: OperationRequest,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
):
    return OperationResponse(operation_id=stock.ship_fbs(
        db, payload.product_id, payload.location_id, payload.qty, key))


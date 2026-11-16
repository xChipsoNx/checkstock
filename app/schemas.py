from pydantic import BaseModel, Field


class OperationRequest(BaseModel):
    product_id: int
    location_id: int
    qty: int = Field(gt=0, description="Количество, больше нуля")


class OperationResponse(BaseModel):
    operation_id: int


class StockRow(BaseModel):
    sku: str
    location: str
    type: str
    physical: int
    reserved_fbs: int
    available: int
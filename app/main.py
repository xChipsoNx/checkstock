from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.routers import stock as stock_api
from app.services import stock as stock_service
from app.routers import pages


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

app = FastAPI(title="CheckStock", version="1.0", default_response_class=UTF8JSONResponse)
app.include_router(pages.router)

@app.get("/health", tags=["system"], include_in_schema=False)
@app.get("/api/health", tags=["system"])
def health():
    return {"status": "ok"}



app.include_router(stock_api.router)


@app.exception_handler(stock_service.NotFound)
async def not_found_handler(request: Request, exc: stock_service.NotFound):
    return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})


@app.exception_handler(stock_service.InsufficientStock)
async def insufficient_handler(request: Request, exc: stock_service.InsufficientStock):
    body = {
        "error": "insufficient_stock",
        "requested": exc.qty,
        "available_here": exc.available_here,
        "available_total": exc.available_total,
    }
    if exc.available_total >= exc.qty:
        body["suggestion"] = "На этом складе не хватает, но суммарно товар есть — создайте перемещение"
        body["can_move"] = True
    else:
        body["suggestion"] = "Товара не хватает на всех складах — нужно поступление или меньший заказ"
        body["can_move"] = False
    return JSONResponse(status_code=409, content=body)


@app.exception_handler(stock_service.CannotShip)
async def cannot_ship_handler(request: Request, exc: stock_service.CannotShip):
    return JSONResponse(
        status_code=409,
        content={
            "error": "cannot_ship",
            "reason": exc.reason,
            "physical": exc.physical,
            "reserved": exc.reserved,
        },
    )
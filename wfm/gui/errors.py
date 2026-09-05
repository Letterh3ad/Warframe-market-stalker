from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from wfm.services.errors import ApiError, CircuitOpen


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CircuitOpen)
    async def _circuit_open(request: Request, exc: CircuitOpen) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": str(exc)})

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"error": str(exc)})

    @app.exception_handler(LookupError)
    async def _lookup_error(request: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

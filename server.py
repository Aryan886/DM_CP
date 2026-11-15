"""StockRelate web server: a small JSON API plus the static Clay-styled frontend.

Run with:  python -m uvicorn server:app --port 8501
"""

import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from stockrelate import service
from stockrelate.config import DEFAULT_SETTINGS
from stockrelate.exports import all_exports
from stockrelate.market_data import DataValidationError

logger = logging.getLogger("stockrelate")
WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="StockRelate", docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Always revalidate the page and its assets (cheap via ETag) so updates are never stale."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class SelectionRequest(BaseModel):
    source: str
    dataset_key: str | None = None
    symbols: list[str] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None
    tick: int = Field(ge=0, le=100)


class PathRequest(SelectionRequest):
    path_from: str
    path_to: str


class SetsRequest(BaseModel):
    source: str
    dataset_key: str | None = None
    applied: list[str] = Field(default_factory=list)
    x: list[str] = Field(default_factory=list)
    y: list[str] = Field(default_factory=list)


def _user_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


def _analyze(request: SelectionRequest):
    try:
        source = service.load_source(request.source, request.dataset_key)
        prepared = service.prepare(source, request.symbols, request.start, request.end)
    except (DataValidationError, ValueError) as error:
        raise _user_error(error) from error
    try:
        result = service.run_analysis(prepared, request.tick)
    except service.ServiceError as error:
        raise _user_error(error) from error
    except Exception as error:  # application boundary: log, show an understandable message
        logger.exception("Analysis failed (data_id=%s, tick=%s)", prepared.data_id, request.tick)
        raise HTTPException(
            status_code=500,
            detail="The analysis failed unexpectedly. Details were written to the application log.",
        ) from error
    return source, prepared, result


@app.get("/api/config")
def get_config() -> dict:
    return service.app_config()


@app.get("/api/sources/{mode}")
def get_source(mode: str) -> dict:
    if mode == "upload":
        raise HTTPException(status_code=422, detail="Upload sources are created with POST /api/upload.")
    try:
        return service.source_payload(service.load_source(mode))
    except (DataValidationError, ValueError) as error:
        raise _user_error(error) from error


@app.post("/api/upload")
async def upload(metadata: UploadFile = File(...), prices: UploadFile = File(...)) -> dict:  # noqa: B008
    limit = DEFAULT_SETTINGS.max_csv_bytes
    metadata_bytes = await metadata.read(limit + 1)
    prices_bytes = await prices.read(limit + 1)
    try:
        return service.source_payload(service.register_upload(metadata_bytes, prices_bytes))
    except (DataValidationError, ValueError) as error:
        raise _user_error(error) from error


@app.post("/api/analyze")
def post_analyze(request: SelectionRequest) -> dict:
    source, prepared, result = _analyze(request)
    return service.analysis_payload(source, prepared, result)


@app.post("/api/sets")
def post_sets(request: SetsRequest) -> dict:
    try:
        source = service.load_source(request.source, request.dataset_key)
        return service.sets_payload(source, request.applied, request.x, request.y)
    except (DataValidationError, ValueError) as error:
        raise _user_error(error) from error


@app.post("/api/path")
def post_path(request: PathRequest) -> dict:
    _, prepared, result = _analyze(request)
    try:
        return service.path_payload(prepared, result, request.path_from, request.path_to)
    except ValueError as error:
        raise _user_error(error) from error


@app.post("/api/export/{filename}")
def post_export(filename: str, request: SelectionRequest) -> Response:
    _, prepared, result = _analyze(request)
    files = all_exports(result, prepared)
    if filename not in files:
        raise HTTPException(status_code=404, detail=f"Unknown export {filename!r}.")
    content, mime = files[filename]
    return Response(
        content=content.encode("utf-8"),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

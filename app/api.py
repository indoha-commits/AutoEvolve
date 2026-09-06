from fastapi import (
    Depends,
    FastAPI,
    Form,
    Request,
)
import os
from urllib.parse import urlparse

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agents.founder import run_founder
from app.security import authenticate
from core.branding import company_logo_url, company_name
from core.dashboard import get_dashboard_state, get_operations_page_state


app = FastAPI(
    title="Company Cockpit",
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)

templates = Jinja2Templates(
    directory="templates"
)
templates.env.globals.update(
    company_name=company_name(),
    company_logo_url=company_logo_url(),
)


def _calendar_base_url() -> str:
    return (
        os.getenv("SALES_CALENDAR_BASE_URL", "http://localhost:3000").strip()
        or "http://localhost:3000"
    ).rstrip("/")


def _calendar_event_path() -> str:
    raw = os.getenv("SALES_CALENDAR_EVENT_PATH", "book/company").strip() or "book/company"
    return raw if raw.startswith("/") else f"/{raw}"


def _calendar_expand_target(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    path = (parsed.path or "").rstrip("/")
    if parsed.scheme and parsed.netloc and path not in {"", "/"}:
        return raw
    if parsed.scheme and parsed.netloc:
        return f"{raw.rstrip('/')}{_calendar_event_path()}"
    return raw


def _calendar_booking_url() -> str:
    override = _calendar_expand_target(os.getenv("SALES_CALENDAR_BOOKING_URL", ""))
    if override:
        return override
    return f"{_calendar_base_url()}{_calendar_event_path()}"


def _calendar_embed_url() -> str | None:
    embed = _calendar_expand_target(os.getenv("SALES_CALENDAR_EMBED_URL", ""))
    if embed:
        return embed
    return _calendar_booking_url()


def _research_context(request: Request, *, result=None, error=None) -> dict:
    return {
        "request": request,
        "result": result,
        "error": error,
        **get_dashboard_state(),
    }


@app.get(
    "/",
    response_class=HTMLResponse,
)
async def home(
    request: Request,
    _: str = Depends(authenticate),
):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_research_context(request),
    )


@app.post(
    "/",
    response_class=HTMLResponse,
)
async def submit(
    request: Request,
    message: str = Form(...),
    _: str = Depends(authenticate),
):
    result = None
    error = None

    try:
        result = await run_founder(message)

    except Exception as exc:
        error = (
            f"{type(exc).__name__}: {exc}"
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_research_context(request, result=result, error=error),
    )


@app.get(
    "/operations",
    response_class=HTMLResponse,
)
async def operations(
    request: Request,
    _: str = Depends(authenticate),
):
    return templates.TemplateResponse(
        request=request,
        name="operations.html",
        context={
            "request": request,
            "page_mode": "operations",
            "active_nav": "operations",
            **get_operations_page_state(),
        },
    )


@app.get(
    "/operations/sales",
    response_class=HTMLResponse,
)
@app.get(
    "/operations/sales/discovery",
    response_class=HTMLResponse,
)
async def operations_sales(
    request: Request,
    _: str = Depends(authenticate),
):
    return templates.TemplateResponse(
        request=request,
        name="operations_sales.html",
        context={
            "request": request,
            "page_mode": "sales",
            "sales_view": "discovery",
            "active_nav": "sales",
            **get_operations_page_state(),
        },
    )


@app.get(
    "/operations/sales/email",
    response_class=HTMLResponse,
)
async def operations_sales_email(
    request: Request,
    _: str = Depends(authenticate),
):
    return templates.TemplateResponse(
        request=request,
        name="operations_sales.html",
        context={
            "request": request,
            "page_mode": "sales",
            "sales_view": "email",
            "active_nav": "sales",
            **get_operations_page_state(),
        },
    )


@app.get(
    "/operations/marketing",
    response_class=HTMLResponse,
)
@app.get(
    "/operations/marketing/campaigns",
    response_class=HTMLResponse,
)
async def operations_marketing(
    request: Request,
    _: str = Depends(authenticate),
):
    return templates.TemplateResponse(
        request=request,
        name="operations_marketing.html",
        context={
            "request": request,
            "page_mode": "marketing",
            "marketing_view": "campaigns",
            "active_nav": "marketing",
            **get_operations_page_state(),
        },
    )


@app.get(
    "/operations/marketing/assets",
    response_class=HTMLResponse,
)
async def operations_marketing_assets(
    request: Request,
    _: str = Depends(authenticate),
):
    return templates.TemplateResponse(
        request=request,
        name="operations_marketing.html",
        context={
            "request": request,
            "page_mode": "marketing",
            "marketing_view": "assets",
            "active_nav": "marketing",
            **get_operations_page_state(),
        },
    )


@app.get(
    "/operations/marketing/publishing",
    response_class=HTMLResponse,
)
async def operations_marketing_publishing(
    request: Request,
    _: str = Depends(authenticate),
):
    return templates.TemplateResponse(
        request=request,
        name="operations_marketing.html",
        context={
            "request": request,
            "page_mode": "marketing",
            "marketing_view": "publishing",
            "active_nav": "marketing",
            **get_operations_page_state(),
        },
    )


@app.get(
    "/operations/history",
    response_class=HTMLResponse,
)
async def operations_history(
    request: Request,
    _: str = Depends(authenticate),
):
    return templates.TemplateResponse(
        request=request,
        name="operations_history.html",
        context={
            "request": request,
            "page_mode": "history",
            "active_nav": "history",
            **get_operations_page_state(),
        },
    )


@app.get(
    "/meet",
    response_class=HTMLResponse,
)
async def meet(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="meet.html",
        context={
            "request": request,
            "calendar_page_url": "/calendar",
            "calendar_direct_url": _calendar_booking_url(),
        },
    )


@app.get(
    "/calendar",
    response_class=HTMLResponse,
)
async def calendar_page(request: Request):
    booking_url = _calendar_booking_url()
    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={
            "request": request,
            "calendar_booking_url": booking_url,
            "calendar_embed_url": _calendar_embed_url(),
            "calendar_base_url": _calendar_base_url(),
        },
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
    }


from app.company_ops_api import router as company_ops_router
from app.marketing_api import router as marketing_router
from app.sales_api import action_router as sales_action_router
from app.sales_api import intake_router as sales_intake_router
from app.sales_api import router as sales_router

app.include_router(company_ops_router, dependencies=[Depends(authenticate)])
app.include_router(marketing_router, dependencies=[Depends(authenticate)])
app.include_router(sales_router, dependencies=[Depends(authenticate)])
app.include_router(sales_action_router, dependencies=[Depends(authenticate)])
# Tunnel-facing integrations stay outside dashboard auth and enforce their own
# shared-secret checks in app.sales_api.
app.include_router(sales_intake_router)

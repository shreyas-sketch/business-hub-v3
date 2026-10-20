"""Business AI Action Hub — FastAPI entry point. Serves the API, member websites and the built React app."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import plans
from .config import settings
from .db import connect, ensure_indexes
from . import files as files_store, mailer
from .routers import (admin, admin_config, agents, auth, billing, brandkit, cashflow, compliance, dashboard, day1, desk, event, followups, domains, growth, hooks, hub, inquiries, gateway, kits, knowledge, leadideas, sitemagnets, membership, money,
                      office, onboard, program, public, recordings, rhythm, social, sopset, team, tools, workforce)
from .sites import rewrite_to_site, slug_from_host

FEATURE_ROUTERS = (kits, membership, team, billing, agents, money, office, recordings, admin_config, onboard, tools, rhythm,
                   program, desk, growth, hooks, workforce, domains, day1, knowledge, compliance, cashflow, followups, brandkit, sopset, social, leadideas, dashboard, inquiries, gateway, sitemagnets, event)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    connect()
    await ensure_indexes()
    for module in (*FEATURE_ROUTERS, files_store, mailer):
        if hasattr(module, "ensure_indexes"):
            await module.ensure_indexes()
    await plans.load(force=True)
    scheduler_task = day1_task = event_task = None
    if settings.run_scheduler and settings.env != "test":
        from .agents import scheduler
        scheduler_task = asyncio.create_task(scheduler.loop())
    if settings.env != "test":   # the Day-1 report queue (tests build reports with day1_runs.drain())
        from . import day1_runs
        day1_task = asyncio.create_task(day1_runs.loop())
        from . import event as event_packs   # the live event pack queue (tests build packs with event.drain())
        event_task = asyncio.create_task(event_packs.loop())
    yield
    for task in (scheduler_task, day1_task, event_task):
        if task:
            task.cancel()


app = FastAPI(title=settings.program_name, lifespan=lifespan, docs_url=None if settings.production else "/api/docs", redoc_url=None)


@app.middleware("http")
async def hosts(request: Request, call_next):
    """The admin's plan changes are picked up here, and requests that arrive on a member website's address
    (<name>.SITES_DOMAIN or an owner's own domain) are answered by that website only."""
    await plans.load()
    host = (request.headers.get("host") or "").split(":")[0].lower().rstrip(".")
    if host and host not in (settings.app_host, "localhost", "127.0.0.1", "testserver"):
        slug = slug_from_host(host)
        if slug:
            if not rewrite_to_site(request.scope, slug):
                return public.not_found()
            return await call_next(request)
        result = await domains.own_domain(request, host)
        if result is not None:
            if isinstance(result, str):
                if not rewrite_to_site(request.scope, result):
                    return public.not_found()
                return await call_next(request)
            return result
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response


app.include_router(auth.router)
app.include_router(hub.router)
app.include_router(admin.router)
app.include_router(public.router)
for _module in FEATURE_ROUTERS:
    app.include_router(_module.router)
app.include_router(day1.admin)
app.include_router(event.admin)
app.include_router(compliance.admin)
app.include_router(dashboard.admin)

if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    file = DIST / path
    if path and file.is_file() and DIST in file.resolve().parents:
        return FileResponse(file)
    index = DIST / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built. Run: cd frontend && npm install && npm run build"}, status_code=503)

"""AOAI Chat — FastAPI backend entry point."""
from __future__ import annotations

import os
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.routes import chat as chat_routes
from app.routes import config as config_routes
from app.routes import conversations as conversations_routes
from app.routes import deployments as deployments_routes


class NoCacheHTMLMiddleware(BaseHTTPMiddleware):
    """Stop browsers from caching the HTML shell.

    Vite produces content-hashed JS/CSS files (cacheable forever), but
    `index.html` references them by name. If the browser caches a stale
    `index.html` it'll keep asking for old, deleted bundle files and the
    app sticks on a previous build. Telling the browser to revalidate
    `index.html` on every load fixes that — at the cost of one extra
    304-revalidated round trip per page load, which is negligible.

    The hashed assets remain freely cacheable (their URLs change when
    content changes, so the browser will fetch the new file naturally).
    """

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    port = int(os.getenv("AOAI_PORT", "8765"))
    if os.getenv("AOAI_OPEN_BROWSER", "1") == "1" and os.getenv("AOAI_DEV") != "1":
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass
    yield


app = FastAPI(title="AOAI Chat", version="0.1.0", lifespan=lifespan)
app.add_middleware(NoCacheHTMLMiddleware)

app.include_router(config_routes.router)
app.include_router(deployments_routes.router)
app.include_router(chat_routes.router)
app.include_router(conversations_routes.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


# Production: serve built frontend from /
# Dev: Vite serves the frontend on :5173 and proxies /api back to us.
if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
else:
    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": "AOAI Chat",
                "status": "scaffold",
                "hint": "Run `npm run dev` in frontend/ for the UI, "
                "or `npm run build` then restart for production single-port mode.",
            }
        )


def run() -> None:
    """`aoai-chat` CLI entry point."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("AOAI_HOST", "127.0.0.1"),
        port=int(os.getenv("AOAI_PORT", "8765")),
        reload=os.getenv("AOAI_DEV") == "1",
    )


if __name__ == "__main__":
    run()

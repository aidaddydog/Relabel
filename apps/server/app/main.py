
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from .core.config import settings
from .routes_auth import router as auth_router
from .routes_client_api import router as client_router
from .routes_admin_api import router as admin_api_router
from .routes_pages import router as pages_router
from .routes_update_templates import router as update_tpl_router

app = FastAPI(title="Relabel Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, session_cookie=settings.SESSION_COOKIE_NAME)

# Routers
app.include_router(auth_router)
app.include_router(client_router)
app.include_router(admin_api_router)
app.include_router(update_tpl_router)
app.include_router(pages_router)

# Static frontend (built dist)
if os.path.isdir(settings.FRONTEND_DIST):
    app.mount("/static", StaticFiles(directory=settings.FRONTEND_DIST, html=False), name="static")

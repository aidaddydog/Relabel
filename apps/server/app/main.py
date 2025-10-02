import os

from fastapi import FastAPI
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

# ---- Middlewares ----
# CORS：允许来自配置中的来源；若未配置则退回通配
origins = getattr(settings, "CORS_ORIGINS", ["*"])
if isinstance(origins, str):
    origins = [o.strip() for o in origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 会话
app.add_middleware(
    SessionMiddleware,
    secret_key=getattr(settings, "SECRET_KEY", "relabel-secret"),
    session_cookie=getattr(settings, "SESSION_COOKIE", "relabel_session"),
)

# ---- Routers ----
# 登录/认证
app.include_router(auth_router)
# 客户端 API（/api/v1/...）
app.include_router(client_router)
# 管理端 API
app.include_router(admin_api_router)
# 更新模板相关
app.include_router(update_tpl_router)
# 页面与健康检查（含 /healthz 与前端入口）
app.include_router(pages_router)

# （可选）如果希望由 StaticFiles 兜底前端，也可以挂载，但 routes_pages 已经处理了首页与 /assets。
# if os.path.isdir(settings.FRONTEND_DIST):
#     app.mount("/", StaticFiles(directory=settings.FRONTEND_DIST, html=True), name="frontend")

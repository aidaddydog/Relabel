
import os
from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse
from .core.config import settings

router = APIRouter()

def _index_html():
    index_path = os.path.join(settings.FRONTEND_DIST, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse("<!doctype html><title>Relabel</title><h1>Relabel Admin</h1><p>Frontend not built yet.</p>")

# Serve SPA for admin pages
@router.get("/admin")
@router.get("/admin/")
@router.get("/admin/{path:path}")
async def admin_spa(path: str = ""):
    return _index_html()

# Health
@router.get("/healthz")
def healthz():
    return {"ok": True}

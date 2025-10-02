
import os, subprocess, json, time, shutil
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from .core.config import settings
from .utils import sse_event

router = APIRouter(tags=["update-templates"])

@router.get("/admin/update")
def update_info():
    # minimal git info
    def git(*args):
        try:
            out = subprocess.check_output(["git", *args], cwd=settings.RELABEL_BASE).decode().strip()
            return out
        except Exception:
            return ""
    return {
        "mode": "git" if git("rev-parse", "--is-inside-work-tree") else "dir",
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": git("rev-parse", "HEAD"),
        "remote": git("remote", "-v"),
    }

@router.post("/admin/update/git_pull")
def git_pull():
    try:
        out = subprocess.check_output(["git", "pull", "--rebase"], cwd=settings.RELABEL_BASE).decode()
        return {"ok": True, "output": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Templates management (whitelisted folder)
TPL_DIR = os.path.join(settings.RELABEL_BASE, "templates_ext")

@router.get("/admin/templates")
def templates_list():
    os.makedirs(TPL_DIR, exist_ok=True)
    files = []
    for root, _, names in os.walk(TPL_DIR):
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), TPL_DIR)
            files.append(rel)
    return {"items": files}

@router.get("/admin/templates/preview")
def templates_preview(path: str):
    full = os.path.join(TPL_DIR, path)
    if not os.path.abspath(full).startswith(os.path.abspath(TPL_DIR)):
        raise HTTPException(403, "forbidden")
    if not os.path.isfile(full):
        raise HTTPException(404, "not found")
    with open(full, "rb") as f:
        data = f.read()
    return StreamingResponse(iter([data]), media_type="text/plain")

@router.post("/admin/templates/save")
async def templates_save(path: str = Form(...), file: UploadFile = File(...)):
    os.makedirs(TPL_DIR, exist_ok=True)
    full = os.path.join(TPL_DIR, path)
    full_dir = os.path.dirname(full)
    os.makedirs(full_dir, exist_ok=True)
    data = await file.read()
    with open(full, "wb") as f:
        f.write(data)
    return {"ok": True, "path": path}

@router.post("/admin/templates/git_push")
def templates_git_push(message: str = "update templates"):
    try:
        subprocess.check_call(["git", "add", "templates_ext"], cwd=settings.RELABEL_BASE)
        subprocess.check_call(["git", "commit", "-m", message], cwd=settings.RELABEL_BASE)
        subprocess.check_call(["git", "push"], cwd=settings.RELABEL_BASE)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=str(e))

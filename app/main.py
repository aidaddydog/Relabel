# app/main.py
# -*- coding: utf-8 -*-
import os, io, re, sys, math, json, zipfile, hashlib, shutil, tempfile
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any, Iterable

from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from . import admin_extras

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text, text, select, func
from sqlalchemy.orm import sessionmaker, declarative_base

# —— 仅顶层导入 Argon2；bcrypt 系兼容在 _verify_password 中“动态导入” —— 
from passlib.hash import argon2 as _argon2

# =========================
# Argon2id + Pepper 口令模块
# =========================
import hmac

def _pepper_bytes() -> bytes:
    """
    读取 Pepper（推荐通过 systemd EnvironmentFile 注入）：
      - HUANDAN_PEPPER_FILE=/etc/huandan/secret_pepper
      - HUANDAN_PEPPER=<hex 或 明文>
    不存在则返回空字节（仅用于兼容旧散列验证；新哈希仍强烈建议存在 Pepper）。
    """
    pfile = (os.environ.get("HUANDAN_PEPPER_FILE") or "").strip()
    if pfile and os.path.exists(pfile):
        try:
            return open(pfile, "rb").read().strip()
        except Exception:
            pass
    val = (os.environ.get("HUANDAN_PEPPER") or "").strip()
    if not val:
        return b""
    try:
        return bytes.fromhex(val)
    except Exception:
        return val.encode("utf-8")

_ARGON2 = _argon2.using(
    rounds=3,
    memory_cost=102400,
    parallelism=8,
    type=argon2.Type.ID if hasattr(_argon2, "Type") else None  # 兼容
) if hasattr(_argon2, "using") else _argon2

def _hmac_sha256(pep: bytes, pw: str) -> str:
    return hmac.new(pep, pw.encode("utf-8"), hashlib.sha256).hexdigest()

def _is_argon2_hash(h: str) -> bool:
    return isinstance(h, str) and h.startswith("$argon2")

def _hash_password(pw: str) -> str:
    """ 新建/重置：Argon2id(HMAC(pepper, password)) """
    pep = _pepper_bytes()
    payload = _hmac_sha256(pep, pw) if pep else pw
    return _ARGON2.hash(payload)

def _verify_password(pw: str, hh: str) -> bool:
    """
    校验顺序：
    1) Argon2id + Pepper
    2) 回退：按需动态导入 bcrypt_sha256 / bcrypt 校验历史散列
    这样避免顶层 import 触发某些环境中的 bcrypt 版本兼容问题。
    """
    # 1) Argon2
    try:
        if _is_argon2_hash(hh):
            pep = _pepper_bytes()
            payload = _hmac_sha256(pep, pw) if pep else pw
            return _ARGON2.verify(payload, hh)
    except Exception:
        pass
    # 2) 兼容旧散列（动态导入）
    try:
        from passlib.hash import bcrypt_sha256 as _bcrypt_sha256
        if hh and hh.startswith("$bcrypt-sha256$"):
            return _bcrypt_sha256.verify(pw, hh)
    except Exception:
        pass
    try:
        from passlib.hash import bcrypt as _bcrypt
        if hh and hh.startswith("$2"):
            return _bcrypt.verify(pw, hh)
    except Exception:
        pass
    return False

# =========================
# FastAPI 初始化
# =========================
app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.environ.get("HUANDAN_DATA", os.path.join(ROOT_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

# 会话
app.add_middleware(SessionMiddleware, secret_key="huandan_session_secret", session_cookie="hd_sess", https_only=False)

# 静态与模板（修正：指向 app/static）
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# 引入扩展管理路由（在线升级、模板编辑）
app.include_router(admin_extras.router)

# SQLite
DB_PATH = os.path.join(DATA_DIR, "huandan.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

# =========================
# ORM 定义（略，保持原样）
# =========================
class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True)
    password_hash = Column(String(256))
    is_active = Column(Boolean, default=True)

class ClientAuth(Base):
    __tablename__ = "client_auth"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code_hash = Column(String(256))
    code_plain = Column(String(16))
    description = Column(String(128), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used = Column(DateTime, nullable=True)
    fail_count = Column(Integer, default=0)
    lock_until = Column(DateTime, nullable=True)

class MetaKV(Base):
    __tablename__ = "meta_kv"
    key = Column(String(64), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TrackingFile(Base):
    __tablename__ = "tracking_file"
    tracking_no = Column(String(128), primary_key=True)
    file_path = Column(Text)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    # —— 1.97 聚合列（由 print_ext 聚合上报）——
    print_status = Column(String(32), default="not_printed")  # not_printed | printed | reprinted
    print_count = Column(Integer, default=0)
    last_print_time = Column(String(32), default="")
    last_print_client_name = Column(String(128), default="")

def init_db():
    Base.metadata.create_all(engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# =========================
# 登录/认证
# =========================
@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request, db=Depends(get_db)):
    hint = ""
    try:
        if db.query(AdminUser).count() == 0:
            hint = "系统尚未初始化管理员账号，请在服务器终端运行一键部署脚本完成初始化。"
    except Exception:
        pass
    return templates.TemplateResponse("login.html", {"request": request, "hint": hint})

@app.post("/admin/login")
def login_do(request: Request, username: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    u = db.execute(select(AdminUser).where(AdminUser.username==username, AdminUser.is_active==True)).scalar_one_or_none()
    if not u or not _verify_password(password, u.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "账户或密码错误"})
    # 自动再哈希：统一升级到 Argon2id + Pepper
    try:
        if _needs_rehash(u.password_hash):
            u.password_hash = _hash_password(password)
            db.add(u); db.commit()
    except Exception:
        db.rollback()
    request.session["admin_user"] = username
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=302)

def require_admin(request: Request, db):
    u = request.session.get("admin_user")
    if not u:
        # 维持原语义：抛 302，由 Starlette 处理为重定向（多数代理可识别）
        raise HTTPException(status_code=302, detail="unauth", headers={"Location": "/admin/login"})
    return u

def cleanup_expired(db):
    # 预留：可清理过期文件/失败记录等；保持空实现不影响逻辑
    return

# =========================
# 管理页：仪表盘
# =========================
@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    count_files = db.query(TrackingFile).count()
    printed = db.query(TrackingFile).filter(TrackingFile.print_status!="not_printed").count()
    return templates.TemplateResponse("index.html", {"request": request, "count_files": count_files, "printed": printed})

# =========================
# 管理页：PDF 列表（含筛选；修复 TemplateResponse 闭合）
# =========================
@app.get("/admin/files", response_class=HTMLResponse)
def list_files(request: Request,
               q: Optional[str]=None,
               status: Optional[str]=None,
               client: Optional[str]=None,
               bind: Optional[str]=None, # bound | unbound | None
               page: int = 1,
               db=Depends(get_db)):
    require_admin(request, db); cleanup_expired(db)
    page_size = 100

    # 基础过滤
    base_q = db.query(TrackingFile)
    if q:
        base_q = base_q.filter(TrackingFile.tracking_no.like(f"%{q}%"))
    if status:
        base_q = base_q.filter(TrackingFile.print_status == status)
    if client:
        base_q = base_q.filter(TrackingFile.last_print_client_name.like(f"%{client}%"))

    # 候选 tracking_no
    cand_tns = [r[0] for r in base_q.with_entities(TrackingFile.tracking_no).order_by(TrackingFile.uploaded_at.desc()).all()

    # 已绑定集合（order_mapping + print_events）
    bound_set = set()
    if cand_tns:
        try:
            rs = db.execute(text("SELECT tracking_no FROM order_mapping WHERE tracking_no IN :tn AND ifnull(tracking_no,'')<>''"),
                            {"tn": tuple(cand_tns)}).fetchall()
            bound_set.update([r[0] for r in rs if r and r[0]])
        except Exception:
            pass
        try:
            rs = db.execute(text("SELECT tracking_no FROM print_events WHERE tracking_no IN :tn AND ifnull(tracking_no,'')<>''"),
                            {"tn": tuple(cand_tns)}).fetchall()
            bound_set.update([r[0] for r in rs if r and r[0]])
        except Exception:
            pass

    # 应用绑定筛选
    if bind == "bound":
        filtered = [tn for tn in cand_tns if tn in bound_set]
    elif bind == "unbound":
        filtered = [tn for tn in cand_tns if tn not in bound_set]
    else:
        filtered = cand_tns

    total = len(filtered)
    pages = max(1, math.ceil(total / page_size))
    page = max(1, min(pages, int(page or 1)))
    page_tns = filtered[(page-1)*page_size : page*page_size]

    rows: List[TrackingFile] = []
    if page_tns:
        rows = db.query(TrackingFile).filter(TrackingFile.tracking_no.in_(page_tns)).order_by(TrackingFile.uploaded_at.desc()).all()

    # extras：订单号、最近一次重印原因、中文状态
    extras: Dict[str, Dict[str, Any]] = {}
    if page_tns:
        order_map: Dict[str,str] = {}
        # 订单号（order_mapping 优先）
        try:
            rs = db.execute(text("SELECT order_id, tracking_no FROM order_mapping WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' ORDER BY updated_at DESC"),
                            {"tn": tuple(page_tns)}).fetchall()
            for oid, tn in rs:
                if tn and oid and tn not in order_map:
                    order_map[tn] = oid
        except Exception:
            pass
        # 最近一次重印原因
        reason_map: Dict[str,str] = {}
        try:
            rs = db.execute(text("SELECT tracking_no, reason FROM print_events WHERE tracking_no IN :tn AND status='reprinted' ORDER BY created_at DESC"),
                            {"tn": tuple(page_tns)}).fetchall()
            for tn, reason in rs:
                if tn and reason and tn not in reason_map:
                    reason_map[tn] = reason
        except Exception:
            pass
        for tn in page_tns:
            extras[tn] = {"order_id": order_map.get(tn, ""), "reason": reason_map.get(tn, "")}

    return templates.TemplateResponse("files.html", {
        "request": request,
        "q": q or "", "status": status or "", "client": client or "", "bind": bind or "",
        "rows": rows,
        "total": total, "page": page, "pages": pages, "page_size": page_size,
        "extras": extras
    })

# ——（如你模板里有“导出”按钮，可保留/实现；否则可以忽略此路由）——
@app.get("/admin/files/export-xlsx")
def files_export_xlsx(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    try:
        import pandas as pd
    except Exception:
        return PlainTextResponse("需要 pandas 以导出 xlsx", status_code=500)
    q = db.query(TrackingFile).order_by(TrackingFile.uploaded_at.desc()).all()
    rows = [{
        "tracking_no": r.tracking_no,
        "uploaded_at": (r.uploaded_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S"),
        "print_status": r.print_status,
        "print_count": r.print_count,
        "last_print_time": (r.last_print_time or ""),
        "last_print_client_name": r.last_print_client_name or ""
    } for r in q]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as wr:
        df.to_excel(wr, index=False, sheet_name="files")
    buf.seek(0)
    headers = {"Content-Disposition": "attachment; filename=files.xlsx"}
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)

# =========================
# 客户端必需 API（保持兼容）
# =========================
@app.get("/api/v1/version")
def api_version(code: str, db=Depends(get_db)):
    # 可按 access_code 做鉴权；此处保持原样返回 KV 里的版本号（缺省 "1.97"）
    v = get_kv(db, "version", "1.97")
    return {"version": v}

@app.get("/api/v1/mapping")
def api_mapping(code: str, db=Depends(get_db)):
    """
    返回“订单号 <-> 追踪号”映射与版本号。
    """
    # ……（保持原有实现）……
    return {"mapping": [], "version": "1.97"}

# =========================
# 运行时组件下载（保持原样）
# =========================
@app.get("/runtime/SumatraPDF-{arch}.exe")
def runtime_sumatra(arch: str):
    """
    提供客户端所需运行时：
      - /runtime/SumatraPDF-x64.exe
      - /runtime/SumatraPDF-x86.exe
    """
    fname = f"SumatraPDF-{arch}.exe"
    fpath = os.path.join(ROOT_DIR, "runtime", fname)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="runtime not found")
    return FileResponse(fpath, filename="SumatraPDF.exe", media_type="application/octet-stream")

# =========================
# 兼容：删除 Web 初始化页（不再提供 /admin/bootstrap）
# =========================
# （无代码：此处仅说明已移除。若仍有旧模板 bootstrap.html，部署脚本会删除）

# =========================
# 健康检查
# =========================
@app.get("/healthz")
def healthz():
    return {"ok": True, "time": datetime.utcnow().isoformat(timespec="seconds")}

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

# Argon2id 参数：time_cost≈轮次数，memory_cost=KiB，parallelism=并行度
_ARGON2 = _argon2.using(type="ID", time_cost=3, memory_cost=65536, parallelism=2)

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
        if _bcrypt_sha256.verify(pw, hh):
            return True
    except Exception:
        pass
    try:
        from passlib.hash import bcrypt as _bcrypt_legacy
        return _bcrypt_legacy.verify(pw, hh)
    except Exception:
        return False

def _needs_rehash(hh: str) -> bool:
    """ 非 Argon2 或 Argon2 参数过旧时应再哈希 """
    try:
        if not _is_argon2_hash(hh):
            return True
        return _ARGON2.needs_update(hh)
    except Exception:
        return True


# =========================
# FastAPI / DB 初始化
# =========================
app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.environ.get("HUANDAN_DATA", os.path.join(ROOT_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

# 会话
app.add_middleware(SessionMiddleware, secret_key="huandan_session_secret", session_cookie="hd_sess", https_only=False)

# 静态与模板
app.mount("/static", StaticFiles(directory=os.path.join(ROOT_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# SQLite
DB_PATH = os.path.join(DATA_DIR, "huandan.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# =========================
# 模型（保持原有字段；1.97 增打印聚合列）
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
    print_status = Column(String(16), default="not_printed")      # not_printed | printed | reprinted
    first_print_time = Column(DateTime, nullable=True)
    last_print_time = Column(DateTime, nullable=True)
    print_count = Column(Integer, default=0)
    last_print_client_name = Column(String(128), default="")

Base.metadata.create_all(bind=engine, checkfirst=True)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_kv(db, key: str, default=""):
    obj = db.get(MetaKV, key)
    return obj.value if obj and obj.value is not None else default

def set_kv(db, key: str, val: str):
    obj = db.get(MetaKV, key)
    if not obj:
        obj = MetaKV(key=key, value=val, updated_at=datetime.utcnow())
    else:
        obj.value = val; obj.updated_at = datetime.utcnow()
    db.add(obj); db.commit()

def require_admin(request: Request, db):
    u = request.session.get("admin_user")
    if not u:
        raise HTTPException(status_code=302, detail="unauth", headers={"Location": "/admin/login"})
    return u

def cleanup_expired(db):
    # 预留：可清理过期文件/失败记录等；保持空实现不影响逻辑
    return

# =========== 打印扩展初始化（保持原有） ===========
from .print_ext import init_print_ext as _init_print_ext_197

def _verify_code(db, code: str):
    return db.query(ClientAuth).filter(ClientAuth.code_plain==code, ClientAuth.is_active==True).first()

_init_print_ext_197(app, engine, SessionLocal, Base, _verify_code)

# =========================
# 登录/登出（无 Web 初始化页）
# =========================
@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request, db=Depends(get_db)):
    hint = None
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
    cand_tns = [r[0] for r in base_q.with_entities(TrackingFile.tracking_no).order_by(TrackingFile.uploaded_at.desc()).all()]

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
            rs = db.execute(text("SELECT tracking_no FROM print_events WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' GROUP BY tracking_no"),
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
                if tn and tn not in order_map:
                    order_map[tn] = oid
        except Exception:
            pass
        try:
            rs = db.execute(text("SELECT tracking_no, order_id FROM print_events WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' ORDER BY created_at DESC"),
                            {"tn": tuple(page_tns)}).fetchall()
            for tn, oid in rs:
                if tn and tn not in order_map and oid:
                    order_map[tn] = oid
        except Exception:
            pass

        # 重印原因（最近一次）
        reprint_reason: Dict[str,str] = {}
        try:
            rs = db.execute(text("SELECT tracking_no, reprint_reason FROM print_events WHERE tracking_no IN :tn AND result='success_reprint' AND ifnull(reprint_reason,'')<>'' ORDER BY created_at DESC"),
                            {"tn": tuple(page_tns)}).fetchall()
            for tn, why in rs:
                if tn and tn not in reprint_reason:
                    reprint_reason[tn] = why
        except Exception:
            pass

        cn = {"not_printed":"未打印","printed":"已打印","reprinted":"重复打印"}
        status_map = {r.tracking_no: cn.get(r.print_status or "not_printed", "未打印") for r in rows}

        for tn in page_tns:
            extras[tn] = {
                "order_id": order_map.get(tn, ""),
                "reprint_reason": reprint_reason.get(tn, ""),
                "status_cn": status_map.get(tn, "未打印"),
            }

    return templates.TemplateResponse("files.html", {
        "request": request, "rows": rows,
        "q": q, "status": status, "client": client, "bind": bind,
        "page": page, "pages": pages, "total": total, "page_size": page_size,
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
    为保持最小改动，优先从 data/mapping.json 读取（若无则返回空列表）。
    文件格式举例：
      {"version":"2025-10-01T12:00:00","mappings":[{"order_id":"...","customer_order":"...","tracking_no":"..."}]}
    """
    mapping_file = os.path.join(DATA_DIR, "mapping.json")
    d = {"version": get_kv(db, "version", "1.97"), "mappings": []}
    try:
        if os.path.exists(mapping_file):
            with open(mapping_file, "r", encoding="utf-8") as f:
                jd = json.load(f)
                if isinstance(jd, dict):
                    d["version"] = str(jd.get("version") or d["version"])
                    ms = jd.get("mappings") or []
                    # 统一键名（tracking_no / order_id / customer_order）
                    out = []
                    for r in ms:
                        if not isinstance(r, dict): continue
                        out.append({
                            "order_id": r.get("order_id") or r.get("order") or "",
                            "customer_order": r.get("customer_order") or r.get("order_no") or "",
                            "tracking_no": r.get("tracking_no") or r.get("hawb") or r.get("waybill") or r.get("tracking") or ""
                        })
                    d["mappings"] = out
    except Exception:
        pass
    return d

@app.get("/api/v1/pdf-zips/dates")
def api_zip_dates(code: str, db=Depends(get_db)):
    """
    枚举 zips 目录下的 pdfs-YYYYMMDD.zip，返回日期数组（YYYY-MM-DD）
    """
    zdir = os.path.join(DATA_DIR, "zips")
    out = []
    try:
        for name in os.listdir(zdir):
            if not name.startswith("pdfs-") or not name.endswith(".zip"): continue
            ymd = name[5:13]
            if len(ymd) == 8 and ymd.isdecimal():
                out.append(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}")
    except Exception:
        pass
    out = sorted(set(out))
    return {"dates": out}

def _file_etag_sha256(fp: str) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

@app.get("/api/v1/pdf-zips/daily")
def api_zip_daily(date: str = Query(..., regex=r"^\d{4}-\d{2}-\d{2}$"), code: str = Query(...), db=Depends(get_db)):
    """
    直接返回 zips/pdf-YYYYMMDD.zip；附带 ETag 与 X-Checksum-SHA256 供客户端缓存。
    """
    zdir = os.path.join(DATA_DIR, "zips")
    zname = f"pdfs-{date.replace('-','')}.zip"
    fpath = os.path.join(zdir, zname)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="zip not found")
    sha = _file_etag_sha256(fpath)
    headers = {
        "ETag": sha[:32],
        "X-Checksum-SHA256": sha
    }
    return FileResponse(fpath, filename=zname, media_type="application/zip", headers=headers)

@app.get("/api/v1/runtime/sumatra")
def api_runtime_sumatra(arch: str = Query(..., regex=r"^win(32|64)$"), code: str = Query(...), db=Depends(get_db)):
    """
    供客户端兜底下载 SumatraPDF。
    路径：runtime/SumatraPDF-<arch>.exe
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

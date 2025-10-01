# app/main.py
# -*- coding: utf-8 -*-
"""
Huandan Server - Web 管理端（选项B：保留 _nav.html 统一导航）
修复点：
- /static 指向 app/static，样式/脚本不再 404
- 统一页面模板：include _nav.html + <div class="container">
- 补齐缺失管理页与 API：订单导入三步（含SSE）、PDF导入（含SSE）、ZIP列表、订单/文件/客户端/设置
- 接入 admin_extras.router：模板列表/编辑、在线升级
- /admin/bootstrap 已正式移除（用 CLI 初始化或默认账号）
"""
import os, io, re, sys, json, zipfile, hashlib, shutil, tempfile, asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Iterable

from fastapi import FastAPI, Request, Depends, UploadFile, File, Form, Query, HTTPException
from fastapi.responses import (
    HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse,
    FileResponse, PlainTextResponse
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from passlib.context import CryptContext

# ---------------- 基础路径 ----------------
app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
DATA_DIR = os.environ.get("HUANDAN_DATA", os.path.join(ROOT_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "pdfs"), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "uploads"), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "zips"), exist_ok=True)

# 会话与模板/静态
app.add_middleware(SessionMiddleware, secret_key="huandan_session_secret", session_cookie="hd_sess", https_only=False)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ---------------- 数据库 ----------------
DB_PATH = os.path.join(DATA_DIR, "huandan.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True)
    password_hash = Column(String(256))
    is_active = Column(Boolean, default=True)

class MetaKV(Base):
    __tablename__ = "meta_kv"
    key = Column(String(64), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow)

class ClientAuth(Base):
    __tablename__ = "client_auth"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code_hash = Column(String(256), default="")
    code_plain = Column(String(16), default="")
    description = Column(String(128), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used = Column(DateTime, nullable=True)

class TrackingFile(Base):
    __tablename__ = "tracking_file"
    tracking_no = Column(String(128), primary_key=True)
    file_path = Column(Text, default="")
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    # 由客户端打印上报聚合的字段
    print_status = Column(String(16), default="not_printed")   # not_printed | printed | reprinted
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

# ---------------- 密码 ----------------
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
def _hash_password(pw: str) -> str:
    return _pwd.hash(pw)
def _verify_password(pw: str, ph: str) -> bool:
    try:
        return _pwd.verify(pw, ph)
    except Exception:
        return False

# admin_cli 复用
__all__ = ["SessionLocal", "AdminUser", "_hash_password"]

# ---------------- KV ----------------
def get_kv(db: Session, key: str, default: str = "") -> str:
    obj = db.get(MetaKV, key)
    return obj.value if obj and obj.value is not None else default

def set_kv(db: Session, key: str, val: str):
    obj = db.get(MetaKV, key)
    if not obj:
        obj = MetaKV(key=key, value=val, updated_at=datetime.utcnow())
        db.add(obj)
    else:
        obj.value = val
        obj.updated_at = datetime.utcnow()
    db.commit()

# ---------------- 会话校验 ----------------
def _ensure_admin(request: Request) -> Optional[RedirectResponse]:
    if not request.session.get("admin_user"):
        return RedirectResponse("/admin/login", status_code=302)
    return None

# ---------------- 映射文件（订单<->追踪） ----------------
MAP_FILE = os.path.join(DATA_DIR, "mapping.json")

def _load_mapping() -> Dict[str, Any]:
    if not os.path.exists(MAP_FILE):
        return {"version": "1.0", "mappings": []}
    try:
        with open(MAP_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict) and "mappings" in d:
                return d
    except Exception:
        pass
    return {"version": "1.0", "mappings": []}

def _save_mapping(d: Dict[str, Any]):
    tmp = MAP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MAP_FILE)

# ---------------- 登录/登出 ----------------
@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request, db=Depends(get_db)):
    # 首次无管理员时，创建默认账号（方便首登）
    if db.query(AdminUser).count() == 0:
        u = AdminUser(username="daddy", password_hash=_hash_password("20240314AaA#"), is_active=True)
        db.add(u); db.commit()
        hint = "已自动创建默认账号：daddy / 20240314AaA#"
    else:
        hint = None
    return templates.TemplateResponse("login.html", {"request": request, "hint": hint, "error": None})

@app.post("/admin/login")
def do_login(request: Request, username: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    u = db.query(AdminUser).filter(AdminUser.username == username, AdminUser.is_active == True).first()
    if not u or not _verify_password(password, u.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "账户或密码错误", "hint": None})
    request.session["admin_user"] = username
    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=302)

# ---------------- 仪表盘 ----------------
@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    mapping = _load_mapping()
    stats = {
        "order_count": len(mapping.get("mappings", [])),
        "file_count": db.query(TrackingFile).count(),
        "client_count": db.query(ClientAuth).count(),
        "version": mapping.get("version") or "1.0",
        "server_version": get_kv(db, "server_version", "1.97"),
        "client_recommend": get_kv(db, "client_recommend", "1.97"),
        "o_days": get_kv(db, "retention_orders_days", "90"),
        "f_days": get_kv(db, "retention_files_days", "90"),
    }
    return templates.TemplateResponse("dashboard.html", {"request": request, "stats": stats})

# ---------------- PDF 文件列表 + 对齐 ----------------
@app.get("/admin/files", response_class=HTMLResponse)
def list_files(request: Request,
               q: Optional[str]=None,
               status: Optional[str]=None,
               client: Optional[str]=None,
               bind: Optional[str]=None,  # bound | unbound | None
               page: int = 1,
               db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    page_size = 100

    base_q = db.query(TrackingFile)
    if q:
        base_q = base_q.filter(TrackingFile.tracking_no.like(f"%{q}%"))
    if status:
        base_q = base_q.filter(TrackingFile.print_status == status)
    if client:
        base_q = base_q.filter(TrackingFile.last_print_client_name.like(f"%{client}%"))

    # 读取 mapping.json，构造 bound 集合
    m = _load_mapping()
    order_map: Dict[str, str] = {}
    for r in m.get("mappings", []):
        tn = (r.get("tracking_no") or "").strip()
        oid = (r.get("order_id") or r.get("customer_order") or "").strip()
        if tn and oid and tn not in order_map:
            order_map[tn] = oid

    if bind == "bound":
        base_q = base_q.filter(TrackingFile.tracking_no.in_(list(order_map.keys())))
    elif bind == "unbound":
        if order_map:
            base_q = base_q.filter(~TrackingFile.tracking_no.in_(list(order_map.keys())))

    total = base_q.count()
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    rows = base_q.order_by(TrackingFile.uploaded_at.desc()) \
                 .offset((page - 1) * page_size).limit(page_size).all()

    # 附加信息（状态中文等）
    extras: Dict[str, Dict[str, str]] = {}
    for r in rows:
        st_cn = "未打印"
        if r.print_status == "printed": st_cn = "已打印"
        elif r.print_status == "reprinted": st_cn = "重复打印"
        extras[r.tracking_no] = {
            "order_id": order_map.get(r.tracking_no, ""),
            "status_cn": st_cn,
            "reprint_reason": ""   # 如需显示重复原因，可在客户端上报同步到服务器后填充
        }

    return templates.TemplateResponse("files.html", {
        "request": request,
        "rows": rows, "extras": extras,
        "q": q or "", "status": status or "", "client": client or "", "bind": bind or "",
        "page": page, "pages": pages, "page_size": page_size, "total": total
    })

@app.get("/admin/files/export-xlsx")
def files_export_xlsx(request: Request, db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
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
        "last_print_time": (r.last_print_time or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S"),
        "last_print_client_name": r.last_print_client_name or "",
    } for r in q]
    import io as _io
    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        pd.DataFrame(rows).to_excel(w, index=False, sheet_name="files")
    buf.seek(0)
    headers = {"Content-Disposition": "attachment; filename=files.xlsx"}
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)

@app.post("/admin/reconcile")
def reconcile_files(request: Request, db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    pdf_dir = os.path.join(DATA_DIR, "pdfs")
    os.makedirs(pdf_dir, exist_ok=True)
    files = []
    for root, _, fs in os.walk(pdf_dir):
        for name in fs:
            if name.lower().endswith(".pdf"):
                files.append(os.path.join(root, name))
    # 规范化文件名：<TRACKING_NO>.pdf
    for fp in files:
        dn, name = os.path.dirname(fp), os.path.basename(fp)
        tn = os.path.splitext(name)[0]
        # 仅保留字母数字与横杆/下划线
        norm_tn = re.sub(r"[^0-9A-Za-z_-]", "", tn)
        norm_name = f"{norm_tn}.pdf"
        if name != norm_name:
            dst = os.path.join(dn, norm_name)
            if not os.path.exists(dst):
                os.rename(fp, dst)
    # 补登记
    for name in os.listdir(pdf_dir):
        if not name.lower().endswith(".pdf"): continue
        tn = os.path.splitext(name)[0]
        row = db.get(TrackingFile, tn)
        if not row:
            db.add(TrackingFile(tracking_no=tn, file_path=os.path.join("pdfs", name), uploaded_at=datetime.utcnow()))
    db.commit()
    return RedirectResponse("/admin/files", status_code=302)

# ---------------- 订单列表（基于 mapping.json） ----------------
@app.get("/admin/orders", response_class=HTMLResponse)
def orders_page(request: Request, q: Optional[str]=None, page: int = 1, db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    m = _load_mapping()
    rows = m.get("mappings", [])
    if q:
        k = q.strip().lower()
        rows = [r for r in rows if k in (r.get("order_id","")+r.get("customer_order","")+r.get("tracking_no","")).lower()]
    rows = list(reversed(rows))  # 新的在前
    page_size = 100
    total = len(rows)
    pages = max(1, (total + page_size - 1)//page_size)
    page = max(1, min(page, pages))
    page_rows = rows[(page-1)*page_size: page*page_size]
    return templates.TemplateResponse("orders.html", {
        "request": request, "rows": page_rows,
        "q": q or "", "page": page, "pages": pages, "total": total
    })

@app.get("/admin/orders/export-xlsx")
def orders_export(request: Request):
    redir = _ensure_admin(request)
    if redir: return redir
    try:
        import pandas as pd
    except Exception:
        return PlainTextResponse("需要 pandas 以导出 xlsx", status_code=500)
    m = _load_mapping()
    df = pd.DataFrame(m.get("mappings", []))
    import io as _io
    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="mappings")
    buf.seek(0)
    headers = {"Content-Disposition": "attachment; filename=orders-mapping.xlsx"}
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)

@app.post("/admin/orders/edit-binding")
def orders_edit_binding(request: Request, tracking_no: str = Form(...), order_id: str = Form(""), customer_order: str = Form("")):
    redir = _ensure_admin(request)
    if redir: return redir
    m = _load_mapping()
    arr = m.get("mappings", [])
    found = False
    for r in arr:
        if (r.get("tracking_no") or "").strip() == tracking_no.strip():
            r["order_id"] = order_id.strip()
            r["customer_order"] = customer_order.strip()
            found = True
            break
    if not found:
        arr.append({"order_id": order_id.strip(), "customer_order": customer_order.strip(), "tracking_no": tracking_no.strip()})
    m["version"] = datetime.utcnow().isoformat(timespec="seconds")
    _save_mapping(m)
    return RedirectResponse("/admin/orders", status_code=302)

@app.post("/admin/orders/unbind")
def orders_unbind(request: Request, tracking_no: str = Form(...)):
    redir = _ensure_admin(request)
    if redir: return redir
    m = _load_mapping()
    arr = [r for r in m.get("mappings", []) if (r.get("tracking_no") or "").strip() != tracking_no.strip()]
    m["mappings"] = arr
    m["version"] = datetime.utcnow().isoformat(timespec="seconds")
    _save_mapping(m)
    return RedirectResponse("/admin/orders", status_code=302)

@app.post("/admin/orders/batch_delete_all")
def orders_batch_delete(request: Request, q: str = Form("")):
    redir = _ensure_admin(request)
    if redir: return redir
    m = _load_mapping()
    if q:
        k = q.strip().lower()
        arr = [r for r in m.get("mappings", []) if k not in (r.get("order_id","")+r.get("customer_order","")+r.get("tracking_no","")).lower()]
    else:
        arr = []
    m["mappings"] = arr
    m["version"] = datetime.utcnow().isoformat(timespec="seconds")
    _save_mapping(m)
    return RedirectResponse("/admin/orders", status_code=302)

# ---------------- 订单导入（3步 + SSE 应用） ----------------
@app.get("/admin/upload-orders", response_class=HTMLResponse)
def upload_orders_page(request: Request):
    redir = _ensure_admin(request)
    if redir: return redir
    return templates.TemplateResponse("upload_orders.html", {"request": request, "err": ""})

@app.post("/admin/upload-orders-step1")
async def upload_orders_step1(request: Request, file: UploadFile = File(...)):
    redir = _ensure_admin(request)
    if redir: return redir
    try:
        import pandas as pd
    except Exception:
        return templates.TemplateResponse("upload_orders.html", {"request": request, "err": "需要 pandas/openpyxl 才能解析 Excel/CSV"})
    suffix = (file.filename or "").lower()
    buf = await file.read()
    tmp_dir = os.path.join(DATA_DIR, "uploads"); os.makedirs(tmp_dir, exist_ok=True)
    token = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    tmp_path = os.path.join(tmp_dir, f"orders-{token}")
    with open(tmp_path, "wb") as f:
        f.write(buf)
    # 读列名
    try:
        if suffix.endswith(".csv"):
            df = pd.read_csv(tmp_path, nrows=50)
        else:
            df = pd.read_excel(tmp_path, nrows=50)
    except Exception as e:
        return templates.TemplateResponse("upload_orders.html", {"request": request, "err": f"文件解析失败：{e}"})
    cols = list(df.columns)
    return templates.TemplateResponse("choose_columns.html", {"request": request, "columns": cols, "token": token})

@app.post("/admin/upload-orders-step2")
def upload_orders_step2(request: Request, order_col: str = Form(...), tracking_col: str = Form(...), token: str = Form(...)):
    redir = _ensure_admin(request)
    if redir: return redir
    import pandas as pd
    tmp_path = os.path.join(DATA_DIR, "uploads", f"orders-{token}")
    if not os.path.exists(tmp_path):
        return templates.TemplateResponse("upload_orders.html", {"request": request, "err": "临时文件不存在或已过期"})
    # 展示前50行预览
    try:
        if tmp_path.lower().endswith(".csv"):
            df = pd.read_csv(tmp_path, dtype=str)
        else:
            df = pd.read_excel(tmp_path, dtype=str)
    except Exception as e:
        return templates.TemplateResponse("upload_orders.html", {"request": request, "err": f"文件解析失败：{e}"})
    rows = []
    for i, r in df.head(50).iterrows():
        rows.append([(str(r.get(order_col) or "")).strip(), (str(r.get(tracking_col) or "")).strip()])
    return templates.TemplateResponse("preview_orders.html", {"request": request, "rows": rows, "token": token, "order_col": order_col, "tracking_col": tracking_col})

@app.get("/admin/api/orders-apply")
async def orders_apply(request: Request, token: str, order_col: str = Query(...), tracking_col: str = Query(...)):
    # SSE：读取临时文件，写入 mapping.json
    async def gen():
        try:
            import pandas as pd
        except Exception:
            yield f"data: {json.dumps({'phase':'error','msg':'需要 pandas/openpyxl'})}\n\n"
            return
        tmp_path = os.path.join(DATA_DIR, "uploads", f"orders-{token}")
        if not os.path.exists(tmp_path):
            yield f"data: {json.dumps({'phase':'error','msg':'临时文件不存在或已过期'})}\n\n"
            return
        if tmp_path.lower().endswith(".csv"):
            df = pd.read_csv(tmp_path, dtype=str)
        else:
            df = pd.read_excel(tmp_path, dtype=str)
        total = len(df)
        yield f"data: {json.dumps({'phase':'read','total':total})}\n\n"
        m = _load_mapping()
        arr = m.get("mappings", [])
        done = 0
        for _, r in df.iterrows():
            oid = (str(r.get(order_col) or "")).strip()
            tn  = (str(r.get(tracking_col) or "")).strip()
            if not tn or not oid: 
                continue
            # 去重：同 tracking_no 只保留最新
            kept = [x for x in arr if (x.get("tracking_no") or "") != tn]
            kept.append({"order_id": oid, "customer_order": "", "tracking_no": tn})
            arr = kept
            done += 1
            if done % 50 == 0:
                yield f"data: {json.dumps({'phase':'progress','done':done,'total':total})}\n\n"
                await asyncio.sleep(0.001)
                if await request.is_disconnected(): return
        m["mappings"] = arr
        m["version"] = datetime.utcnow().isoformat(timespec="seconds")
        _save_mapping(m)
        yield f"data: {json.dumps({'phase':'done','count':done})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

# ---------------- PDF 导入（上传 + SSE 应用） ----------------
@app.get("/admin/upload-pdf", response_class=HTMLResponse)
def upload_pdf_page(request: Request):
    redir = _ensure_admin(request)
    if redir: return redir
    return templates.TemplateResponse("upload_pdf.html", {"request": request})

@app.post("/admin/api/upload-pdf-file")
async def api_upload_pdf_file(request: Request, zipfile_upload: UploadFile = File(...)):
    redir = _ensure_admin(request)
    if redir: return redir
    fn = zipfile_upload.filename or "upload.zip"
    token = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    tmp = os.path.join(DATA_DIR, "uploads", f"pdfs-{token}.zip")
    buf = await zipfile_upload.read()
    with open(tmp, "wb") as f:
        f.write(buf)
    return JSONResponse({"ok": True, "tmp": os.path.basename(tmp), "name": fn, "size": len(buf)})

@app.get("/admin/api/upload-pdf-apply")
async def api_upload_pdf_apply(request: Request, tmp: str):
    async def gen():
        zip_path = os.path.join(DATA_DIR, "uploads", tmp)
        if not os.path.exists(zip_path):
            yield f"data: {json.dumps({'phase':'error','msg':'临时压缩包不存在'})}\n\n"; return
        pdf_dir = os.path.join(DATA_DIR, "pdfs")
        os.makedirs(pdf_dir, exist_ok=True)
        # 读取 zip
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
                total = len(names)
                done = 0
                yield f"data: {json.dumps({'phase':'read','total':total})}\n\n"
                for n in names:
                    data = zf.read(n)
                    base = os.path.basename(n)
                    tn = os.path.splitext(base)[0]
                    tn = re.sub(r"[^0-9A-Za-z_-]", "", tn)
                    dst = os.path.join(pdf_dir, f"{tn}.pdf")
                    with open(dst, "wb") as f:
                        f.write(data)
                    # 登记/更新
                    with SessionLocal() as db:
                        row = db.get(TrackingFile, tn)
                        if not row:
                            db.add(TrackingFile(tracking_no=tn, file_path=os.path.join("pdfs", f"{tn}.pdf"), uploaded_at=datetime.utcnow()))
                        else:
                            row.file_path = os.path.join("pdfs", f"{tn}.pdf")
                            row.uploaded_at = datetime.utcnow()
                        db.commit()
                    done += 1
                    if done % 20 == 0:
                        yield f"data: {json.dumps({'phase':'progress','done':done,'total':total})}\n\n"
                        await asyncio.sleep(0.001)
                        if await request.is_disconnected(): return
        except Exception as e:
            yield f"data: {json.dumps({'phase':'error','msg':str(e)})}\n\n"; return
        yield f"data: {json.dumps({'phase':'done'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

# ---------------- ZIP 列表 ----------------
@app.get("/admin/zips", response_class=HTMLResponse)
def zips_page(request: Request):
    redir = _ensure_admin(request)
    if redir: return redir
    zdir = os.path.join(DATA_DIR, "zips")
    rows = []
    try:
        for name in sorted(os.listdir(zdir), reverse=True):
            if name.startswith("pdfs-") and name.endswith(".zip"):
                ymd = name[5:13]
                if len(ymd) == 8 and ymd.isdecimal():
                    date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
                    size = os.path.getsize(os.path.join(zdir, name))
                    rows.append({"date": date, "zip_name": name, "size": size})
    except Exception:
        pass
    return templates.TemplateResponse("zips.html", {"request": request, "rows": rows})

# ---------------- 客户端访问码 ----------------
@app.get("/admin/clients", response_class=HTMLResponse)
def clients_page(request: Request, db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    rows = db.query(ClientAuth).order_by(ClientAuth.id.desc()).all()
    return templates.TemplateResponse("clients.html", {"request": request, "rows": rows})

@app.post("/admin/clients/add")
def clients_add(request: Request, code6: str = Form(""), description: str = Form(""), db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    code6 = re.sub(r"\D", "", code6 or "")[:6]
    if not code6:
        return RedirectResponse("/admin/clients", status_code=302)
    db.add(ClientAuth(code_plain=code6, description=description.strip(), is_active=True, created_at=datetime.utcnow()))
    db.commit()
    return RedirectResponse("/admin/clients", status_code=302)

@app.post("/admin/clients/toggle")
def clients_toggle(request: Request, id: int = Form(...), db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    r = db.get(ClientAuth, id)
    if r:
        r.is_active = not r.is_active
        db.commit()
    return RedirectResponse("/admin/clients", status_code=302)

@app.post("/admin/clients/delete")
def clients_delete(request: Request, id: int = Form(...), db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    r = db.get(ClientAuth, id)
    if r:
        db.delete(r); db.commit()
    return RedirectResponse("/admin/clients", status_code=302)

# ---------------- 设置 ----------------
@app.get("/admin/settings", response_class=HTMLResponse)
def settings_page(request: Request, db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "o_days": get_kv(db, "retention_orders_days", "90"),
        "f_days": get_kv(db, "retention_files_days", "90"),
        "server_version": get_kv(db, "server_version", "1.97"),
        "client_recommend": get_kv(db, "client_recommend", "1.97"),
    })

@app.post("/admin/settings")
def settings_save(request: Request,
                  retention_orders_days: str = Form("90"),
                  retention_files_days: str = Form("90"),
                  server_version: str = Form("1.97"),
                  client_recommend: str = Form("1.97"),
                  db=Depends(get_db)):
    redir = _ensure_admin(request)
    if redir: return redir
    set_kv(db, "retention_orders_days", retention_orders_days.strip())
    set_kv(db, "retention_files_days", retention_files_days.strip())
    set_kv(db, "server_version", server_version.strip())
    set_kv(db, "client_recommend", client_recommend.strip())
    return RedirectResponse("/admin/settings", status_code=302)

# ---------------- 客户端兼容 API ----------------
@app.get("/api/v1/version")
def api_version(code: str = Query(...), db=Depends(get_db)):
    return {"version": get_kv(db, "server_version", "1.97")}

@app.get("/api/v1/mapping")
def api_mapping(code: str = Query(...)):
    d = _load_mapping()
    if "version" not in d: d["version"] = "1.0"
    if "mappings" not in d: d["mappings"] = []
    return d

def _file_sha256(fp: str) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

@app.get("/api/v1/pdf-zips/dates")
def api_zip_dates(code: str = Query(...)):
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
    return {"dates": sorted(set(out))}

@app.get("/api/v1/pdf-zips/daily")
def api_zip_daily(date: str = Query(..., regex=r"^\d{4}-\d{2}-\d{2}$"), code: str = Query(...)):
    zdir = os.path.join(DATA_DIR, "zips")
    zname = f"pdfs-{date.replace('-','')}.zip"
    fpath = os.path.join(zdir, zname)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="not found")
    headers = {
        "ETag": _file_sha256(fpath),
        "X-Checksum-SHA256": _file_sha256(fpath),
        "Content-Disposition": f'attachment; filename="{zname}"',
    }
    return FileResponse(fpath, media_type="application/zip", headers=headers)

# ---------------- 健康检查 ----------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "time": datetime.utcnow().isoformat(timespec="seconds")}

# ---------------- 接入 admin_extras（模板/在线升级） ----------------
try:
    from .admin_extras import router as admin_extras_router
    app.include_router(admin_extras_router)
except Exception as e:
    print("WARN: admin_extras not loaded:", e, file=sys.stderr)

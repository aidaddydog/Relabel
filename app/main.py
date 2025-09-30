# app/main.py
import os, zipfile, re, shutil, time, math, json, traceback, hashlib
import subprocess, shlex
from datetime import datetime, timedelta, date
from typing import Optional, Iterable

from fastapi import FastAPI, Request, UploadFile, File, Form, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, PlainTextResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import text, create_engine, Column, String, Integer, Boolean, DateTime, Text, select
from sqlalchemy.orm import sessionmaker, declarative_base
from .print_ext import init_print_ext as _init_print_ext_197

from passlib.hash import bcrypt as _bcrypt_legacy, bcrypt_sha256 as _bcrypt_sha256, argon2 as _argon2


# ---- 登录口令哈希：Argon2id + Pepper（向后兼容 bcrypt/bcrypt_sha256）----
# - 新装/重置：一律 Argon2id( HMAC(pepper, password) )
# - 验证：优先以 Argon2id+pepper 校验；失败再回退到 bcrypt_sha256 / bcrypt
# - 自动再哈希：登录成功后若发现非 Argon2 或参数过旧，立即升级为 Argon2id
import hmac, secrets

def _pepper_bytes() -> bytes:
    """
    读取全局 Pepper（推荐通过系统环境文件注入），若不存在则返回空字节：
      - HUANDAN_PEPPER_FILE=/etc/huandan/secret_pepper
      - HUANDAN_PEPPER=<hex或明文>
    """
    pfile = os.environ.get("HUANDAN_PEPPER_FILE", "").strip()
    if pfile and os.path.exists(pfile):
        try:
            return open(pfile, "rb").read().strip()
        except Exception:
            pass
    val = os.environ.get("HUANDAN_PEPPER", "").strip()
    if not val:
        return b""
    try:
        return bytes.fromhex(val)
    except Exception:
        return val.encode("utf-8")

# Argon2id 参数（passlib）：
# time_cost≈轮次数，memory_cost=KiB，parallelism=并行度
_ARGON2 = _argon2.using(type="ID", time_cost=3, memory_cost=65536, parallelism=2)

def _hmac_sha256(pepr: bytes, pw: str) -> str:
    return hmac.new(pepr, pw.encode("utf-8"), hashlib.sha256).hexdigest()

def _is_argon2_hash(h: str) -> bool:
    return isinstance(h, str) and h.startswith("$argon2")

def _hash_password(pw: str) -> str:
    pepr = _pepper_bytes()
    payload = _hmac_sha256(pepr, pw) if pepr else pw
    return _ARGON2.hash(payload)

def _verify_password(pw: str, hh: str) -> bool:
    # 1) Argon2 + Pepper
    try:
        if _is_argon2_hash(hh):
            pepr = _pepper_bytes()
            payload = _hmac_sha256(pepr, pw) if pepr else pw
            return _ARGON2.verify(payload, hh)
    except Exception:
        pass
    # 2) 回退：bcrypt_sha256 -> bcrypt（历史散列）
    try:
        return _bcrypt_sha256.verify(pw, hh)
    except Exception:
        try:
            return _bcrypt_legacy.verify(pw, hh)
        except Exception:
            return False

def _needs_rehash(hh: str) -> bool:
    try:
        if not _is_argon2_hash(hh):
            return True
        return _ARGON2.needs_update(hh)
    except Exception:
        return True

# 使用 bcrypt_sha256 以避免 72 字节限制，同时兼容历史上用 bcrypt 生成的散列。
def _hash_password(pw: str) -> str:
    return _bcrypt_sha256.hash(pw)

def _verify_password(pw: str, hh: str) -> bool:
    # 先按 bcrypt_sha256 校验，失败后再回退到老的 bcrypt
    try:
        return _bcrypt_sha256.verify(pw, hh)
    except Exception:
        try:
            return _bcrypt_legacy.verify(pw, hh)
        except Exception:
            return False

import pandas as pd

# -------- 基本路径 --------
BASE_DIR = os.environ.get("HUANDAN_BASE", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
DATA_DIR = os.environ.get("HUANDAN_DATA", "/opt/huandan-data")

PDF_DIR = os.path.join(DATA_DIR, "pdfs")
UP_DIR  = os.path.join(DATA_DIR, "uploads")
ZIP_DIR = os.path.join(DATA_DIR, "pdf_zips")  # 每日归档

os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(UP_DIR,  exist_ok=True)
os.makedirs(ZIP_DIR, exist_ok=True)

# 确保静态/更新/运行时目录存在
os.makedirs(os.path.join(BASE_DIR, "app", "static"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "app", "templates"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "updates"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "runtime"), exist_ok=True)

# -------- 应用/挂载 --------
app = FastAPI(title="换单服务端")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY","huandan-secret-key"))

app.mount("/static",  StaticFiles(directory=os.path.join(BASE_DIR, "app", "static")),  name="static")
app.mount("/updates", StaticFiles(directory=os.path.join(BASE_DIR, "updates")),       name="updates")
app.mount("/runtime", StaticFiles(directory=os.path.join(BASE_DIR, "runtime")),       name="runtime")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app", "templates"))
try:
    templates.env.auto_reload = True
except Exception:
    pass

# 尝试挂载额外路由（可选）
try:
    from app.admin_extras import router as admin_extras_router
    app.include_router(admin_extras_router)
except Exception:
    pass

# -------- 数据库 --------
engine = create_engine(
    f"sqlite:///{os.path.join(BASE_DIR,'huandan.sqlite3')}",
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

class MetaKV(Base):
    __tablename__ = "meta"
    key = Column(String(64), primary_key=True)
    value = Column(Text)

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
    locked_until = Column(DateTime, nullable=True)

class OrderMapping(Base):
    __tablename__ = "order_mapping"
    order_id = Column(String(128), primary_key=True)
    tracking_no = Column(String(128), index=True)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TrackingFile(Base):
    __tablename__ = "tracking_file"
    tracking_no = Column(String(128), primary_key=True)
    file_path = Column(Text)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    # --- 1.97 新增聚合列 ---
    print_status = Column(String(16), default="not_printed")      # not_printed | printed | reprinted
    first_print_time = Column(DateTime, nullable=True)
    last_print_time = Column(DateTime, nullable=True)
    print_count = Column(Integer, default=0)
    last_print_client_name = Column(String(128), default="")



# -------- 工具函数 --------
def now_iso(): return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def to_iso(dt: Optional[datetime]) -> str:
    if not dt: return ""
    try: return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception: return ""

def canon_tracking(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("._")
    return s[:128]

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def get_kv(db, key, default=""):
    obj = db.get(MetaKV, key)
    return obj.value if obj and obj.value is not None else default

def set_kv(db, key, value):
    obj = db.get(MetaKV, key)
    if not obj:
        obj = MetaKV(key=key, value=str(value)); db.add(obj)
    else:
        obj.value = str(value)
    db.commit()

def set_mapping_version(db): set_kv(db, "mapping_version", now_iso())
def get_mapping_version(db):
    v = get_kv(db, "mapping_version", "")
    if not v:
        set_mapping_version(db); v = get_kv(db,"mapping_version","")
    return v

def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

def _safe_join_uploads(name: str) -> str:
    """将上传的临时文件名规范化到 UP_DIR 下，拒绝路径穿越"""
    s = (name or "").replace("\\","/").lstrip("/")
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return os.path.join(UP_DIR, s)

def _read_sidecar_sha(fp: str) -> Optional[str]:
    try:
        p = fp + ".sha256"
        if os.path.exists(p):
            return open(p, "r", encoding="utf-8").read().strip()
    except Exception:
        pass
    return None

def _write_sidecar_sha(fp: str, sha: str):
    try:
        with open(fp + ".sha256", "w", encoding="utf-8") as f:
            f.write(sha.strip())
    except Exception:
        pass

# -------- 映射写盘 --------
def _build_mapping_payload(db):
    map_rows = db.query(OrderMapping).all()
    file_rows = db.query(TrackingFile).all()
    tf_by_tn = {f.tracking_no: f for f in file_rows}
    payload, seen = [], set()
    for r in map_rows:
        tn_norm = canon_tracking(r.tracking_no or "")
        tf = tf_by_tn.get(tn_norm) or tf_by_tn.get(r.tracking_no or "")
        u = r.updated_at
        if tf and tf.uploaded_at: u = max([x for x in (u, tf.uploaded_at) if x is not None])
        payload.append({"order_id": r.order_id, "tracking_no": tn_norm, "updated_at": to_iso(u)})
        seen.add(tn_norm)
    for f in file_rows:
        tn_norm = canon_tracking(f.tracking_no or "")
        if tn_norm in seen: continue
        payload.append({"order_id": "", "tracking_no": tn_norm, "updated_at": to_iso(f.uploaded_at)})
    return {"version": get_mapping_version(db), "mappings": payload}

def write_mapping_json(db):
    data = _build_mapping_payload(db)
    fp = os.path.join(DATA_DIR, "mapping.json")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ===== 每日ZIP =====
def _date_str(d: date) -> str:
    try: return d.strftime("%Y-%m-%d")
    except Exception: return str(d)

def _date_str_compact(d: date) -> str:
    try: return d.strftime("%Y%m%d")
    except Exception: return str(d).replace("-","")

def build_daily_pdf_zip(db, target_date: Optional[date]=None) -> str:
    """为 target_date（默认今天）重建仅包含当日上传/更新PDF的 zip；返回zip路径"""
    if target_date is None: target_date = datetime.utcnow().date()
    start_dt = datetime(target_date.year, target_date.month, target_date.day)
    end_dt   = start_dt + timedelta(days=1)
    files = db.query(TrackingFile).filter(
        TrackingFile.uploaded_at >= start_dt,
        TrackingFile.uploaded_at <  end_dt
    ).all()

    zip_name = f"pdfs-{_date_str_compact(target_date)}.zip"
    fp_zip   = os.path.join(ZIP_DIR, zip_name)
    if not files:
        # 无文件：仍返回路径（可能不存在）
        return fp_zip

    tmp_zip = fp_zip + ".tmp"
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for f in files:
                try:
                    if not f.file_path or (not os.path.exists(f.file_path)): continue
                    arcname = f"{canon_tracking(f.tracking_no)}.pdf"
                    z.write(f.file_path, arcname)
                except Exception:
                    pass
        os.makedirs(os.path.dirname(fp_zip), exist_ok=True)
        if os.path.exists(fp_zip):
            try: os.replace(tmp_zip, fp_zip)
            except Exception:
                try: os.remove(fp_zip)
                except Exception: pass
                os.replace(tmp_zip, fp_zip)
        else:
            os.replace(tmp_zip, fp_zip)
        # 写入 SHA256（供响应头使用）
        try:
            h=hashlib.sha256()
            with open(fp_zip, "rb") as f:
                for chunk in iter(lambda: f.read(1024*1024), b""):
                    h.update(chunk)
            _write_sidecar_sha(fp_zip, h.hexdigest())
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(tmp_zip): os.remove(tmp_zip)
        except Exception: pass
    return fp_zip

def list_pdf_zip_dates() -> list:
    """扫描 ZIP_DIR 下所有 pdfs-YYYYMMDD.zip，返回按日期倒序的列表。"""
    out=[]
    if not os.path.isdir(ZIP_DIR): return out
    for name in os.listdir(ZIP_DIR):
        if not name.startswith("pdfs-") or not name.endswith(".zip"): continue
        dpart=name[len("pdfs-"):-len(".zip")]
        if len(dpart)==8 and dpart.isdigit():
            d=f"{dpart[0:4]}-{dpart[4:6]}-{dpart[6:8]}"
        elif len(dpart)==10 and dpart[4]=='-' and dpart[7]=='-':
            d=dpart
        else:
            continue
        fp=os.path.join(ZIP_DIR,name)
        try: size=os.path.getsize(fp)
        except Exception: size=0
        out.append({"date": d, "zip_name": name, "size": size})
    try:
        out.sort(key=lambda x: x.get("date",""), reverse=True)
    except Exception:
        pass
    return out

# -------- 认证、清理 --------
def is_locked(c: ClientAuth) -> bool:
    return bool(c.locked_until and datetime.utcnow() < c.locked_until)

def verify_code(db, code: str):
    if not code or not code.isdigit() or len(code)!=6: return None
    rows = db.execute(select(ClientAuth).where(ClientAuth.is_active==True)).scalars().all()
    for c in rows:
        if is_locked(c): continue
        if (c.code_plain == code) or (c.code_hash and _verify_password(code, c.code_hash)):
            c.last_used = datetime.utcnow(); c.fail_count = 0; c.locked_until=None; db.commit(); return c
    for c in rows:
        c.fail_count = (c.fail_count or 0) + 1
        if c.fail_count >= 5: c.locked_until = datetime.utcnow() + timedelta(minutes=5)
    db.commit(); return None

# ---- 初始化打印扩展（1.97）----
from .print_ext import init_print_ext as _init_print_ext_197
_init_print_ext_197(app, engine, SessionLocal, Base, verify_code)


def cleanup_expired(db):
    o_days = int(get_kv(db, 'retention_orders_days', '0') or '0')
    f_days = int(get_kv(db, 'retention_files_days', '0') or '0')
    if o_days > 0:
        dt = datetime.utcnow() - timedelta(days=o_days)
        db.query(OrderMapping).filter(OrderMapping.updated_at < dt).delete()
    if f_days > 0:
        dt = datetime.utcnow() - timedelta(days=f_days)
        olds = db.query(TrackingFile).filter(TrackingFile.uploaded_at < dt).all()
        for r in olds:
            try:
                if r.file_path and os.path.exists(r.file_path): os.remove(r.file_path)
            except Exception:
                pass
            db.delete(r)
    db.commit()

# -------- 启动钩子：建表 + 默认管理员 --------
def _ensure_default_admin():
    """（已还原手动初始化流程）不再自动创建/重置管理员；仅保留历史兼容位。
    初始化请访问 /admin/bootstrap 创建首个管理员。
    """
    return
@app.on_event("startup")
def _init_db():
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except Exception as e:
        print("DB init warn:", e)
    _ensure_default_admin()

# ------------------ 管理端认证与页面 ------------------
@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request, db=Depends(get_db)):
    # 删除 Web 初始化页：若无管理员，仅在登录页提示
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

def require_admin(request: Request, db):
    """不需要登录：统一放行（仅此处修改）。"""
    return

# 首次初始化管理员（仍保留）
@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, db=Depends(get_db)):
    require_admin(request, db); cleanup_expired(db)
    stats = {
        "order_count": db.query(OrderMapping).count(),
        "file_count": db.query(TrackingFile).count(),
        "client_count": db.query(ClientAuth).count(),
        "version": get_mapping_version(db),
        "server_version": get_kv(db,"server_version","server-20250916b"),
        "client_recommend": get_kv(db,"client_recommend","client-20250916b"),
        "o_days": get_kv(db,"retention_orders_days","30"),
        "f_days": get_kv(db,"retention_files_days","30"),
    }
    return templates.TemplateResponse("dashboard.html", {"request": request, "stats": stats})

# ------------------ 工具：执行命令 ------------------
def run_cmd(cmd: str, cwd: Optional[str] = None, timeout: int = 60):
    p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()

def git_status_info(base: str):
    repo = base
    git_dir = os.path.join(repo, ".git")
    if not os.path.isdir(git_dir):
        return {"mode": "nogit"}
    info = {"mode": "git", "repo": repo}
    # 允许失败但不抛异常
    _, branch, _ = run_cmd("git rev-parse --abbrev-ref HEAD", cwd=repo)
    _, origin, _ = run_cmd("git remote get-url origin", cwd=repo)
    run_cmd("git fetch --all --prune", cwd=repo)
    _, counts, _ = run_cmd(f"git rev-list --left-right --count HEAD...origin/{branch}", cwd=repo)
    ahead = behind = 0
    if counts:
        parts = counts.replace("\t"," ").split()
        if len(parts)>=2:
            ahead, behind = int(parts[0]), int(parts[1])
    _, local_log, _  = run_cmd('git log -1 --date=iso --pretty=format:"%h %cd %s"', cwd=repo)
    _, remote_log, _ = run_cmd(f'git log -1 origin/{branch} --date=iso --pretty=format:"%h %cd %s"', cwd=repo)
    info.update({
        "branch": branch or "",
        "origin": origin or "",
        "ahead": ahead,
        "behind": behind,
        "local": local_log.strip('"'),
        "remote": remote_log.strip('"'),
    })
    return info

# ------------------ 在线升级（仅管理员） ------------------
@app.get("/admin/update", response_class=HTMLResponse)
def update_page(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    info = git_status_info(BASE_DIR)
    oneliner = "bash <(curl -fsSL https://raw.githubusercontent.com/aidaddydog/huandan.server/main/scripts/bootstrap_online.sh)"
    return templates.TemplateResponse("update.html", {"request": request, "info": info, "oneliner": oneliner})

@app.post("/admin/update/git_pull")
def update_git_pull(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    if not os.path.isdir(os.path.join(BASE_DIR, ".git")):
        raise HTTPException(status_code=400, detail="当前目录不是 git 仓库，无法 git pull")
    cmds = [
        "git fetch --all --prune",
        "git checkout $(git rev-parse --abbrev-ref HEAD) || true",
        "git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)",
        "git clean -fd"
    ]
    for c in cmds:
        rc, out, err = run_cmd(c, cwd=BASE_DIR)
        if rc != 0:
            return PlainTextResponse(f"更新失败：{c}\n\n{out}\n{err}", status_code=500)
    rc, out, err = run_cmd(f"bash {shlex.quote(os.path.join(BASE_DIR,'scripts','install_root.sh'))}", cwd=BASE_DIR, timeout=1800)
    if rc != 0:
        return PlainTextResponse(f"install 脚本执行失败：\n{out}\n{err}", status_code=500)
    return RedirectResponse("/admin/update?ok=1", status_code=302)

# ------------------ 模板编辑器（仅管理员） ------------------
TEMPLATE_ROOT = os.path.join(BASE_DIR, "app", "templates")

def _safe_template_rel(path: str) -> str:
    p = (path or "").replace("\\", "/").lstrip("/")
    if ".." in p or not p.endswith(".html"):
        raise HTTPException(status_code=400, detail="非法模板路径")
    return p

def _safe_template_abs(path: str) -> str:
    rel = _safe_template_rel(path)
    abs_path = os.path.abspath(os.path.join(TEMPLATE_ROOT, rel))
    if not abs_path.startswith(os.path.abspath(TEMPLATE_ROOT)+os.sep) and abs_path != os.path.abspath(TEMPLATE_ROOT):
        raise HTTPException(status_code=400, detail="非法模板路径")
    return abs_path

def _list_templates():
    out = []
    for root, _, files in os.walk(TEMPLATE_ROOT):
        for f in files:
            if f.endswith(".html"):
                abs_p = os.path.join(root, f)
                rel_p = os.path.relpath(abs_p, TEMPLATE_ROOT).replace("\\","/")
                out.append(rel_p)
    out.sort()
    return out

@app.get("/admin/templates", response_class=HTMLResponse)
def templates_list(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    files = _list_templates()
    return templates.TemplateResponse("templates_list.html", {"request": request, "files": files})

@app.get("/admin/templates/edit", response_class=HTMLResponse)
def templates_edit(request: Request, path: str, db=Depends(get_db)):
    require_admin(request, db)
    abs_p = _safe_template_abs(path)
    if not os.path.exists(abs_p):
        raise HTTPException(status_code=404, detail="模板不存在")
    content = open(abs_p, "r", encoding="utf-8").read()
    return templates.TemplateResponse("templates_edit.html", {"request": request, "path": path, "content": content})

@app.post("/admin/templates/save")
def templates_save(request: Request, path: str = Form(...), content: str = Form(...), db=Depends(get_db)):
    require_admin(request, db)
    abs_p = _safe_template_abs(path)
    backup_dir = os.path.join(BASE_DIR, "updates", "template-backups", datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.join(backup_dir, os.path.dirname(path)), exist_ok=True)
    if os.path.exists(abs_p):
        shutil.copy2(abs_p, os.path.join(backup_dir, path))
    os.makedirs(os.path.dirname(abs_p), exist_ok=True)
    with open(abs_p, "w", encoding="utf-8") as f:
        f.write(content)
    return RedirectResponse(f"/admin/templates/edit?path={path}&saved=1", status_code=302)

# ------------------ 订单导入（3步） + 进度SSE ------------------
@app.get("/admin/upload-orders", response_class=HTMLResponse)
def upload_orders_page(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    return templates.TemplateResponse("upload_orders.html", {"request": request})

@app.post("/admin/upload-orders-step1", response_class=HTMLResponse)
async def upload_orders_step1(request: Request, file: UploadFile = File(...), db=Depends(get_db)):
    require_admin(request, db)
    tmp = os.path.join(UP_DIR, f"orders-{int(time.time())}-{re.sub(r'[^A-Za-z0-9_.-]+','_',file.filename)}")
    with open(tmp, "wb") as f: f.write(await file.read())
    try:
        if tmp.lower().endswith(".csv"): df = pd.read_csv(tmp, nrows=1)
        else: df = pd.read_excel(tmp, nrows=1)
    except Exception as e:
        return templates.TemplateResponse("upload_orders.html", {"request": request, "err": f"读取失败：{e}"})
    request.session["last_orders_tmp"] = tmp
    return templates.TemplateResponse("choose_columns.html", {"request": request, "columns": list(df.columns)})

@app.post("/admin/upload-orders-step2", response_class=HTMLResponse)
def upload_orders_step2(request: Request, order_col: str = Form(...), tracking_col: str = Form(...), db=Depends(get_db)):
    require_admin(request, db)
    tmp = request.session.get("last_orders_tmp")
    if not tmp or not os.path.exists(tmp): return RedirectResponse("/admin/upload-orders", status_code=302)
    if tmp.lower().endswith(".csv"): df = pd.read_csv(tmp, dtype=str)
    else: df = pd.read_excel(tmp, dtype=str)
    df = df.fillna("")
    prev = df[[order_col, tracking_col]].head(50).values.tolist()
    request.session["orders_cols"] = {"order": order_col, "tracking": tracking_col}
    return templates.TemplateResponse("preview_orders.html", {"request": request, "rows": prev})

# 新增：订单导入 SSE（前端按钮调用）
@app.get("/admin/api/orders-apply")
def orders_apply_sse(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    tmp = request.session.get("last_orders_tmp")
    cols = request.session.get("orders_cols") or {}
    if not tmp or not os.path.exists(tmp) or "order" not in cols or "tracking" not in cols:
        def _err():
            yield _sse({"phase":"error","msg":"未找到待导入数据，请重新上传并选择列"})
        return StreamingResponse(_err(), media_type="text/event-stream", headers={"Cache-Control":"no-cache"})

    def _stream():
        total = 0; count = 0
        try:
            if tmp.lower().endswith(".csv"): df = pd.read_csv(tmp, dtype=str)
            else: df = pd.read_excel(tmp, dtype=str)
            df = df.fillna("")
            total = len(df)
            yield _sse({"phase":"read","total": total})
            now = datetime.utcnow()
            for i, r in df.iterrows():
                oid = str(r[cols["order"]]).strip()
                tn  = canon_tracking(str(r[cols["tracking"]]).strip())
                if oid and tn:
                    m = db.get(OrderMapping, oid)
                    if not m:
                        m = OrderMapping(order_id=oid, tracking_no=tn, updated_at=now); db.add(m)
                    else:
                        m.tracking_no = tn; m.updated_at = now
                    count += 1
                if (i+1) % 200 == 0:
                    db.commit()
                    yield _sse({"phase":"progress","done": i+1, "total": total})
            db.commit()
            set_mapping_version(db); write_mapping_json(db)
            # 清理 session 与临时文件
            try:
                os.remove(tmp)
            except Exception:
                pass
            request.session.pop("last_orders_tmp", None)
            request.session.pop("orders_cols", None)
            yield _sse({"phase":"done","count": count,"redirect":"/admin/orders"})
        except Exception as e:
            db.rollback()
            yield _sse({"phase":"error","msg": f"导入失败：{e}"})
    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache"})

# ------------------ PDF 导入（ZIP） + 进度SSE ------------------
@app.get("/admin/upload-pdf", response_class=HTMLResponse)
def upload_pdf_page(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    return templates.TemplateResponse("upload_pdf.html", {"request": request})

# 第一步：仅接收文件并保存为临时文件（前端能显示上传进度）
@app.post("/admin/api/upload-pdf-file")
async def api_upload_pdf_file(request: Request, zipfile_upload: UploadFile = File(...), db=Depends(get_db)):
    require_admin(request, db)
    tmp_name = f"pdfs-{int(time.time())}-{re.sub(r'[^A-Za-z0-9_.-]+','_',zipfile_upload.filename)}"
    tmp_zip = os.path.join(UP_DIR, tmp_name)
    with open(tmp_zip, "wb") as f:
        f.write(await zipfile_upload.read())
    return {"ok": True, "tmp": tmp_name}

# 第二步：SSE 解压→入库→重建当日ZIP
@app.get("/admin/api/apply-pdf-import")
def api_apply_pdf_import(request: Request, tmp: str = Query(...), db=Depends(get_db)):
    require_admin(request, db)
    tmp_zip = _safe_join_uploads(tmp)
    if not (tmp_zip and os.path.exists(tmp_zip)):
        def _err():
            yield _sse({"phase":"error","msg":"临时ZIP不存在，请重新上传"})
        return StreamingResponse(_err(), media_type="text/event-stream", headers={"Cache-Control":"no-cache"})

    def _stream():
        saved=0; skipped=0
        try:
            with zipfile.ZipFile(tmp_zip, "r") as z:
                members = [m for m in z.namelist() if (m and not m.endswith("/") and m.lower().endswith(".pdf"))]
                total = len(members)
                done = 0
                yield _sse({"phase":"unzip","total": total, "done": done})
                for m in members:
                    try:
                        tracking = canon_tracking(os.path.splitext(os.path.basename(m))[0])
                        if not tracking:
                            skipped += 1
                            continue
                        target = os.path.join(PDF_DIR, f"{tracking}.pdf")
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with z.open(m) as src, open(target,"wb") as dst:
                            shutil.copyfileobj(src, dst)
                        tf = db.get(TrackingFile, tracking)
                        if not tf:
                            tf = TrackingFile(tracking_no=tracking, file_path=target, uploaded_at=datetime.utcnow()); db.add(tf)
                        else:
                            tf.file_path = target; tf.uploaded_at = datetime.utcnow()
                        saved += 1
                    except Exception:
                        skipped += 1
                    done += 1
                    if done % 200 == 0:
                        db.commit()
                        yield _sse({"phase":"unzip","total": total, "done": done})
                db.commit()

            # 重建当日 ZIP
            yield _sse({"phase":"repack","msg":"重建当日归档ZIP…"})
            try:
                fp_zip = build_daily_pdf_zip(db, datetime.utcnow().date())
                if os.path.exists(fp_zip):
                    # 如果 sidecar 还没有 SHA，尽量写一个
                    if not _read_sidecar_sha(fp_zip):
                        try:
                            h=hashlib.sha256()
                            with open(fp_zip, "rb") as f:
                                for chunk in iter(lambda: f.read(1024*1024), b""):
                                    h.update(chunk)
                            _write_sidecar_sha(fp_zip, h.hexdigest())
                        except Exception:
                            pass
            except Exception:
                pass

            set_mapping_version(db); write_mapping_json(db)

            # 删除临时文件
            try: os.remove(tmp_zip)
            except Exception: pass

            yield _sse({"phase":"done","saved": saved, "skipped": skipped, "redirect": "/admin/files"})
        except Exception as e:
            db.rollback()
            yield _sse({"phase":"error","msg": f"处理失败：{e}"})
    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache"})

# ------------------ 文件/订单列表与批量操作 ------------------

@app.get("/admin/files", response_class=HTMLResponse)
def list_files(request: Request,
               q: Optional[str]=None,
               status: Optional[str]=None,
               client: Optional[str]=None,
               bind: Optional[str]=None,  # 'bound' | 'unbound' | None
               page: int=1,
               db=Depends(get_db)):
    """
    文件（PDF）列表：
    - 列：# | PDF（导出名） | 订单号 | 上传时间 | 打印状态 | 打印次数 | 打印客户端名称 | 操作
    - 筛选：q/status/client/bind
    - 绑定判定：优先 order_mapping，其次 print_events 最近一次记录（仅用于展示与筛选，主表字段不写回）
    """
    require_admin(request, db); cleanup_expired(db)
    page_size = 100

    # 基础过滤（不含绑定状态）
    base_q = db.query(TrackingFile)
    if q:
        base_q = base_q.filter(TrackingFile.tracking_no.like(f"%{q}%"))
    if status:
        base_q = base_q.filter(TrackingFile.print_status == status)
    if client:
        base_q = base_q.filter(TrackingFile.last_print_client_name.like(f"%{client}%"))

    # 先拿出候选 tracking_no（用于绑定态判断与分页）
    cands = [r[0] for r in base_q.with_entities(TrackingFile.tracking_no).order_by(TrackingFile.uploaded_at.desc()).all()]

    # 计算“已绑定”集合：order_mapping + print_events（有 order_id）
    bound_set = set()
    if cands:
        rows = db.execute(text("SELECT tracking_no FROM order_mapping WHERE tracking_no IN :tn AND ifnull(tracking_no,'')<>''"),
                          {"tn": tuple(cands)}).fetchall()
        bound_set.update([r[0] for r in rows if r and r[0]])
        rows = db.execute(text("SELECT tracking_no FROM print_events WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' GROUP BY tracking_no"),
                          {"tn": tuple(cands)}).fetchall()
        bound_set.update([r[0] for r in rows if r and r[0]])

    # 应用绑定筛选
    if bind == "bound":
        filtered = [tn for tn in cands if tn in bound_set]
    elif bind == "unbound":
        filtered = [tn for tn in cands if tn not in bound_set]
    else:
        filtered = cands

    total = len(filtered)
    pages = max(1, math.ceil(total / page_size))
    page = max(1, min(pages, int(page or 1)))
    start_idx = (page - 1) * page_size
    page_tns = filtered[start_idx : start_idx + page_size]

    # 取本页行
    rows = []
    if page_tns:
        rows = db.query(TrackingFile).filter(TrackingFile.tracking_no.in_(page_tns)).order_by(TrackingFile.uploaded_at.desc()).all()

    # 计算显示所需的“绑定订单号/中文状态/重印原因”
    extras = {}
    if page_tns:
        # 订单号（优先 order_mapping，缺省再从 print_events 最近一次取）
        mm = {}
        rs = db.execute(text("SELECT order_id, tracking_no FROM order_mapping WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' ORDER BY updated_at DESC"),
                        {"tn": tuple(page_tns)}).fetchall()
        for oid, tn in rs:
            if tn and tn not in mm:
                mm[tn] = oid
        # print_events 补洞
        rs2 = db.execute(text("SELECT tracking_no, order_id FROM print_events WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' ORDER BY created_at DESC"),
                         {"tn": tuple(page_tns)}).fetchall()
        for tn, oid in rs2:
            if tn and tn not in mm and oid:
                mm[tn] = oid

        # 重印原因（最近一次）
        rmap = {}
        rs3 = db.execute(text("SELECT tracking_no, reprint_reason FROM print_events WHERE tracking_no IN :tn AND result='success_reprint' AND ifnull(reprint_reason,'')<>'' ORDER BY created_at DESC"),
                         {"tn": tuple(page_tns)}).fetchall()
        for tn, reason in rs3:
            if tn and tn not in rmap:
                rmap[tn] = reason

        # 中文状态
        cn = {"not_printed":"未打印","printed":"已打印","reprinted":"重复打印"}

        for tn in page_tns:
            extras[tn] = {
                "order_id": mm.get(tn, ""),
                "reprint_reason": rmap.get(tn, ""),
                "status_cn": cn.get(next((r.print_status for r in rows if r.tracking_no==tn), "not_printed"), "未打印")
            }

    return templates.TemplateResponse("files.html", {"request": request, "rows": rows, "q": q, "status": status, "client": client, "bind": bind, "page": page, "pages": pages, "total": total, "page_size": page_size, "extras": extras})

@app.get("/admin/files/export-xlsx")
def export_files_xlsx(request: Request,
                      q: Optional[str]=None,
                      status: Optional[str]=None,
                      client: Optional[str]=None,
                      bind: Optional[str]=None,
                      db=Depends(get_db)):
    """导出当前筛选（忽略分页）：两列 -> 追踪号、订单号"""
    require_admin(request, db)
    # 复用 list_files 的候选 + 绑定判定
    base_q = db.query(TrackingFile)
    if q:
        base_q = base_q.filter(TrackingFile.tracking_no.like(f"%{q}%"))
    if status:
        base_q = base_q.filter(TrackingFile.print_status == status)
    if client:
        base_q = base_q.filter(TrackingFile.last_print_client_name.like(f"%{client}%"))
    cands = [r[0] for r in base_q.with_entities(TrackingFile.tracking_no).order_by(TrackingFile.uploaded_at.desc()).all()]
    bound_set = set()
    if cands:
        rows = db.execute(text("SELECT tracking_no FROM order_mapping WHERE tracking_no IN :tn AND ifnull(tracking_no,'')<>''"),
                          {"tn": tuple(cands)}).fetchall()
        bound_set.update([r[0] for r in rows if r and r[0]])
        rows = db.execute(text("SELECT tracking_no FROM print_events WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' GROUP BY tracking_no"),
                          {"tn": tuple(cands)}).fetchall()
        bound_set.update([r[0] for r in rows if r and r[0]])
    if bind == "bound":
        tns = [tn for tn in cands if tn in bound_set]
    elif bind == "unbound":
        tns = [tn for tn in cands if tn not in bound_set]
    else:
        tns = cands

    # 计算绑定订单号（同 list_files）
    mm = {}
    if tns:
        rs = db.execute(text("SELECT order_id, tracking_no FROM order_mapping WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' ORDER BY updated_at DESC"),
                        {"tn": tuple(tns)}).fetchall()
        for oid, tn in rs:
            if tn and tn not in mm:
                mm[tn] = oid
        rs2 = db.execute(text("SELECT tracking_no, order_id FROM print_events WHERE tracking_no IN :tn AND ifnull(order_id,'')<>'' ORDER BY created_at DESC"),
                         {"tn": tuple(tns)}).fetchall()
        for tn, oid in rs2:
            if tn and tn not in mm and oid:
                mm[tn] = oid

    # 生成 Excel
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "export"
    ws.append(["追踪号","订单号"])
    for tn in tns:
        ws.append([tn, mm.get(tn, "")])

    from io import BytesIO
    bio = BytesIO()
    wb.save(bio); bio.seek(0)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"pdf_list_export_{ts}.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
    return StreamingResponse(bio, headers=headers)

@app.post("/admin/files/batch_delete_all")
def file_batch_delete_all(request: Request, q: str = Form(""), db=Depends(get_db)):
    require_admin(request, db)
    targets = db.query(TrackingFile).filter(TrackingFile.tracking_no.like(f"%{q}%")).all() if q else db.query(TrackingFile).all()
    cnt=0
    for tf in targets:
        try:
            if tf.file_path and os.path.exists(tf.file_path): os.remove(tf.file_path)
        except Exception: pass
        db.delete(tf); cnt+=1
    db.commit()
    if cnt>0: set_mapping_version(db); write_mapping_json(db)
    return RedirectResponse(f"/admin/files?ok={cnt}&q={q}", status_code=302)

@app.get("/admin/file/{tracking_no}")
def admin_file_download(tracking_no: str, request: Request, db=Depends(get_db)):
    require_admin(request, db)
    def _find(tr):
        cand = [tr, canon_tracking(tr)]
        for t in cand:
            fp = os.path.join(PDF_DIR, f"{t}.pdf")
            if os.path.exists(fp): return fp
        tn = f"{tr}.pdf".lower()
        for name in os.listdir(PDF_DIR):
            if name.lower()==tn: return os.path.join(PDF_DIR,name)
        return None
    fp = _find(tracking_no)
    if not fp: raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(fp, media_type="application/pdf", filename=os.path.basename(fp))

@app.get("/admin/orders", response_class=HTMLResponse)
def list_orders(request: Request,
                q: Optional[str]=None,
                bind: Optional[str]=None,   # 'bound' | 'unbound' | None
                page: int=1,
                db=Depends(get_db)):
    require_admin(request, db); cleanup_expired(db)
    page_size=100

    query = db.query(OrderMapping)
    if q:
        query = query.filter(OrderMapping.order_id.like(f"%{q}%"))

    # 绑定筛选（已绑定追踪号 / 未绑定）
    if bind == "bound":
        query = query.filter(text("ifnull(tracking_no,'')<>''"))
    elif bind == "unbound":
        query = query.filter(text("ifnull(tracking_no,'')=''"))

    total = query.count()
    rows = query.order_by(OrderMapping.updated_at.desc()).offset((page-1)*page_size).limit(page_size).all()
    pages = max(1, math.ceil(total/page_size))
    return templates.TemplateResponse("orders.html", {"request": request, "rows": rows, "q": q, "bind": bind, "page": page, "pages": pages, "total": total, "page_size": page_size})

@app.get("/admin/orders/export-xlsx")
def export_orders_xlsx(request: Request,
                       q: Optional[str]=None,
                       bind: Optional[str]=None,
                       db=Depends(get_db)):
    """导出订单列表当前筛选（忽略分页）：两列 -> 追踪号、订单号。
       对未绑定的订单，追踪号留空；若一个订单有多个追踪号，将逐行展开。
    """
    require_admin(request, db)
    query = db.query(OrderMapping)
    if q:
        query = query.filter(OrderMapping.order_id.like(f"%{q}%"))
    if bind == "bound":
        query = query.filter(text("ifnull(tracking_no,'')<>''"))
    elif bind == "unbound":
        query = query.filter(text("ifnull(tracking_no,'')=''"))
    rows = query.order_by(OrderMapping.updated_at.desc()).all()

    # 生成 Excel
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "export"
    ws.append(["追踪号","订单号"])
    for r in rows:
        tn = (r.tracking_no or "").strip()
        oid = (r.order_id or "").strip()
        if tn:
            ws.append([tn, oid])
        else:
            ws.append(["", oid])

    from io import BytesIO
    bio = BytesIO(); wb.save(bio); bio.seek(0)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"orders_export_{ts}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"',
               "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    return StreamingResponse(bio, headers=headers)

@app.post("/admin/orders/batch_delete_all")
def orders_batch_delete_all(request: Request, q: str = Form(""), db=Depends(get_db)):
    require_admin(request, db)
    if q: db.query(OrderMapping).filter(OrderMapping.order_id.like(f"%{q}%")).delete()
    else: db.query(OrderMapping).delete()
    db.commit(); set_mapping_version(db); write_mapping_json(db)
    return RedirectResponse(f"/admin/orders?q={q}", status_code=302)

# ---- 客户端访问码 ----
@app.get("/admin/clients", response_class=HTMLResponse)
def clients_page(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    rows = db.query(ClientAuth).order_by(ClientAuth.created_at.desc()).all()
    return templates.TemplateResponse("clients.html", {"request": request, "rows": rows})

@app.post("/admin/clients/add")
def clients_add(request: Request, code6: str = Form(...), description: str = Form(""), db=Depends(get_db)):
    require_admin(request, db)
    if not code6.isdigit() or len(code6)!=6:
        return RedirectResponse("/admin/clients", status_code=302)
    db.add(ClientAuth(code_plain=code6, description=description, is_active=True)); db.commit()
    return RedirectResponse("/admin/clients", status_code=302)

@app.post("/admin/clients/toggle")
def clients_toggle(request: Request, client_id: int = Form(...), db=Depends(get_db)):
    require_admin(request, db)
    c = db.get(ClientAuth, client_id)
    if c: c.is_active = not c.is_active; db.commit()
    return RedirectResponse("/admin/clients", status_code=302)

@app.post("/admin/clients/delete")
def clients_delete(request: Request, client_id: int = Form(...), db=Depends(get_db)):
    require_admin(request, db)
    c = db.get(ClientAuth, client_id)
    if c: db.delete(c); db.commit()
    return RedirectResponse("/admin/clients", status_code=302)

# ---- 设置 ----
@app.get("/admin/settings", response_class=HTMLResponse)
def settings_page(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "o_days": get_kv(db,'retention_orders_days','30'),
        "f_days": get_kv(db,'retention_files_days','30'),
        "server_version": get_kv(db,"server_version","server-20250916b"),
        "client_recommend": get_kv(db,"client_recommend","client-20250916b")
    })

@app.post("/admin/settings")
def settings_save(request: Request,
                  retention_orders_days: str = Form(...),
                  retention_files_days: str = Form(...),
                  server_version: str = Form(...),
                  client_recommend: str = Form(...),
                  db=Depends(get_db)):
    require_admin(request, db)
    set_kv(db,"retention_orders_days", retention_orders_days or "30")
    set_kv(db,"retention_files_days", retention_files_days or "30")
    set_kv(db,"server_version", server_version or "server-20250916b")
    set_kv(db,"client_recommend", client_recommend or "client-20250916b")
    cleanup_expired(db)
    return RedirectResponse("/admin", status_code=302)

# ---- 对齐：后台列表 ≡ 磁盘 pdfs/ ----
@app.post("/admin/reconcile")
def admin_reconcile(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    import glob
    added=renamed=0
    for fp in glob.glob(os.path.join(PDF_DIR, '*.pdf')):
        base=os.path.splitext(os.path.basename(fp))[0]
        cn=canon_tracking(base)
        dst=os.path.join(PDF_DIR, f"{cn}.pdf")
        if os.path.abspath(fp)!=os.path.abspath(dst):
            if not os.path.exists(dst): shutil.move(fp,dst); renamed+=1
            else: os.remove(fp)
            fp=dst
        rec = db.get(TrackingFile, cn)
        if not rec:
            db.add(TrackingFile(tracking_no=cn, file_path=fp, uploaded_at=datetime.utcnow()))
            added+=1
    db.commit()
    drop=0
    for rec in db.query(TrackingFile).all():
        if not rec.file_path or not os.path.exists(rec.file_path):
            db.delete(rec); drop+=1
    db.commit()
    set_mapping_version(db); write_mapping_json(db)
    return RedirectResponse(f"/admin/files?reconciled=1&added={added}&renamed={renamed}&dropped={drop}", status_code=302)

# ------------------ ZIP 列表（一级菜单页） ------------------
@app.get("/admin/zips", response_class=HTMLResponse)
def admin_zip_list(request: Request, db=Depends(get_db)):
    require_admin(request, db)
    rows = list_pdf_zip_dates()
    # 模板存在则渲染，否则直接给 JSON 以保证可用
    tpl_path = os.path.join(TEMPLATE_ROOT, "zips.html")
    if os.path.exists(tpl_path):
        return templates.TemplateResponse("zips.html", {"request": request, "rows": rows})
    return JSONResponse({"rows": rows})

# ------------------ API（客户端使用） ------------------
@app.get("/api/v1/version")
def api_version(code: str = Query(""), db=Depends(get_db)):
    c = verify_code(db, code)
    if not c: raise HTTPException(status_code=403, detail="invalid code")
    return JSONResponse({
        "version": get_mapping_version(db),
        "list_version": get_mapping_version(db),
        "server_version": get_kv(db,"server_version","server-20250916b"),
        "client_recommend": get_kv(db,"client_recommend","client-20250916b"),
    })

@app.get("/api/v1/mapping")
def api_mapping(code: str = Query(""), db=Depends(get_db)):
    c = verify_code(db, code)
    if not c: raise HTTPException(status_code=403, detail="invalid code")
    return _build_mapping_payload(db)

# 单个PDF下载（大小写不敏感兜底）
@app.get("/api/v1/file/{tracking_no}")
def api_file(tracking_no: str, code: str = Query(""), db=Depends(get_db)):
    c = verify_code(db, code)
    if not c: raise HTTPException(status_code=403, detail="invalid code")
    def _find(tr):
        cand = [tr, canon_tracking(tr)]
        for t in cand:
            fp = os.path.join(PDF_DIR, f"{t}.pdf")
            if os.path.exists(fp): return fp
        tn = f"{tr}.pdf".lower()
        for name in os.listdir(PDF_DIR):
            if name.lower()==tn: return os.path.join(PDF_DIR,name)
        return None
    fp = _find(tracking_no)
    if not fp: raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(fp, media_type="application/pdf", filename=os.path.basename(fp))

# 列表：已有归档日期
@app.get("/api/v1/pdf-zips/dates")
def api_pdf_zip_dates(code: str = Query(""), db=Depends(get_db)):
    c = verify_code(db, code)
    if not c: raise HTTPException(status_code=403, detail="invalid code")
    dates_db=set()
    try:
        rows=db.query(TrackingFile.uploaded_at).all()
        for (u,) in rows:
            if not u: continue
            dstr=u.strftime("%Y-%m-%d")
            dates_db.add(dstr)
    except Exception:
        pass
    lst=list_pdf_zip_dates()
    dates_zip={x.get("date") for x in lst}
    for d in sorted(dates_db):
        if d not in dates_zip:
            lst.append({"date": d, "zip_name": f"pdfs-{d.replace('-','')}.zip", "size": 0})
    try:
        today=datetime.utcnow().date()
        if today.strftime("%Y-%m-%d") in dates_db:
            build_daily_pdf_zip(db, today)
    except Exception:
        pass
    try:
        lst.sort(key=lambda x: x.get("date",""), reverse=True)
    except Exception:
        pass
    return {"dates": lst}

# 下载：某日 ZIP（支持 ETag / If-None-Match；带 X-Checksum-Sha256）
@app.get("/api/v1/pdf-zips/daily")
def api_pdf_zip_daily(request: Request, date: Optional[str] = Query(None), code: str = Query(""), db=Depends(get_db)):
    c = verify_code(db, code)
    if not c: raise HTTPException(status_code=403, detail="invalid code")
    if not date:
        d = datetime.utcnow().date()
    else:
        s=str(date).strip()
        if re.fullmatch(r"\d{8}", s):
            d=datetime(int(s[0:4]), int(s[4:6]), int(s[6:8])).date()
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            d=datetime(int(s[0:4]), int(s[5:7]), int(s[8:10])).date()
        else:
            raise HTTPException(status_code=400, detail="invalid date")
    fp = os.path.join(ZIP_DIR, f"pdfs-{_date_str_compact(d)}.zip")
    if not os.path.exists(fp):
        try:
            fp = build_daily_pdf_zip(db, d)
        except Exception:
            pass
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="zip not found")

    # 生成弱 ETag（mtime + size）
    st = os.stat(fp)
    etag = f'W/"{int(st.st_mtime)}-{st.st_size}"'
    inm = request.headers.get("if-none-match")
    if inm and inm.strip() == etag:
        return PlainTextResponse("", status_code=304, headers={"ETag": etag})

    headers = {"ETag": etag}
    sha = _read_sidecar_sha(fp)
    if sha:
        headers["X-Checksum-Sha256"] = sha
    return FileResponse(fp, media_type="application/zip", filename=os.path.basename(fp), headers=headers)

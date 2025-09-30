
# app/print_ext.py
"""
打印扩展（1.97）
- 新增打印事件表 PrintEvent
- 新增 API：
  * GET  /api/v1/print/check
  * POST /api/v1/print/report
- 追踪号维度的聚合写回 tracking_file（print_status/first_print_time/last_print_time/print_count/last_print_client_name）
- 提供简易“客户端子列表”（按访问码聚合最近出现的 host/MAC/IP），供管理页展示
说明：
  * 为避免与 app.main 循环依赖，本模块仅通过原生 SQL 更新 tracking_file 的新增列；
  * 列不存在时自动 ALTER TABLE 以兼容旧库（SQLite）。
"""
from datetime import datetime
import json
from typing import Optional, List, Dict, Any

from fastapi import Request, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import Column, Integer, String, Text, DateTime, text
from sqlalchemy.orm import Session, declarative_base

# 由 init_print_ext 注入
SessionLocal = None
Base = None
engine = None
verify_code = None

def _utcnow():
    return datetime.utcnow()

# ---------- 新表：打印事件 ----------
class PrintEventBase:
    __tablename__ = "print_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    access_code = Column(String(16), index=True)
    input_kind = Column(String(16))           # 'order' | 'tracking'
    code_value = Column(String(128))          # 扫入的原始值（便于审计）
    order_id = Column(String(128), index=True, nullable=True)
    tracking_no = Column(String(128), index=True)
    result = Column(String(32))               # 'success' | 'fail' | 'success_reprint'
    reprint_reason = Column(Text, nullable=True)
    host = Column(String(128), default="")
    user = Column(String(128), default="")
    client_version = Column(String(64), default="")
    printer_name = Column(String(256), default="")
    mac_list = Column(Text, default="[]")     # JSON 数组
    ip_list = Column(Text, default="[]")      # JSON 数组
    pdf_sha256 = Column(String(64), default="")
    client_ip = Column(String(64), default="")
    created_at = Column(DateTime, default=_utcnow)

def ensure_schema():
    """
    - 创建 print_events 表
    - 为 tracking_file 表补新增列
    """
    # 1) 创建 print_events（若不存在）
    Base.metadata.create_all(bind=engine, checkfirst=True)

    # 2) tracking_file 补列
    with engine.connect() as conn:
        # 查询现有列
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tracking_file)")).fetchall()}
        def addcol(sql):
            try:
                conn.execute(text(sql))
            except Exception:
                pass
        if "print_status" not in cols:
            addcol("ALTER TABLE tracking_file ADD COLUMN print_status TEXT DEFAULT 'not_printed'")
        if "first_print_time" not in cols:
            addcol("ALTER TABLE tracking_file ADD COLUMN first_print_time DATETIME NULL")
        if "last_print_time" not in cols:
            addcol("ALTER TABLE tracking_file ADD COLUMN last_print_time DATETIME NULL")
        if "print_count" not in cols:
            addcol("ALTER TABLE tracking_file ADD COLUMN print_count INTEGER DEFAULT 0")
        if "last_print_client_name" not in cols:
            addcol("ALTER TABLE tracking_file ADD COLUMN last_print_client_name TEXT DEFAULT ''")
        conn.commit()

def _norm(s: Optional[str]) -> str:
    return (s or "").strip()

def _safe_json_loads(s: str):
    try:
        return json.loads(s or "[]")
    except Exception:
        return []

def _update_tracking_aggregate(db: Session, tracking_no: str, host: str, is_success: bool):
    """
    使用原生 SQL 更新 tracking_file 的聚合列。
    - 首次成功打印 -> print_count=1, print_status='printed', first_print_time=now, last_print_time=now, last_print_client_name=host
    - 重复成功打印 -> print_count+=1, print_status='reprinted', last_print_time=now, last_print_client_name=host
    """
    tn = _norm(tracking_no)
    if not tn:
        return
    now = _utcnow()
    # 读当前计数
    row = db.execute(text("SELECT print_count FROM tracking_file WHERE tracking_no=:tn"), {"tn": tn}).fetchone()
    if not row:
        # 未登记追踪号（极少见）：插入一条空路径记录以便聚合（不影响文件存取）
        db.execute(text("INSERT OR IGNORE INTO tracking_file (tracking_no, file_path, uploaded_at, print_status, first_print_time, last_print_time, print_count, last_print_client_name) VALUES (:tn,'',:now,'not_printed',NULL,NULL,0,'')"),
                   {"tn": tn, "now": now})
        cnt = 0
    else:
        cnt = int(row[0] or 0)

    if is_success:
        if cnt <= 0:
            db.execute(text("""UPDATE tracking_file
                               SET print_count=1, print_status='printed',
                                   first_print_time=:now, last_print_time=:now,
                                   last_print_client_name=:host
                               WHERE tracking_no=:tn"""), {"tn": tn, "now": now, "host": host})
        else:
            db.execute(text("""UPDATE tracking_file
                               SET print_count=print_count+1, print_status='reprinted',
                                   last_print_time=:now, last_print_client_name=:host
                               WHERE tracking_no=:tn"""), {"tn": tn, "now": now, "host": host})
    db.commit()

def init_print_ext(app, _engine, _SessionLocal, _Base, _verify_code):
    global SessionLocal, Base, engine, verify_code, PrintEvent
    SessionLocal = _SessionLocal
    Base = _Base
    engine = _engine
    verify_code = _verify_code

    # 动态定义模型并绑定 Base
    class PrintEvent(Base, PrintEventBase):
        pass
    globals()['PrintEvent'] = PrintEvent

    # 确保表/列
    ensure_schema()

    # ---- 依赖注入 ----
    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ---- API：可打印检查 ----
    @app.get("/api/v1/print/check")
    def api_print_check(
        request: Request,
        code: str = Query(""),
        input_kind: str = Query("order"),
        order_id: str = Query(""),
        tracking_no: str = Query(""),
        code_value: str = Query(""),
        db: Session = Depends(get_db)
    ):
        c = verify_code(db, code)
        if not c:
            raise HTTPException(status_code=403, detail="invalid code")

        # 规范入参
        input_kind = (input_kind or "order").lower()
        order_id = _norm(order_id)
        tracking_no = _norm(tracking_no)
        code_value = _norm(code_value)

        # 判重：订单维度与追踪号维度都查
        dup_order = False
        dup_tracking = False
        if order_id:
            dup_order = db.query(PrintEvent).filter(PrintEvent.order_id == order_id, PrintEvent.result.in_(["success","success_reprint"])).first() is not None
        if tracking_no:
            dup_tracking = db.query(PrintEvent).filter(PrintEvent.tracking_no == tracking_no, PrintEvent.result.in_(["success","success_reprint"])).first() is not None

        duplicate_kind = None
        if input_kind == "order" and dup_order:
            duplicate_kind = "order"
        elif input_kind == "tracking" and dup_tracking:
            duplicate_kind = "tracking"
        elif dup_tracking:
            duplicate_kind = "tracking"
        elif dup_order:
            duplicate_kind = "order"

        # 读取累计次数
        total_cnt = 0
        if tracking_no:
            row = db.execute(text("SELECT print_count FROM tracking_file WHERE tracking_no=:tn"), {"tn": tracking_no}).fetchone()
            total_cnt = int((row[0] if row else 0) or 0)

        return JSONResponse({
            "allow": True,
            "status": ("reprinted" if (total_cnt > 0) else "not_printed"),
            "duplicate_kind": duplicate_kind,
            "print_count": total_cnt
        })

    # ---- API：打印上报 ----
    @app.post("/api/v1/print/report")
    async def api_print_report(request: Request, db: Session = Depends(get_db)):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        code = _norm(payload.get("access_code"))
        c = verify_code(db, code)
        if not c:
            raise HTTPException(status_code=403, detail="invalid code")

        tracking_no = _norm(payload.get("tracking_no"))
        order_id = _norm(payload.get("order_id"))
        result = _norm(payload.get("result"))
        host = _norm(payload.get("host"))
        user = _norm(payload.get("user"))
        client_version = _norm(payload.get("client_version"))
        printer_name = _norm(payload.get("printer_name"))
        reprint_reason = payload.get("reprint_reason") or ""
        input_kind = _norm(payload.get("input_kind"))
        code_value = _norm(payload.get("code_value"))
        mac_list = json.dumps(payload.get("mac_list") or [])
        ip_list = json.dumps(payload.get("ip_list") or [])
        pdf_sha256 = _norm(payload.get("pdf_sha256"))
        client_ip = request.client.host if request.client else ""

        ev = PrintEvent(
            access_code=code, input_kind=input_kind, code_value=code_value,
            order_id=order_id, tracking_no=tracking_no,
            result=result, reprint_reason=reprint_reason,
            host=host, user=user, client_version=client_version, printer_name=printer_name,
            mac_list=mac_list, ip_list=ip_list, pdf_sha256=pdf_sha256, client_ip=client_ip
        )
        db.add(ev); db.commit()

        # 聚合写回 tracking_file
        _update_tracking_aggregate(db, tracking_no, host, is_success=(result in ["success","success_reprint"]))

        # 返回累积信息
        row = db.execute(text("SELECT print_status, print_count, last_print_time, last_print_client_name FROM tracking_file WHERE tracking_no=:tn"), {"tn": tracking_no}).fetchone()
        resp = {
            "ok": True,
            "print_status": row[0] if row else "not_printed",
            "print_count": int((row[1] if row else 0) or 0),
            "last_print_time": (row[2].isoformat(sep=' ', timespec='seconds') if row and row[2] else ""),
            "last_print_client_name": (row[3] if row else "")
        }
        return JSONResponse(resp)

    # ---- Admin：查询某访问码下的客户端子列表（给管理页调用）----
    @app.get("/api/v1/clients/by-code")
    def api_clients_by_code(access_code: str = Query(""), db: Session = Depends(get_db)):
        rows = db.query(PrintEvent).filter(PrintEvent.access_code == access_code).order_by(PrintEvent.created_at.desc()).all()
        # 聚合去重：host + mac_list 作为设备指纹
        out = []
        seen = set()
        for r in rows:
            try:
                macs = _safe_json_loads(r.mac_list)
                ips = _safe_json_loads(r.ip_list)
            except Exception:
                macs, ips = [], []
            key = (r.host or "", json.dumps(macs, ensure_ascii=False))
            if key in seen: 
                continue
            seen.add(key)
            out.append({
                "host": r.host or "",
                "mac_list": macs,
                "ip_list": ips,
                "last_seen": r.created_at.isoformat(sep=' ', timespec='seconds'),
                "client_version": r.client_version or ""
            })
        return {"devices": out}

path: apps/server/app/routes_client_api.py
import os, json, datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from .core.database import SessionLocal
from .core.config import settings
from .models import TrackingFile, OrderMapping, PrintEvent, ClientAuth, MetaKV
from .schemas import PrintCheckResponse, ClientInfo
from .utils import list_zip_dates, sha256_file
from .security import verify_password  # 新增：严格校验 6 位访问码哈希

router = APIRouter(prefix="/api/v1", tags=["client-api"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _verify_code(db: Session, code: str) -> ClientAuth:
    """严格校验 6 位访问码，与数据库中 Argon2+Pepper 的 code_hash 比对；匹配后更新 last_used。"""
    if not code or len(code) != 6:
        raise HTTPException(status_code=401, detail="invalid code")
    rows = db.query(ClientAuth).filter(ClientAuth.is_active == True).all()
    for row in rows:
        if verify_password(code, row.code_hash):
            row.last_used = datetime.datetime.utcnow()
            db.commit()
            return row
    raise HTTPException(status_code=401, detail="invalid code")

@router.get("/version")
def version(db: Session = Depends(get_db), code: str = Query(...)):
    _verify_code(db, code)
    row = db.query(MetaKV).filter(MetaKV.k == "version").first()
    return {"version": row.v if row and row.v else "1.97"}

@router.get("/mapping")
def mapping(db: Session = Depends(get_db), code: str = Query(...)):
    _verify_code(db, code)
    items = db.query(OrderMapping).all()
    return [{"order_id": o.order_id, "tracking_no": o.tracking_no, "updated_at": o.updated_at.isoformat()} for o in items]

@router.get("/pdf-zips/dates")
def zip_dates(db: Session = Depends(get_db), code: str = Query(...)):
    _verify_code(db, code)
    zips_dir = os.path.join(settings.RELABEL_DATA, "zips")
    return list_zip_dates(zips_dir)

@router.get("/pdf-zips/daily")
def zip_daily(date: str, db: Session = Depends(get_db), code: str = Query(...)):
    _verify_code(db, code)
    ymd = date.replace("-", "")
    path = os.path.join(settings.RELABEL_DATA, "zips", f"pdfs-{ymd}.zip")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="not found")
    sha = sha256_file(path)
    headers = {"ETag": sha[:32], "X-Checksum-SHA256": sha}
    return FileResponse(path, media_type="application/zip", filename=os.path.basename(path), headers=headers)

@router.get("/runtime/sumatra")
def runtime(arch: str = "win64", db: Session = Depends(get_db), code: str = Query(...)):
    _verify_code(db, code)
    name = "SumatraPDF-64.exe" if arch == "win64" else "SumatraPDF-32.exe"
    path = os.path.join(settings.RELABEL_BASE, "runtime", name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="runtime not found")
    return FileResponse(path, media_type="application/octet-stream", filename=name)

@router.get("/file/{tracking_no}")
def get_file(tracking_no: str, db: Session = Depends(get_db), code: str = Query(...)):
    _verify_code(db, code)
    path = os.path.join(settings.RELABEL_DATA, "pdfs", f"{tracking_no}.pdf")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, media_type="application/pdf", filename=f"{tracking_no}.pdf")

@router.get("/print/check", response_model=PrintCheckResponse)
def print_check(
    db: Session = Depends(get_db),
    code: str = Query(...),
    input_kind: str = Query("order"),
    order_id: Optional[str] = None,
    tracking_no: Optional[str] = None,
    code_value: Optional[str] = None,
):
    _verify_code(db, code)
    # resolve tracking/order
    resolved_tracking = tracking_no
    resolved_order = order_id
    if input_kind == "order" and order_id and not tracking_no:
        row = db.query(OrderMapping).filter(OrderMapping.order_id == order_id).first()
        if row:
            resolved_tracking = row.tracking_no
    status = "not_printed"
    count = 0
    if resolved_tracking:
        tf = db.query(TrackingFile).filter(TrackingFile.tracking_no == resolved_tracking).first()
        if tf:
            status = tf.print_status or "not_printed"
            count = tf.print_count or 0
    return {"allow": True, "status": status, "duplicate_kind": None, "print_count": count,
            "tracking_no": resolved_tracking, "order_id": resolved_order}

@router.post("/print/report")
def print_report(payload: dict, db: Session = Depends(get_db)):
    code = payload.get("access_code") or payload.get("code")
    _verify_code(db, code or "")
    tracking_no = payload.get("tracking_no")
    if not tracking_no:
        raise HTTPException(status_code=400, detail="tracking_no required")
    # write event
    evt = PrintEvent(
        access_code=code,
        order_id=payload.get("order_id"),
        tracking_no=tracking_no,
        result=payload.get("result","success"),
        host=payload.get("host"),
        user=payload.get("user"),
        client_version=payload.get("client_version"),
        printer_name=payload.get("printer_name"),
        mac_list=json.dumps(payload.get("mac_list") or []),
        ip_list=json.dumps(payload.get("ip_list") or []),
        pdf_sha256=payload.get("pdf_sha256")
    )
    db.add(evt)
    # update aggregate
    tf = db.query(TrackingFile).filter(TrackingFile.tracking_no == tracking_no).first()
    now = datetime.datetime.utcnow()
    if not tf:
        tf = TrackingFile(tracking_no=tracking_no, file_path=f"{tracking_no}.pdf", uploaded_at=now)
        db.add(tf)
    tf.print_count = (tf.print_count or 0) + 1
    tf.print_status = "printed" if tf.print_count == 1 else "reprinted"
    if tf.first_print_time is None:
        tf.first_print_time = now
    tf.last_print_time = now
    tf.last_print_client_name = payload.get("host") or "client"
    db.commit()
    return {
        "print_status": tf.print_status,
        "print_count": tf.print_count,
        "first_print_time": tf.first_print_time.isoformat() if tf.first_print_time else None,
        "last_print_time": tf.last_print_time.isoformat() if tf.last_print_time else None,
        "last_print_client_name": tf.last_print_client_name
    }

@router.get("/clients/by-code", response_model=ClientInfo)
def clients_by_code(db: Session = Depends(get_db), access_code: str = Query(None), code: str = Query(None)):
    matched = _verify_code(db, access_code or code or "")
    # Aggregate from last events
    devices = {}
    for evt in db.query(PrintEvent).order_by(PrintEvent.created_at.desc()).all():
        key = evt.host or "unknown"
        if key not in devices:
            devices[key] = {
                "host": evt.host,
                "mac_list": json.loads(evt.mac_list or "[]"),
                "ip_list": json.loads(evt.ip_list or "[]"),
                "last_seen": evt.created_at.isoformat(),
                "client_version": evt.client_version
            }
    return {
        "description": matched.description or "",
        "is_active": bool(matched.is_active),
        "devices": list(devices.values())
    }

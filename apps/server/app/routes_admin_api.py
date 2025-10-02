
import os, io, json, time, zipfile, shutil
from datetime import datetime
from typing import Optional
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Response
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, or_
from .core.database import SessionLocal
from .core.config import settings
from .models import TrackingFile, OrderMapping, PrintEvent, ClientAuth, MetaKV
from .utils import ensure_dirs, list_zip_dates, sha256_file, sse_event

router = APIRouter(prefix="/admin/api", tags=["admin-api"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def require_login():
    # NOTE: use dependency in main to enforce session for /admin/api if needed
    pass

# ---------- Files (PDF) listing & export ----------
@router.get("/files")
def list_files(
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    status: Optional[str] = Query(None, pattern="^(not_printed|printed|reprinted)$"),
    client: Optional[str] = None,
    bind: Optional[str] = Query(None, pattern="^(bound|unbound)$"),
    page: int = 1,
    page_size: int = 100,
):
    stmt = select(TrackingFile)
    if q:
        stmt = stmt.where(TrackingFile.tracking_no.ilike(f"%{q}%"))
    if status:
        stmt = stmt.where(TrackingFile.print_status == status)
    if client:
        stmt = stmt.where(TrackingFile.last_print_client_name.ilike(f"%{client}%"))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    items = db.execute(stmt.order_by(TrackingFile.uploaded_at.desc())
                       .offset((page-1)*page_size).limit(page_size)).scalars().all()

    # bound inference
    bound_set = set()
    for row in db.execute(select(OrderMapping.tracking_no)).all():
        bound_set.add(row[0])
    for row in db.execute(select(PrintEvent.tracking_no)).all():
        bound_set.add(row[0])

    data = []
    for f in items:
        is_bound = f.tracking_no in bound_set
        if bind == "bound" and not is_bound:
            continue
        if bind == "unbound" and is_bound:
            continue
        data.append({
            "tracking_no": f.tracking_no,
            "uploaded_at": f.uploaded_at.isoformat(),
            "print_status": f.print_status,
            "print_count": f.print_count,
            "first_print_time": f.first_print_time.isoformat() if f.first_print_time else None,
            "last_print_time": f.last_print_time.isoformat() if f.last_print_time else None,
            "last_print_client_name": f.last_print_client_name,
        })
    return {"total": total, "items": data}

@router.get("/files/export-xlsx")
def export_files_xlsx(db: Session = Depends(get_db)):
    items = db.execute(select(TrackingFile)).scalars().all()
    rows = [{
        "tracking_no": f.tracking_no,
        "uploaded_at": f.uploaded_at,
        "print_status": f.print_status,
        "print_count": f.print_count,
        "first_print_time": f.first_print_time,
        "last_print_time": f.last_print_time,
        "last_print_client_name": f.last_print_client_name,
    } for f in items]
    df = pd.DataFrame(rows)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    bio.seek(0)
    return Response(bio.read(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=files.xlsx"})

# ---------- Orders listing & export ----------
@router.get("/orders")
def list_orders(db: Session = Depends(get_db), q: Optional[str] = None, bind: Optional[str] = None, page:int=1, page_size:int=100):
    stmt = select(OrderMapping)
    if q:
        stmt = stmt.where(OrderMapping.order_id.ilike(f"%{q}%"))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    items = db.execute(stmt.order_by(OrderMapping.updated_at.desc())
                       .offset((page-1)*page_size).limit(page_size)).scalars().all()
    data = [{"order_id": o.order_id, "tracking_no": o.tracking_no, "updated_at": o.updated_at.isoformat()} for o in items]
    return {"total": total, "items": data}

@router.get("/orders/export-xlsx")
def export_orders_xlsx(db: Session = Depends(get_db)):
    items = db.execute(select(OrderMapping)).scalars().all()
    rows = [{"order_id": o.order_id, "tracking_no": o.tracking_no, "updated_at": o.updated_at} for o in items]
    df = pd.DataFrame(rows)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    bio.seek(0)
    return Response(bio.read(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=orders.xlsx"})

@router.post("/orders/batch_delete_all")
def batch_delete_all(db: Session = Depends(get_db)):
    from sqlalchemy import delete
    from .core.config import settings
    if not settings.RELABEL_ENABLE_DANGEROUS:
        raise HTTPException(status_code=403, detail="Dangerous operations are disabled")
    db.execute(delete(OrderMapping))
    db.commit()
    return {"ok": True}

# ---------- Upload PDF ZIP ----------
@router.post("/upload-pdf-file")
async def upload_pdf_file(file: UploadFile = File(...)):
    tmp_dir = os.path.join(settings.RELABEL_DATA, "tmp")
    ensure_dirs(tmp_dir)
    token = f"{int(time.time())}"
    tmp_path = os.path.join(tmp_dir, f"pdfs-{token}.zip")
    with open(tmp_path, "wb") as f:
        while True:
            chunk = await file.read(1024*1024)
            if not chunk:
                break
            f.write(chunk)
    return {"ok": True, "token": token}

@router.get("/apply-pdf-import")
def apply_pdf_import(token: str):
    tmp_dir = os.path.join(settings.RELABEL_DATA, "tmp")
    zips_dir = os.path.join(settings.RELABEL_DATA, "zips")
    pdfs_dir = os.path.join(settings.RELABEL_DATA, "pdfs")
    ensure_dirs(tmp_dir, zips_dir, pdfs_dir)
    zip_path = os.path.join(tmp_dir, f"pdfs-{token}.zip")
    if not os.path.isfile(zip_path):
        raise HTTPException(status_code=404, detail="Token not found")
    def _iter():
        # unzip
        saved = 0
        skipped = 0
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            total = len(names)
            yield sse_event({"phase":"unzip","done":0,"total":total})
            for i, name in enumerate(names, 1):
                if name.endswith("/"):
                    continue
                if not name.lower().endswith(".pdf"):
                    skipped += 1
                    yield sse_event({"phase":"unzip","done":i,"total":total})
                    continue
                data = zf.read(name)
                base = os.path.basename(name)
                tracking_no = os.path.splitext(base)[0]
                out_path = os.path.join(pdfs_dir, f"{tracking_no}.pdf")
                with open(out_path, "wb") as f:
                    f.write(data)
                saved += 1
                yield sse_event({"phase":"unzip","done":i,"total":total})
        # archive move
        today = datetime.utcnow().strftime("%Y%m%d")
        archive_path = os.path.join(zips_dir, f"pdfs-{today}.zip")
        shutil.move(zip_path, archive_path)
        yield sse_event({"phase":"repack","msg":"archived"})
        yield sse_event({"phase":"done","saved":saved,"skipped":skipped,"redirect":"/admin/files"})
    return StreamingResponse(_iter(), media_type="text/event-stream")

# ---------- Upload Orders (3-step) ----------
@router.post("/upload-orders-step1")
async def upload_orders_step1(file: UploadFile = File(...)):
    import pandas as pd
    tmp_dir = os.path.join(settings.RELABEL_DATA, "tmp")
    ensure_dirs(tmp_dir)
    tmp_path = os.path.join(tmp_dir, f"orders-{int(time.time())}.xlsx")
    with open(tmp_path, "wb") as f:
        while True:
            chunk = await file.read(1024*1024)
            if not chunk: break
            f.write(chunk)
    # read columns
    df = pd.read_excel(tmp_path, nrows=1) if tmp_path.lower().endswith((".xls",".xlsx")) else pd.read_csv(tmp_path, nrows=1)
    cols = list(df.columns)
    return {"ok": True, "tmp_path": tmp_path, "columns": cols}

@router.post("/upload-orders-step2")
async def upload_orders_step2(tmp_path: str, order_col: str, tracking_col: str):
    # verify existence
    if not os.path.isfile(tmp_path):
        raise HTTPException(status_code=404, detail="temp file not found")
    return {"ok": True, "tmp_path": tmp_path, "order_col": order_col, "tracking_col": tracking_col}

@router.get("/orders-apply")
def orders_apply(tmp_path: str, order_col: str, tracking_col: str, db: Session = Depends(get_db)):
    import pandas as pd
    def _iter():
        try:
            if tmp_path.lower().endswith((".xls",".xlsx")):
                df = pd.read_excel(tmp_path)
            else:
                df = pd.read_csv(tmp_path)
            total = len(df)
            yield sse_event({"phase":"read","total": int(total)})
            done = 0
            for i, row in df.iterrows():
                order_id = str(row[order_col]).strip() if order_col in row else None
                tracking_no = str(row[tracking_col]).strip() if tracking_col in row else None
                if order_id and tracking_no:
                    # upsert
                    existing = db.query(OrderMapping).filter(OrderMapping.order_id == order_id).first()
                    if existing:
                        existing.tracking_no = tracking_no
                        existing.updated_at = datetime.utcnow()
                    else:
                        db.add(OrderMapping(order_id=order_id, tracking_no=tracking_no))
                    db.commit()
                done += 1
                if done % 10 == 0 or done == total:
                    yield sse_event({"phase":"progress","done": int(done),"total": int(total)})
            yield sse_event({"phase":"done","count": int(done),"redirect":"/admin/orders"})
        except Exception as e:
            yield sse_event({"phase":"error","msg": str(e)})
    return StreamingResponse(_iter(), media_type="text/event-stream")

# ---------- Zips & Settings ----------
@router.get("/zips")
def list_zips():
    zips_dir = os.path.join(settings.RELABEL_DATA, "zips")
    items = []
    if os.path.isdir(zips_dir):
        for name in sorted(os.listdir(zips_dir), reverse=True):
            if name.endswith(".zip"):
                p = os.path.join(zips_dir, name)
                items.append({"file": name, "size": os.path.getsize(p)})
    return {"items": items}

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    rows = db.query(MetaKV).all()
    return {"items": {r.k: r.v for r in rows}}

@router.post("/settings")
def set_settings(items: dict, db: Session = Depends(get_db)):
    for k, v in items.items():
        row = db.query(MetaKV).filter(MetaKV.k == k).first()
        if row: row.v = str(v)
        else: db.add(MetaKV(k=k, v=str(v)))
    db.commit()
    return {"ok": True}

# ---------- Admin single file download ----------
@router.get("/admin-file/{tracking_no}")
def admin_file(tracking_no: str):
    pdfs_dir = os.path.join(settings.RELABEL_DATA, "pdfs")
    path = os.path.join(pdfs_dir, f"{tracking_no}.pdf")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="application/pdf", filename=f"{tracking_no}.pdf")


from fastapi import APIRouter, Request, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session
from .core.database import SessionLocal
from .models import AdminUser
from .security import verify_password
from .schemas import LoginRequest

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/admin/login")
async def post_login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(AdminUser).filter(AdminUser.username == req.username).first()
    if not user or not user.is_active or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    request.session["admin_user"] = req.username
    return {"ok": True}

@router.get("/admin/logout")
async def get_logout(request: Request):
    request.session.clear()
    return {"ok": True}

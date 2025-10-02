
import os, secrets
from app.core.database import SessionLocal, engine, Base
from app.models import AdminUser, ClientAuth, MetaKV
from app.security import hash_password

def seed():
    db = SessionLocal()
    try:
        # admin
        if not db.query(AdminUser).filter_by(username="admin").first():
            pw = os.getenv("RELABEL_ADMIN_PASSWORD", "admin123")
            db.add(AdminUser(username="admin", password_hash=hash_password(pw)))
            print(f"Seeded admin user 'admin' with password: {pw}")
        # client code
        if not db.query(ClientAuth).first():
            code = os.getenv("RELABEL_CLIENT_CODE", "123456")
            db.add(ClientAuth(description="default client code", code_hash=hash_password(code), code_plain=code))
            print(f"Seeded client code: {code}")
        # default version
        if not db.query(MetaKV).filter_by(k="version").first():
            db.add(MetaKV(k="version", v="1.97", remark="server version"))
            print("Seeded default version=1.97")
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    seed()

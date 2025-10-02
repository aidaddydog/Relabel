#!/usr/bin/env python3
import os, sys, pathlib

# 确保可以 import app.*
BASE = pathlib.Path(__file__).resolve().parent.parent / "apps" / "server"
sys.path.insert(0, str(BASE))

from app.core.database import SessionLocal
from app.models import AdminUser, ClientAuth, MetaKV
from app.security import hash_password

def seed():
    db = SessionLocal()
    try:
        admin_user = os.getenv("RELABEL_ADMIN_USER", "admin")
        admin_pass = os.getenv("RELABEL_ADMIN_PASSWORD", "admin123")
        client_code = os.getenv("RELABEL_CLIENT_CODE", "123456")

        # 管理员（仅在不存在时创建）
        if not db.query(AdminUser).filter_by(username=admin_user).first():
            db.add(AdminUser(username=admin_user, password_hash=hash_password(admin_pass)))
            print(f"Seeded admin user '{admin_user}' with password: {admin_pass}")

        # 客户端访问码（仅在空表时创建一条默认）
        if not db.query(ClientAuth).first():
            db.add(ClientAuth(description="default client code", code_hash=hash_password(client_code), code_plain=client_code))
            print(f"Seeded client code: {client_code}")

        # 默认版本
        if not db.query(MetaKV).filter_by(k="version").first():
            db.add(MetaKV(k="version", v="1.97", remark="server version"))
            print("Seeded default version=1.97")

        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    seed()

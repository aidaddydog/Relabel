# app/admin_cli.py
"""
管理员 CLI：
- python -m app.admin_cli has-admin               # 存在管理员则退出码 0，反之 1
- python -m app.admin_cli init-admin -u admin     # 交互输入密码（或 -p 指定）
- python -m app.admin_cli reset-admin -u admin    # 重置指定管理员密码
说明：依赖 app.main 中的 SQLAlchemy 初始化，避免重复配置。
"""
import getpass, sys
from typing import Optional
from sqlalchemy import select
from .main import SessionLocal, AdminUser, _hash_password

def _has_admin() -> bool:
    db = SessionLocal()
    try:
        return db.query(AdminUser).count() > 0
    finally:
        db.close()

def _init_admin(username: str, password: Optional[str]):
    db = SessionLocal()
    try:
        if not username:
            print("用户名不能为空", file=sys.stderr); sys.exit(2)
        if password is None:
            pw1 = getpass.getpass("设置管理员密码：")
            pw2 = getpass.getpass("再次输入以确认：")
            if pw1 != pw2:
                print("两次输入不一致", file=sys.stderr); sys.exit(2)
            password = pw1
        # 若存在则更新；不存在则创建
        u = db.execute(select(AdminUser).where(AdminUser.username==username)).scalar_one_or_none()
        if u is None:
            u = AdminUser(username=username, password_hash=_hash_password(password), is_active=True)
        else:
            u.password_hash = _hash_password(password); u.is_active = True
        db.add(u); db.commit()
        print("OK")
    except Exception as e:
        db.rollback(); print(f"失败：{e}", file=sys.stderr); sys.exit(1)
    finally:
        db.close()

def _reset_admin(username: str, password: Optional[str]):
    _init_admin(username, password)

def main():
    if len(sys.argv)<2:
        print(__doc__); sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "has-admin":
        sys.exit(0 if _has_admin() else 1)
    elif cmd == "init-admin":
        u = None; p = None
        # 解析 -u / -p
        args = sys.argv[2:]
        for i,a in enumerate(args):
            if a in ("-u","--username"):
                u = args[i+1]
            if a in ("-p","--password"):
                p = args[i+1]
        _init_admin(u or "admin", p)
    elif cmd == "reset-admin":
        u = None; p = None
        args = sys.argv[2:]
        for i,a in enumerate(args):
            if a in ("-u","--username"):
                u = args[i+1]
            if a in ("-p","--password"):
                p = args[i+1]
        _reset_admin(u or "admin", p)
    else:
        print("未知命令"); sys.exit(2)

if __name__ == "__main__":
    main()

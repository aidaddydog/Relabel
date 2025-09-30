import os, sys, getpass
import uvicorn

def _init_admin_cli():
    """
    初始化/更新管理员账号密码（仅修改 run.py；对现有服务无其他改动）
    用法：
      python run.py init-admin --username <用户名> [--password <密码>]
    若未提供参数，将进入交互式输入。
    """
    # 延迟导入，避免非此命令时做多余初始化
    from app.main import SessionLocal, Base, engine, AdminUser
    from passlib.hash import bcrypt

    # 确保表存在
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except Exception as e:
        print("数据库初始化失败：", e)
        sys.exit(2)

    # 读取参数或交互式输入
    username = None
    password = None
    args = sys.argv[2:]
    for i, tok in enumerate(args):
        if tok in ("--username", "-u") and i + 1 < len(args):
            username = args[i+1]
        if tok in ("--password", "-p") and i + 1 < len(args):
            password = args[i+1]

    if not username:
        username = input("请输入管理员用户名: ").strip()
    if not password:
        # 使用 getpass 避免在终端回显
        pw1 = getpass.getpass("请输入管理员密码: ").strip()
        pw2 = getpass.getpass("请再次输入以确认: ").strip()
        if not pw1 or pw1 != pw2:
            print("两次输入不一致或为空，已取消。")
            sys.exit(1)
        password = pw1

    if not username:
        print("用户名不能为空。")
        sys.exit(1)

    db = SessionLocal()
    try:
        # 查询是否已存在该用户
        from sqlalchemy import select
        u = db.execute(select(AdminUser).where(AdminUser.username == username)).scalar_one_or_none()
        if u:
            u.password_hash = bcrypt.hash(password)
            u.is_active = True
            db.commit()
            print(f"已更新管理员：{username}")
        else:
            db.add(AdminUser(username=username, password_hash=bcrypt.hash(password), is_active=True))
            db.commit()
            print(f"已创建管理员：{username}")
        print("完成。")
    except Exception as e:
        db.rollback()
        print("操作失败：", e)
        sys.exit(2)
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("init-admin", "init_admin"):
        _init_admin_cli()
        sys.exit(0)

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False, workers=1)

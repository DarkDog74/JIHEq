"""Run with: python -m platform_app.manage_users create USERNAME --admin"""
from __future__ import annotations

import argparse
import getpass
import sqlite3

from .auth import hash_password
from .database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="维护稽核平台账号，密码通过交互输入")
    parser.add_argument("action", choices=["create", "reset-password", "disable", "enable", "list"])
    parser.add_argument("username", nargs="?")
    parser.add_argument("--admin", action="store_true", help="创建管理员")
    args = parser.parse_args()
    db = Database()
    try:
        if args.action == "list":
            with db.connect() as connection:
                for row in connection.execute("SELECT username, role, enabled FROM users ORDER BY username"):
                    print(f"{row['username']}\t{row['role']}\t{'启用' if row['enabled'] else '禁用'}")
            return
        if not args.username:
            parser.error("需要账号名称")
        if args.action in {"create", "reset-password"}:
            password = getpass.getpass("密码（至少12个字符）：")
            if password != getpass.getpass("再次输入密码："):
                raise ValueError("两次密码不一致")
            encoded = hash_password(password)
            if args.action == "create":
                db.create_user(args.username, encoded, "admin" if args.admin else "user")
            else:
                db.set_user(args.username, password_hash=encoded)
        else:
            db.set_user(args.username, enabled=args.action == "enable")
        print("账号操作已完成")
    except (ValueError, sqlite3.IntegrityError) as error:
        parser.exit(1, f"操作失败：{error}\n")


if __name__ == "__main__":
    main()

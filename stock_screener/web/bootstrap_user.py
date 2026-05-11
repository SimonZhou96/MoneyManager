#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass

from db import MarketDatabase
from .config import load_dotenv, mysql_config_from_env


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Create or update a fixed Web login user.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--role", default="admin")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if not password:
        raise SystemExit("password is required")

    db = MarketDatabase(mysql_config_from_env())
    try:
        db.init_web_schema()
        db.upsert_web_user(args.username, password, role=args.role, is_active=True)
    finally:
        db.close()
    print(f"Web user upserted: {args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

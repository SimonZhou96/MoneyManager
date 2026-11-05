#!/usr/bin/env python3
"""Back up and migrate the legacy stocks table into stock_pools.

Run with API/worker writers stopped. Schema initialization never drops tables;
deletion is explicit and happens only after every legacy field is verified.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import MarketDatabase, STOCK_CATALOG_FIELDS
from web.config import load_dotenv, mysql_config_from_env


def migrate(db: MarketDatabase, backup: Path, *, drop_legacy: bool = False) -> int:
    with db.conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'stocks'")
        if not cursor.fetchone():
            print("No legacy stocks table; nothing to migrate.")
            return 0
        cursor.execute("SHOW CREATE TABLE stocks")
        ddl = cursor.fetchone()[1]
        cursor.execute("SELECT * FROM stocks ORDER BY market, code")
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()

    backup.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects an existing backup from accidental overwrite.
    with backup.open("x", encoding="utf-8") as output:
        backup.chmod(0o600)
        output.write("-- Restore into a database where stocks does not exist.\n")
        output.write("SET NAMES utf8mb4;\n" + ddl + ";\n")
        names = ", ".join(f"`{column}`" for column in columns)
        for row in rows:
            values = ", ".join(db.conn.escape(value) for value in row)
            output.write(f"INSERT INTO stocks ({names}) VALUES ({values});\n")
    legacy = [dict(zip(columns, row)) for row in rows]
    db.init_stock_pool_schema()
    by_market = defaultdict(list)
    for row in legacy:
        by_market[row["market"]].append(row)
    try:
        db.conn.begin()
        for market, items in by_market.items():
            db.upsert_stocks(market, items)
        with db.conn.cursor() as cursor:
            fields = ", ".join(STOCK_CATALOG_FIELDS)
            cursor.execute(f"SELECT market, code, {fields} FROM stock_pools")
            actual = defaultdict(list)
            for row in cursor.fetchall():
                actual[(row[0], row[1])].append(dict(zip(STOCK_CATALOG_FIELDS, row[2:])))
        for row in legacy:
            candidates = actual[(row["market"], row["code"])]
            if not candidates:
                raise RuntimeError(f"Missing migrated security: {row['market']} {row['code']}")
            for field in STOCK_CATALOG_FIELDS:
                expected = row.get(field)
                if expected is not None and expected != "":
                    if any(item[field] != expected for item in candidates):
                        raise RuntimeError(f"Migration mismatch: {row['market']} {row['code']} {field}")
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
    if drop_legacy:
        with db.conn.cursor() as cursor:
            cursor.execute("DROP TABLE stocks")
    print(f"Verified {len(legacy)} securities; backup: {backup}; dropped legacy: {drop_legacy}")
    return len(legacy)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--drop-legacy", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    db = MarketDatabase(mysql_config_from_env())
    try:
        migrate(db, args.backup, drop_legacy=args.drop_legacy)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

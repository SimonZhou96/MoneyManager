#!/usr/bin/env python3
"""Migrate account ownership columns from web_users.id to web_users.user_id."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import MarketDatabase
from web.config import load_dotenv, mysql_config_from_env


RELATION_TABLES = (
    ("web_sessions", "NOT NULL", "idx_web_sessions_user", "user_id"),
    ("single_stock_runs", "NULL", "idx_single_stock_user", "user_id, created_at"),
    ("web_screening_jobs", "NULL", "idx_web_screening_jobs_user", "user_id, created_at"),
    ("option_evaluation_runs", "NULL", "idx_option_eval_user", "user_id, created_at"),
    ("option_order_plans", "NULL", "idx_option_order_user", "user_id, created_at"),
    ("option_tracked_positions", "NULL", "idx_option_position_user", "user_id, created_at"),
    ("quant_backtest_runs", "NULL", "idx_quant_backtest_user", "user_id, created_at"),
    ("quant_paper_accounts", "NULL", "idx_quant_paper_user", "user_id, created_at"),
)


def _column(cursor, table: str, column: str) -> dict | None:
    cursor.execute(
        """
        SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
        FROM information_schema.columns
        WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s
        """,
        (table, column),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {"name": row[0], "type": str(row[1]).lower(), "nullable": row[2] == "YES"}


def _index_exists(cursor, table: str, index: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s LIMIT 1
        """,
        (table, index),
    )
    return cursor.fetchone() is not None


def _table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s LIMIT 1",
        (table,),
    )
    return cursor.fetchone() is not None


def _ensure_user_uuid(cursor) -> None:
    info = _column(cursor, "web_users", "user_id")
    if not info:
        cursor.execute(
            "ALTER TABLE web_users ADD COLUMN user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL AFTER id"
        )
    cursor.execute("SELECT id FROM web_users WHERE user_id IS NULL OR user_id=''")
    for row in cursor.fetchall():
        cursor.execute(
            "UPDATE web_users SET user_id=%s WHERE id=%s AND (user_id IS NULL OR user_id='')",
            (str(uuid.uuid4()), row[0]),
        )
    cursor.execute(
        "ALTER TABLE web_users MODIFY COLUMN user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL"
    )
    if not _index_exists(cursor, "web_users", "uk_web_users_user_id"):
        cursor.execute("ALTER TABLE web_users ADD UNIQUE KEY uk_web_users_user_id (user_id)")


def _ensure_provider_config_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS web_account_provider_configs (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            user_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            provider_key VARCHAR(64) NOT NULL,
            config_ciphertext LONGBLOB NOT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ON UPDATE CURRENT_TIMESTAMP(6),
            PRIMARY KEY (id),
            UNIQUE KEY uk_web_provider_config_user_provider (user_id, provider_key),
            KEY idx_web_provider_config_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        COMMENT='Encrypted account-level provider settings'
        """
    )


def _ensure_relation_user_id(cursor, table: str, nullability: str, index: str, index_columns: str) -> None:
    old = _column(cursor, table, "user_id")
    temporary = _column(cursor, table, "user_id_uuid")
    if not old and temporary:
        if nullability == "NOT NULL":
            cursor.execute(f"SELECT COUNT(1) FROM `{table}` WHERE user_id_uuid IS NULL")
            if int(cursor.fetchone()[0] or 0):
                raise RuntimeError(f"{table}.user_id_uuid contains unmapped required values")
        cursor.execute(
            f"ALTER TABLE `{table}` CHANGE COLUMN user_id_uuid user_id CHAR(36) "
            f"CHARACTER SET ascii COLLATE ascii_bin {nullability}"
        )
        if not _index_exists(cursor, table, index):
            cursor.execute(f"ALTER TABLE `{table}` ADD KEY `{index}` ({index_columns})")
        return
    if not old:
        raise RuntimeError(f"{table}.user_id column is missing")
    if old["type"].startswith(("char(", "varchar(")):
        if old["nullable"] != (nullability == "NULL"):
            cursor.execute(
                f"ALTER TABLE `{table}` MODIFY COLUMN user_id CHAR(36) "
                f"CHARACTER SET ascii COLLATE ascii_bin {nullability}"
            )
        if not _index_exists(cursor, table, index):
            cursor.execute(f"ALTER TABLE `{table}` ADD KEY `{index}` ({index_columns})")
        return

    if not temporary:
        cursor.execute(
            f"ALTER TABLE `{table}` ADD COLUMN user_id_uuid CHAR(36) "
            "CHARACTER SET ascii COLLATE ascii_bin NULL"
        )
    cursor.execute(
        f"""
        UPDATE `{table}` AS relation_row
        LEFT JOIN web_users AS account_row ON account_row.id=relation_row.user_id
        SET relation_row.user_id_uuid=account_row.user_id
        WHERE relation_row.user_id IS NOT NULL AND relation_row.user_id_uuid IS NULL
        """
    )
    cursor.execute(
        f"SELECT COUNT(1) FROM `{table}` WHERE user_id IS NOT NULL AND user_id_uuid IS NULL"
    )
    missing = int(cursor.fetchone()[0] or 0)
    if missing:
        raise RuntimeError(f"{table} has {missing} user_id values that do not map to web_users.id")

    if _index_exists(cursor, table, index):
        cursor.execute(f"ALTER TABLE `{table}` DROP INDEX `{index}`")
    cursor.execute(f"ALTER TABLE `{table}` DROP COLUMN user_id")
    cursor.execute(
        f"ALTER TABLE `{table}` CHANGE COLUMN user_id_uuid user_id CHAR(36) "
        f"CHARACTER SET ascii COLLATE ascii_bin {nullability}"
    )
    cursor.execute(f"ALTER TABLE `{table}` ADD KEY `{index}` ({index_columns})")


def migrate() -> None:
    load_dotenv()
    db = MarketDatabase(mysql_config_from_env())
    try:
        with db.conn.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            database_name = cursor.fetchone()[0]
            if not database_name:
                raise RuntimeError("No MySQL database selected")
            cursor.execute("SELECT COUNT(1) FROM web_users")
            account_count = int(cursor.fetchone()[0] or 0)
            print(f"Database: {database_name}; accounts to assign UUIDs: {account_count}")
            if input("Confirm migration by typing MIGRATE-UUID: ").strip() != "MIGRATE-UUID":
                print("Migration cancelled")
                return

            _ensure_user_uuid(cursor)
            for table, nullability, index, index_columns in RELATION_TABLES:
                if not _table_exists(cursor, table):
                    print(f"Skipped absent table {table}")
                    continue
                _ensure_relation_user_id(cursor, table, nullability, index, index_columns)
                print(f"Migrated {table}.user_id")
            _ensure_provider_config_table(cursor)
        db.conn.commit()
        print("Account UUID migration completed")
    finally:
        db.close()


if __name__ == "__main__":
    migrate()

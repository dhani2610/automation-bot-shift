"""MySQL database layer for Locum ED HJ Auto-Booking Dashboard."""

from __future__ import annotations

import datetime as dt
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

load_dotenv()


@dataclass
class MySQLConfig:
    host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    port: int = int(os.getenv("MYSQL_PORT", "3306"))
    user: str = os.getenv("MYSQL_USER", "root")
    password: str = os.getenv("MYSQL_PASSWORD", "")
    database: str = os.getenv("MYSQL_DB", "locum_booking")


def get_config() -> MySQLConfig:
    load_dotenv(override=True)
    return MySQLConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "locum_booking"),
    )


class DatabaseError(Exception):
    """Raised when MySQL is unreachable or a query fails."""


@contextmanager
def get_conn(with_db: bool = True) -> Iterator[pymysql.connections.Connection]:
    cfg = get_config()
    try:
        conn = pymysql.connect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            database=cfg.database if with_db else None,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=5,
        )
    except pymysql.MySQLError as exc:
        raise DatabaseError(
            f"Gagal terhubung ke MySQL ({cfg.host}:{cfg.port}, db={cfg.database}): {exc}"
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create database and tables if they do not exist yet."""
    cfg = get_config()
    with get_conn(with_db=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{cfg.database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    with get_conn(with_db=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS `accounts` (
                  `id` INT AUTO_INCREMENT PRIMARY KEY,
                  `ic_number` VARCHAR(12) NOT NULL UNIQUE,
                  `last4` VARCHAR(4) NOT NULL,
                  `name` VARCHAR(150) DEFAULT NULL,
                  `role` VARCHAR(20) DEFAULT 'MO',
                  `reg_no` VARCHAR(50) DEFAULT NULL,
                  `workplace` VARCHAR(150) DEFAULT NULL,
                  `phone` VARCHAR(30) DEFAULT NULL,
                  `email` VARCHAR(150) DEFAULT NULL,
                  `last_login_at` DATETIME DEFAULT NULL,
                  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS `booking_jobs` (
                  `id` INT AUTO_INCREMENT PRIMARY KEY,
                  `account_ic` VARCHAR(12) NOT NULL,
                  `title` VARCHAR(150) NOT NULL,
                  `mode` ENUM('scheduled', 'monitor') NOT NULL DEFAULT 'scheduled',
                  `target_dates` TEXT NULL,
                  `target_slots` TEXT NULL,
                  `schedule_time` VARCHAR(8) NULL,
                  `schedule_tz` VARCHAR(10) NOT NULL DEFAULT 'MYT',
                  `schedule_date` DATE NULL,
                  `next_run_at` DATETIME NULL,
                  `poll_interval` DECIMAL(5,2) NOT NULL DEFAULT 2.00,
                  `max_bookings` INT NOT NULL DEFAULT 1,
                  `one_per_day` TINYINT(1) NOT NULL DEFAULT 0,
                  `dry_run` TINYINT(1) NOT NULL DEFAULT 0,
                  `status` ENUM('pending', 'waiting', 'running', 'completed', 'stopped', 'error') NOT NULL DEFAULT 'pending',
                  `success_count` INT NOT NULL DEFAULT 0,
                  `last_message` VARCHAR(255) NULL,
                  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX `idx_jobs_ic_status` (`account_ic`, `status`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS `telegram_settings` (
                  `id` INT AUTO_INCREMENT PRIMARY KEY,
                  `account_ic` VARCHAR(12) NOT NULL UNIQUE,
                  `bot_token` VARCHAR(200) NOT NULL DEFAULT '',
                  `chat_id` VARCHAR(80) NOT NULL DEFAULT '',
                  `enabled` TINYINT(1) NOT NULL DEFAULT 1,
                  `notify_on_success` TINYINT(1) NOT NULL DEFAULT 1,
                  `notify_on_fail` TINYINT(1) NOT NULL DEFAULT 1,
                  `notify_on_dry_run` TINYINT(1) NOT NULL DEFAULT 0,
                  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS `booking_results` (
                  `id` INT AUTO_INCREMENT PRIMARY KEY,
                  `job_id` INT NULL,
                  `account_ic` VARCHAR(12) NOT NULL,
                  `account_name` VARCHAR(150) NULL,
                  `account_role` VARCHAR(20) NULL,
                  `workplace` VARCHAR(150) NULL,
                  `job_title` VARCHAR(150) NULL,
                  `mode` VARCHAR(30) NULL,
                  `outcome` ENUM('SUCCESS', 'FAILED', 'NO_SLOT', 'DRY_RUN') NOT NULL DEFAULT 'SUCCESS',
                  `booking_ref` VARCHAR(50) NULL,
                  `slot_date` VARCHAR(120) NOT NULL,
                  `slot_key` VARCHAR(120) NOT NULL,
                  `slot_label` VARCHAR(120) NULL,
                  `seat` VARCHAR(20) NULL,
                  `reason` TEXT NULL,
                  `telegram_sent` TINYINT(1) NOT NULL DEFAULT 0,
                  `telegram_info` VARCHAR(255) NULL,
                  `is_dry_run` TINYINT(1) NOT NULL DEFAULT 0,
                  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  INDEX `idx_results_ic` (`account_ic`, `created_at`),
                  INDEX `idx_results_outcome` (`account_ic`, `outcome`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            _ensure_booking_results_columns(cur)
            _ensure_bot_logs_columns(cur)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS `bot_logs` (
                  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                  `job_id` INT NULL,
                  `account_ic` VARCHAR(12) NULL,
                  `level` ENUM('INFO', 'SUCCESS', 'WARN', 'ERROR') NOT NULL DEFAULT 'INFO',
                  `message` TEXT NOT NULL,
                  `raw_payload` MEDIUMTEXT NULL,
                  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  INDEX `idx_logs_job_id` (`job_id`, `id`),
                  INDEX `idx_logs_ic_id` (`account_ic`, `id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )


def _ensure_bot_logs_columns(cur: Any) -> None:
    """Safely add raw_payload column to bot_logs table if missing."""
    try:
        cur.execute("SHOW COLUMNS FROM `bot_logs` LIKE 'raw_payload'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE `bot_logs` ADD COLUMN `raw_payload` MEDIUMTEXT NULL AFTER `message`")
    except Exception:
        pass


def _ensure_booking_results_columns(cur: Any) -> None:
    """Safely add new columns if booking_results was created by an older schema."""
    cur.execute("SHOW COLUMNS FROM `booking_results`")
    existing = {row["Field"] for row in cur.fetchall()}
    migrations = [
        ("account_name", "ADD COLUMN `account_name` VARCHAR(150) NULL AFTER `account_ic`"),
        ("account_role", "ADD COLUMN `account_role` VARCHAR(20) NULL AFTER `account_name`"),
        ("workplace", "ADD COLUMN `workplace` VARCHAR(150) NULL AFTER `account_role`"),
        ("job_title", "ADD COLUMN `job_title` VARCHAR(150) NULL AFTER `workplace`"),
        ("mode", "ADD COLUMN `mode` VARCHAR(30) NULL AFTER `job_title`"),
        ("outcome", "ADD COLUMN `outcome` ENUM('SUCCESS', 'FAILED', 'NO_SLOT', 'DRY_RUN') NOT NULL DEFAULT 'SUCCESS' AFTER `mode`"),
        ("reason", "ADD COLUMN `reason` TEXT NULL AFTER `seat`"),
        ("telegram_sent", "ADD COLUMN `telegram_sent` TINYINT(1) NOT NULL DEFAULT 0 AFTER `reason`"),
        ("telegram_info", "ADD COLUMN `telegram_info` VARCHAR(255) NULL AFTER `telegram_sent`"),
    ]
    for col, ddl in migrations:
        if col not in existing:
            cur.execute(f"ALTER TABLE `booking_results` {ddl}")
    cur.execute(
        "ALTER TABLE `booking_results` "
        "MODIFY COLUMN `slot_date` VARCHAR(120) NOT NULL, "
        "MODIFY COLUMN `slot_key` VARCHAR(120) NOT NULL, "
        "MODIFY COLUMN `slot_label` VARCHAR(120) NULL"
    )


def check_health() -> tuple[bool, str]:
    cfg = get_config()
    try:
        try:
            with get_conn(with_db=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
        except DatabaseError as exc:
            msg = str(exc)
            if "1049" in msg or "Unknown database" in msg:
                init_db()
            else:
                raise
        return True, f"Terhubung ke {cfg.host}:{cfg.port}/{cfg.database}"
    except DatabaseError as exc:
        return False, str(exc)


def upsert_account(ic: str, last4: str, profile: dict[str, Any]) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO `accounts` (`ic_number`, `last4`, `name`, `role`, `reg_no`, `workplace`, `phone`, `email`, `last_login_at`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                  `last4` = VALUES(`last4`),
                  `name` = VALUES(`name`),
                  `role` = VALUES(`role`),
                  `reg_no` = VALUES(`reg_no`),
                  `workplace` = VALUES(`workplace`),
                  `phone` = VALUES(`phone`),
                  `email` = VALUES(`email`),
                  `last_login_at` = NOW()
                """,
                (
                    ic,
                    last4,
                    profile.get("name"),
                    profile.get("role", "MO"),
                    profile.get("regNo"),
                    profile.get("workplace"),
                    profile.get("phone"),
                    profile.get("email"),
                ),
            )
            cur.execute("SELECT * FROM `accounts` WHERE `ic_number` = %s", (ic,))
            return dict(cur.fetchone() or {})


def get_account(ic: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM `accounts` WHERE `ic_number` = %s", (ic,))
            row = cur.fetchone()
            return dict(row) if row else None


def list_accounts() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `ic_number`, `name`, `role`, `reg_no`, `workplace`, `last_login_at` "
                "FROM `accounts` ORDER BY `last_login_at` DESC, `id` DESC LIMIT 20"
            )
            return [_serialize_row(dict(r)) for r in cur.fetchall()]


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (dt.datetime, dt.date)):
            out[k] = v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _deserialize_job(row: dict[str, Any]) -> dict[str, Any]:
    out = _serialize_row(row)
    for field in ("target_dates", "target_slots"):
        raw = out.get(field)
        if isinstance(raw, str) and raw:
            try:
                out[field] = json.loads(raw)
            except ValueError:
                out[field] = []
        else:
            out[field] = []
    out["one_per_day"] = bool(out.get("one_per_day"))
    out["dry_run"] = bool(out.get("dry_run"))
    return out


def create_job(data: dict[str, Any]) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO `booking_jobs` (
                  `account_ic`, `title`, `mode`, `target_dates`, `target_slots`,
                  `schedule_time`, `schedule_tz`, `schedule_date`, `next_run_at`,
                  `poll_interval`, `max_bookings`, `one_per_day`, `dry_run`, `status`, `last_message`
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    data["account_ic"],
                    data["title"],
                    data["mode"],
                    json.dumps(data.get("target_dates") or []),
                    json.dumps(data.get("target_slots") or []),
                    data.get("schedule_time"),
                    data.get("schedule_tz", "MYT"),
                    data.get("schedule_date"),
                    data.get("next_run_at"),
                    float(data.get("poll_interval", 2.0)),
                    int(data.get("max_bookings", 1)),
                    1 if data.get("one_per_day") else 0,
                    1 if data.get("dry_run") else 0,
                    data.get("status", "pending"),
                    data.get("last_message"),
                ),
            )
            job_id = cur.lastrowid
            cur.execute("SELECT * FROM `booking_jobs` WHERE `id` = %s", (job_id,))
            return _deserialize_job(dict(cur.fetchone()))


def get_job(job_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM `booking_jobs` WHERE `id` = %s", (job_id,))
            row = cur.fetchone()
            return _deserialize_job(dict(row)) if row else None


def list_jobs(account_ic: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if account_ic:
                cur.execute(
                    "SELECT * FROM `booking_jobs` WHERE `account_ic` = %s ORDER BY `id` DESC LIMIT %s",
                    (account_ic, limit),
                )
            else:
                cur.execute("SELECT * FROM `booking_jobs` ORDER BY `id` DESC LIMIT %s", (limit,))
            return [_deserialize_job(dict(r)) for r in cur.fetchall()]


def list_active_jobs_all() -> list[dict[str, Any]]:
    """Return all jobs left in waiting/running state (for auto-resume on server boot)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `booking_jobs` WHERE `status` IN ('waiting', 'running') ORDER BY `id` ASC"
            )
            return [_deserialize_job(dict(r)) for r in cur.fetchall()]


def update_job_status(
    job_id: int,
    status: str,
    last_message: str | None = None,
    success_count: int | None = None,
    next_run_at: dt.datetime | None = None,
    clear_next_run: bool = False,
) -> None:
    sets = ["`status` = %s"]
    params: list[Any] = [status]
    if last_message is not None:
        sets.append("`last_message` = %s")
        params.append(last_message[:255])
    if success_count is not None:
        sets.append("`success_count` = %s")
        params.append(success_count)
    if next_run_at is not None:
        sets.append("`next_run_at` = %s")
        params.append(next_run_at.strftime("%Y-%m-%d %H:%M:%S"))
    elif clear_next_run:
        sets.append("`next_run_at` = NULL")
    params.append(job_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE `booking_jobs` SET {', '.join(sets)} WHERE `id` = %s", params)


def delete_job(job_id: int, account_ic: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM `booking_jobs` WHERE `id` = %s AND `account_ic` = %s",
                (job_id, account_ic),
            )
            return cur.rowcount > 0


def add_booking_result(
    *,
    job_id: int | None,
    account_ic: str,
    account_name: str | None = None,
    account_role: str | None = None,
    workplace: str | None = None,
    job_title: str | None = None,
    mode: str | None = None,
    outcome: str = "SUCCESS",
    booking_ref: str | None = None,
    slot_date: str,
    slot_key: str,
    slot_label: str | None = None,
    seat: str | None = None,
    reason: str | None = None,
    is_dry_run: bool = False,
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO `booking_results` (
                  `job_id`, `account_ic`, `account_name`, `account_role`, `workplace`,
                  `job_title`, `mode`, `outcome`, `booking_ref`,
                  `slot_date`, `slot_key`, `slot_label`, `seat`, `reason`, `is_dry_run`
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    account_ic[:12],
                    (account_name or "")[:150] or None,
                    (account_role or "")[:20] or None,
                    (workplace or "")[:150] or None,
                    (job_title or "")[:150] or None,
                    (mode or "")[:30] or None,
                    outcome,
                    (booking_ref or "")[:50] or None,
                    slot_date[:120],
                    slot_key[:120],
                    (slot_label or slot_key)[:120],
                    (seat or "")[:20] or None,
                    reason,
                    1 if is_dry_run else 0,
                ),
            )
            return int(cur.lastrowid)


def update_booking_result_telegram(result_id: int, sent: bool, info: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `booking_results` SET `telegram_sent` = %s, `telegram_info` = %s WHERE `id` = %s",
                (1 if sent else 0, info[:255], result_id),
            )


def list_booking_results(
    account_ic: str | None = None,
    outcome: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if account_ic:
        clauses.append("`account_ic` = %s")
        params.append(account_ic)
    if outcome and outcome != "ALL":
        if outcome == "FAILED_GROUP":
            clauses.append("`outcome` IN ('FAILED', 'NO_SLOT')")
        else:
            clauses.append("`outcome` = %s")
            params.append(outcome)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM `booking_results` {where} ORDER BY `id` DESC LIMIT %s",
                params,
            )
            rows = []
            for r in cur.fetchall():
                item = _serialize_row(dict(r))
                item["is_dry_run"] = bool(item.get("is_dry_run"))
                item["telegram_sent"] = bool(item.get("telegram_sent"))
                rows.append(item)
            return rows


def get_telegram_settings(account_ic: str) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM `telegram_settings` WHERE `account_ic` = %s", (account_ic,))
            row = cur.fetchone()
            if row:
                item = _serialize_row(dict(row))
                item["enabled"] = bool(item.get("enabled"))
                item["notify_on_success"] = bool(item.get("notify_on_success"))
                item["notify_on_fail"] = bool(item.get("notify_on_fail"))
                item["notify_on_dry_run"] = bool(item.get("notify_on_dry_run"))
                return item
    return {
        "account_ic": account_ic,
        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "enabled": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
        "notify_on_success": True,
        "notify_on_fail": True,
        "notify_on_dry_run": False,
    }


def upsert_telegram_settings(
    account_ic: str,
    bot_token: str,
    chat_id: str,
    enabled: bool,
    notify_on_success: bool,
    notify_on_fail: bool,
    notify_on_dry_run: bool,
) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO `telegram_settings` (
                  `account_ic`, `bot_token`, `chat_id`, `enabled`,
                  `notify_on_success`, `notify_on_fail`, `notify_on_dry_run`
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  `bot_token` = VALUES(`bot_token`),
                  `chat_id` = VALUES(`chat_id`),
                  `enabled` = VALUES(`enabled`),
                  `notify_on_success` = VALUES(`notify_on_success`),
                  `notify_on_fail` = VALUES(`notify_on_fail`),
                  `notify_on_dry_run` = VALUES(`notify_on_dry_run`)
                """,
                (
                    account_ic,
                    bot_token.strip(),
                    chat_id.strip(),
                    1 if enabled else 0,
                    1 if notify_on_success else 0,
                    1 if notify_on_fail else 0,
                    1 if notify_on_dry_run else 0,
                ),
            )
    return get_telegram_settings(account_ic)


def add_log(
    job_id: int | None,
    account_ic: str | None,
    level: str,
    message: str,
    raw_payload: str | None = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO `bot_logs` (`job_id`, `account_ic`, `level`, `message`, `raw_payload`) VALUES (%s, %s, %s, %s, %s)",
                (job_id, account_ic, level, message, raw_payload),
            )


def list_logs(
    account_ic: str | None = None,
    job_id: int | None = None,
    level: str | None = None,
    limit: int = 150,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if account_ic:
        clauses.append("l.`account_ic` = %s")
        params.append(account_ic)
    if job_id is not None:
        if job_id == 0:
            clauses.append("l.`job_id` IS NULL")
        else:
            clauses.append("l.`job_id` = %s")
            params.append(job_id)
    if level and level != "ALL":
        clauses.append("l.`level` = %s")
        params.append(level)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT l.*, j.`title` AS `job_title`
                FROM `bot_logs` l
                LEFT JOIN `booking_jobs` j ON l.`job_id` = j.`id`
                {where}
                ORDER BY l.`id` DESC
                LIMIT %s
                """,
                params,
            )
            return [_serialize_row(dict(r)) for r in cur.fetchall()]


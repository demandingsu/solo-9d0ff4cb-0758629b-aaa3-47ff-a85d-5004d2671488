"""SQLite 连接与表结构。"""
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_no       TEXT NOT NULL UNIQUE,           -- 公告编号（业务主键）
    project_no      TEXT,                           -- 项目编号
    notice_type     TEXT,                           -- first/correction/revocation
    publish_time    TEXT,                           -- 发布时间（ISO 风格字符串）
    deadline        TEXT,                           -- 截止时间（ISO 风格字符串）
    ref_notice_no   TEXT,                           -- 引用编号
    source_url      TEXT,                           -- 来源（详情页）链接
    list_url        TEXT,                           -- 所属列表页
    content_hash    TEXT,                           -- 内容指纹，用于识别变更
    chain_id        TEXT,                           -- 所属链（首次公告编号）；孤立公告为 NULL
    chain_status    TEXT,                           -- ok / conflict / cancelled / orphan
    conflict_reasons TEXT,                          -- 冲突原因，JSON 数组字符串
    effective_deadline TEXT,                        -- 有效截止时间（计算结果）
    last_synced_at  TEXT NOT NULL,                  -- 最近一次同步时间
    last_fetch_error TEXT                           -- 最近一次详情抓取错误（成功后清空）
);

CREATE INDEX IF NOT EXISTS idx_ann_project ON announcements(project_no);
CREATE INDEX IF NOT EXISTS idx_ann_chain ON announcements(chain_id);
CREATE INDEX IF NOT EXISTS idx_ann_ref ON announcements(ref_notice_no);

CREATE TABLE IF NOT EXISTS sync_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL UNIQUE,
    list_url        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,                  -- success / partial / failed
    total_listed    INTEGER NOT NULL DEFAULT 0,
    fetched_ok      INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    kept_last       INTEGER NOT NULL DEFAULT 0,
    message         TEXT
);
"""


def get_db_path() -> Path:
    return config.DB_PATH


def connect() -> sqlite3.Connection:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()

"""同步服务：抓取列表页 + 详情页，持久化并触发链重算。"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime

from . import chains, config, db, fetcher, parser
from .fetcher import FetchError

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _content_hash(detail: parser.NoticeDetail) -> str:
    raw = "|".join(
        str(getattr(detail, field) or "")
        for field in (
            "notice_no", "project_no", "notice_type", "publish_time",
            "deadline", "ref_notice_no",
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _start_run(conn: sqlite3.Connection, list_url: str) -> str:
    run_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sync_runs (run_id, list_url, started_at, status) "
        "VALUES (?, ?, ?, 'running')",
        (run_id, list_url, _now()),
    )
    conn.commit()
    return run_id


def _finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    total: int,
    fetched: int,
    failed: int,
    kept: int,
    message: str | None,
) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, status = ?, total_listed = ?, "
        "fetched_ok = ?, failed_count = ?, kept_last = ?, message = ? "
        "WHERE run_id = ?",
        (_now(), status, total, fetched, failed, kept, message, run_id),
    )
    conn.commit()


def _upsert_detail(
    conn: sqlite3.Connection,
    detail: parser.NoticeDetail,
    list_url: str,
) -> None:
    digest = _content_hash(detail)
    existing = conn.execute(
        "SELECT id FROM announcements WHERE notice_no = ?", (detail.notice_no,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE announcements SET project_no = ?, notice_type = ?, "
            "publish_time = ?, deadline = ?, ref_notice_no = ?, source_url = ?, "
            "list_url = ?, content_hash = ?, last_synced_at = ?, "
            "last_fetch_error = NULL WHERE notice_no = ?",
            (
                detail.project_no, detail.notice_type, detail.publish_time,
                detail.deadline, detail.ref_notice_no, detail.source_url,
                list_url, digest, _now(), detail.notice_no,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO announcements (notice_no, project_no, notice_type, "
            "publish_time, deadline, ref_notice_no, source_url, list_url, "
            "content_hash, last_synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                detail.notice_no, detail.project_no, detail.notice_type,
                detail.publish_time, detail.deadline, detail.ref_notice_no,
                detail.source_url, list_url, digest, _now(),
            ),
        )


def _record_failure(
    conn: sqlite3.Connection, notice_no: str, list_url: str, error: str
) -> None:
    """详情抓取失败：已有记录则保留上次完整结果，仅刷新同步时间与错误信息。"""
    existing = conn.execute(
        "SELECT id FROM announcements WHERE notice_no = ?", (notice_no,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE announcements SET last_synced_at = ?, last_fetch_error = ? "
            "WHERE notice_no = ?",
            (_now(), error, notice_no),
        )
    else:
        # 从未成功抓取过：落一条占位记录，交由链计算归入孤立异常
        conn.execute(
            "INSERT INTO announcements (notice_no, project_no, notice_type, "
            "list_url, last_synced_at, last_fetch_error) VALUES (?, ?, ?, ?, ?, ?)",
            (notice_no, None, None, list_url, _now(), error),
        )


def sync_list(list_url: str) -> dict:
    """执行一次完整同步并返回统计信息。"""
    conn = db.connect()
    db.init_db(conn)
    run_id = _start_run(conn, list_url)

    try:
        list_html = fetcher.fetch(list_url)
    except FetchError as exc:
        logger.error("列表页抓取失败：%s", exc)
        _finish_run(conn, run_id, "failed", 0, 0, 0, 0, str(exc))
        conn.close()
        return {
            "run_id": run_id, "status": "failed", "total_listed": 0,
            "fetched_ok": 0, "failed_count": 0, "kept_last": 0,
            "message": str(exc),
        }

    listed = parser.parse_list(list_html, list_url)
    total = len(listed)
    fetched_ok = failed = kept = 0

    for item in listed:
        try:
            detail_html = fetcher.fetch(item.detail_url)
            detail = parser.parse_detail(detail_html, item.detail_url)
            _upsert_detail(conn, detail, list_url)
            fetched_ok += 1
        except (FetchError, ValueError) as exc:
            failed += 1
            logger.warning("详情页 %s 抓取/解析失败：%s", item.notice_no, exc)
            existed = conn.execute(
                "SELECT id FROM announcements WHERE notice_no = ?",
                (item.notice_no,),
            ).fetchone()
            _record_failure(conn, item.notice_no, list_url, str(exc))
            if existed and config.KEEP_LAST_ON_DETAIL_FAILURE:
                kept += 1
    conn.commit()

    # 无论是否有详情失败，都基于当前库内数据重算链状态
    chains.recompute(conn)

    status = "success" if failed == 0 else "partial"
    message = None
    if failed:
        message = f"{failed} 条详情抓取失败，其中 {kept} 条保留上次完整结果"
    _finish_run(conn, run_id, status, total, fetched_ok, failed, kept, message)
    conn.close()

    return {
        "run_id": run_id, "status": status, "total_listed": total,
        "fetched_ok": fetched_ok, "failed_count": failed, "kept_last": kept,
        "message": message,
    }

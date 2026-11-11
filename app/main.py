"""FastAPI 路由定义。

接口
----
GET  /health                       健康检查
POST /api/v1/sync                  同步指定列表页及其详情页
GET  /api/v1/chains                查询全部引用链与孤立异常公告
GET  /api/v1/announcements/{no}    查询单条公告
GET  /api/v1/sync-runs             查询同步执行记录
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from . import db, service
from .models import (
    AnnouncementItem,
    Chain,
    OrphanAnnouncement,
    QueryResponse,
    SyncRequest,
    SyncResponse,
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = db.connect()
    db.init_db(conn)
    conn.close()
    yield


app = FastAPI(
    title="招投标公告采集 API",
    description="采集公共采购公告，按引用链归并首次公告/更正/撤销并识别冲突。",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/sync", response_model=SyncResponse)
def run_sync(payload: SyncRequest) -> dict:
    if not payload.list_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="list_url 必须是 http(s) URL")
    return service.sync_list(payload.list_url)


def _loads(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def _item(row: dict) -> AnnouncementItem:
    return AnnouncementItem(
        notice_no=row["notice_no"],
        project_no=row["project_no"],
        notice_type=row["notice_type"],
        publish_time=row["publish_time"],
        deadline=row["deadline"],
        ref_notice_no=row["ref_notice_no"],
        source_url=row["source_url"],
        conflict_reasons=_loads(row["conflict_reasons"]),
        effective_deadline=row["effective_deadline"],
    )


@app.get("/api/v1/chains", response_model=QueryResponse)
def query_chains() -> dict:
    conn = db.connect()
    db.init_db(conn)

    chain_rows = conn.execute(
        "SELECT DISTINCT chain_id FROM announcements "
        "WHERE chain_id IS NOT NULL ORDER BY chain_id"
    ).fetchall()

    chains_out: list[Chain] = []
    for row in chain_rows:
        chain_id = row["chain_id"]
        members = conn.execute(
            "SELECT * FROM announcements WHERE chain_id = ? ORDER BY id",
            (chain_id,),
        ).fetchall()
        if not members:
            continue
        head = members[0]
        chains_out.append(
            Chain(
                chain_id=chain_id,
                project_no=head["project_no"],
                status=head["chain_status"],
                effective_deadline=head["effective_deadline"],
                announcements=[_item(dict(m)) for m in members],
            )
        )

    orphan_rows = conn.execute(
        "SELECT * FROM announcements WHERE chain_id IS NULL ORDER BY id"
    ).fetchall()
    orphans = [
        OrphanAnnouncement(
            notice_no=r["notice_no"],
            project_no=r["project_no"],
            notice_type=r["notice_type"],
            status=r["chain_status"] or "orphan",
            conflict_reasons=_loads(r["conflict_reasons"]),
            effective_deadline=r["effective_deadline"],
            source_url=r["source_url"],
            publish_time=r["publish_time"],
            deadline=r["deadline"],
            ref_notice_no=r["ref_notice_no"],
        )
        for r in orphan_rows
    ]
    conn.close()
    return {"chains": chains_out, "orphans": orphans}


@app.get("/api/v1/announcements/{notice_no}")
def get_announcement(notice_no: str) -> dict:
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM announcements WHERE notice_no = ?", (notice_no,)
    ).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    data = dict(row)
    data["conflict_reasons"] = _loads(data["conflict_reasons"])
    return data


@app.get("/api/v1/sync-runs")
def list_sync_runs(limit: int = 20) -> dict:
    limit = max(1, min(limit, 100))
    conn = db.connect()
    rows = conn.execute(
        "SELECT run_id, list_url, started_at, finished_at, status, "
        "total_listed, fetched_ok, failed_count, kept_last, message "
        "FROM sync_runs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return {"runs": [dict(r) for r in rows]}

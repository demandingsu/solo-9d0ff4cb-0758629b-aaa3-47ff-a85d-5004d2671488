"""截止时间变动追踪：基于“完整采集可确认状态”基准的对比与落库。

业务规则
--------
1. 按列表 URL（采购目录）为每条首次公告链保存最近一次**完整采集**的可确认
   状态作为基准；新链首次出现只建立基准，不记录变动。
2. 后续**完整采集**（列表页与全部详情页均抓取、解析成功）才与基准对比：
     - 截止时间提前 -> ``advanced``
     - 截止时间延后 -> ``postponed``
     - 链被撤销     -> ``revoked``（新截止时间为 NULL）
     - 撤销后恢复   -> ``restored``（原截止时间为 NULL）
3. 列表页或任一详情页抓取/解析失败时本次同步不推进基准、不记录变动；
   部分同步已写入的更正公告，在恢复成功后的下一次完整采集中仍会被发现。
4. 链处于冲突（conflict）时显示“待核实”（``needs_review=1``）并保留旧基准，
   解除冲突后再与保留的基准比较。
5. 重复同步相同内容不产生新变动（基准已是当前状态，对比结果为空）。
6. 基准与变动记录随同步事务一起提交（由 service 层控制单一事务）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

# 变动类型
ADVANCED = "advanced"      # 截止时间提前
POSTPONED = "postponed"    # 截止时间延后
REVOKED = "revoked"        # 公告撤销，截止时间失效
RESTORED = "restored"      # 撤销后恢复，重新给出截止时间

CONFIRMABLE_STATUSES = ("ok", "cancelled")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _chain_ids(conn: sqlite3.Connection, list_url: str) -> list[str]:
    """本次目录下当前库内存在的链（链上公告均来自该列表页）。"""
    rows = conn.execute(
        "SELECT DISTINCT chain_id FROM announcements "
        "WHERE chain_id IS NOT NULL AND list_url = ? ORDER BY chain_id",
        (list_url,),
    ).fetchall()
    return [r["chain_id"] for r in rows]


def _members(conn: sqlite3.Connection, chain_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM announcements WHERE chain_id = ? ORDER BY id",
        (chain_id,),
    ).fetchall()


def _pick_notice(
    members: list[sqlite3.Row], wanted_type: str | None = None
) -> sqlite3.Row | None:
    """取链上承载当前有效截止时间的公告（或撤销公告）。"""
    if wanted_type is not None:
        for node in reversed(members):
            if node["notice_type"] == wanted_type:
                return node
        return None
    for node in reversed(members):
        if node["deadline"]:
            return node
    return members[0] if members else None


def _classify(
    baseline_status: str | None,
    baseline_deadline: str | None,
    current_status: str,
    current_deadline: str | None,
) -> str | None:
    """对比基准与当前可确认状态，返回变动类型；无变动返回 None。"""
    if baseline_status is None:
        return None  # 首次建立基准
    if baseline_status == "cancelled" and current_status == "ok":
        return RESTORED
    if current_status == "cancelled" and baseline_status != "cancelled":
        return REVOKED
    if current_status == "ok" and baseline_status == "ok":
        if baseline_deadline and current_deadline:
            if current_deadline < baseline_deadline:
                return ADVANCED
            if current_deadline > baseline_deadline:
                return POSTPONED
        elif current_deadline != baseline_deadline:
            # 一侧缺少截止时间而另一侧给出：按时间有无归类为延后/提前
            return POSTPONED if current_deadline else ADVANCED
    return None


def _insert_pending(
    conn: sqlite3.Connection,
    list_url: str,
    chain_id: str,
    head_no: str | None,
) -> None:
    """冲突链首次出现：建立“待核实”基准，确认字段留空，不记录变动。"""
    conn.execute(
        "INSERT INTO chain_baselines (list_url, chain_id, head_notice_no, "
        "chain_status, confirmed_deadline, needs_review, first_seen_at, "
        "last_checked_at) VALUES (?, ?, ?, NULL, NULL, 1, ?, ?)",
        (list_url, chain_id, head_no, _now(), _now()),
    )


def _mark_pending(
    conn: sqlite3.Connection, list_url: str, chain_id: str, head_no: str | None
) -> None:
    """已存在的链进入冲突：保留旧基准，仅置待核实并刷新检查时间。"""
    conn.execute(
        "UPDATE chain_baselines SET needs_review = 1, last_checked_at = ?, "
        "head_notice_no = ? WHERE list_url = ? AND chain_id = ?",
        (_now(), head_no, list_url, chain_id),
    )


def _advance_baseline(
    conn: sqlite3.Connection,
    list_url: str,
    chain_id: str,
    head_no: str | None,
    status: str,
    deadline: str | None,
) -> None:
    """确认状态落基准：已存在则推进并解除待核实，不存在则建立首条基准。"""
    conn.execute(
        "INSERT INTO chain_baselines (list_url, chain_id, head_notice_no, "
        "chain_status, confirmed_deadline, needs_review, first_seen_at, "
        "last_checked_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?) "
        "ON CONFLICT(list_url, chain_id) DO UPDATE SET "
        "head_notice_no = excluded.head_notice_no, "
        "chain_status = excluded.chain_status, "
        "confirmed_deadline = excluded.confirmed_deadline, "
        "needs_review = 0, last_checked_at = excluded.last_checked_at",
        (list_url, chain_id, head_no, status, deadline, _now(), _now()),
    )


def _record_change(
    conn: sqlite3.Connection,
    run_id: str,
    list_url: str,
    chain_id: str,
    change_type: str,
    old_deadline: str | None,
    new_deadline: str | None,
    notice: sqlite3.Row | None,
) -> None:
    notice_no = notice["notice_no"] if notice is not None else chain_id
    source_url = notice["source_url"] if notice is not None else None
    # UNIQUE(run_id, chain_id) 兜底：同一次同步对同一条链至多一条变动
    conn.execute(
        "INSERT OR IGNORE INTO deadline_changes (run_id, list_url, chain_id, "
        "change_type, old_deadline, new_deadline, notice_no, source_url, "
        "detected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id, list_url, chain_id, change_type, old_deadline,
            new_deadline, notice_no, source_url, _now(),
        ),
    )


def track_changes(
    conn: sqlite3.Connection, list_url: str, run_id: str
) -> list[dict]:
    """完整采集后对比基准、写入变动并推进基准。

    仅在同步成功（列表与全部详情均抓取/解析成功）后调用；调用方负责在同一
    事务内提交。返回本次写入的变动记录列表。
    """
    changes: list[dict] = []
    for chain_id in _chain_ids(conn, list_url):
        members = _members(conn, chain_id)
        if not members:
            continue
        head = members[0]
        head_no = head["notice_no"]
        current_status = head["chain_status"]

        baseline = conn.execute(
            "SELECT * FROM chain_baselines WHERE list_url = ? AND chain_id = ?",
            (list_url, chain_id),
        ).fetchone()

        # 冲突链：待核实并保留旧基准，解除冲突前不比较、不推进
        if current_status not in CONFIRMABLE_STATUSES:
            if baseline is None:
                _insert_pending(conn, list_url, chain_id, head_no)
            else:
                _mark_pending(conn, list_url, chain_id, head_no)
            continue

        current_deadline = head["effective_deadline"]
        baseline_status = baseline["chain_status"] if baseline else None
        baseline_deadline = (
            baseline["confirmed_deadline"] if baseline else None
        )

        change_type = _classify(
            baseline_status, baseline_deadline, current_status, current_deadline
        )
        if change_type is not None:
            if change_type == REVOKED:
                notice = _pick_notice(members, wanted_type="revocation")
            else:
                notice = _pick_notice(members)
            _record_change(
                conn, run_id, list_url, chain_id, change_type,
                baseline_deadline, current_deadline, notice,
            )
            changes.append(
                {
                    "chain_id": chain_id,
                    "change_type": change_type,
                    "old_deadline": baseline_deadline,
                    "new_deadline": current_deadline,
                    "notice_no": notice["notice_no"] if notice else chain_id,
                    "source_url": notice["source_url"] if notice else None,
                }
            )

        _advance_baseline(
            conn, list_url, chain_id, head_no, current_status, current_deadline
        )
    return changes

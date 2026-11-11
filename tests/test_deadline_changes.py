"""截止时间变动追踪：基准建立、提前/延后/撤销、失败不推进、冲突待核实、幂等。"""
from __future__ import annotations

from http.client import HTTPConnection
from urllib.parse import quote

from app import chains, changes, db


I001_DEFAULT = "2026-12-10 09:30:00"
I001_EARLIER = "2026-12-05 09:30:00"
I001_LATER = "2026-12-20 09:30:00"


def _toggle(base_url: str, path: str) -> None:
    conn = HTTPConnection("127.0.0.1", base_url.rsplit(":", 1)[1])
    conn.request("GET", path)
    resp = conn.getresponse()
    resp.read()
    conn.close()


def _deadline_toggle(base_url: str, deadline: str | None) -> None:
    if deadline is None:
        path = "/deadline-toggle"
    else:
        path = f"/deadline-toggle?deadline={quote(deadline, safe='')}"
    _toggle(base_url, path)


def _revoke_toggle(base_url: str, enabled: bool) -> None:
    _toggle(base_url, f"/revoke-toggle?enabled={'1' if enabled else '0'}")


def _sync(client, list_url) -> dict:
    resp = client.post("/api/v1/sync", json={"list_url": list_url})
    assert resp.status_code == 200
    return resp.json()


def _baseline(client, list_url) -> dict:
    rows = client.get(
        "/api/v1/baselines", params={"list_url": list_url}
    ).json()["baselines"]
    return {b["chain_id"]: b for b in rows}


def _changes(client, list_url) -> list[dict]:
    return client.get(
        "/api/v1/deadline-changes", params={"list_url": list_url}
    ).json()["changes"]


# ---------------------------------------------------------------------------
# 端到端（经 FastAPI + 示例站）
# ---------------------------------------------------------------------------

def test_first_sync_only_establishes_baseline(client, site_base_url):
    c, _ = client
    list_url = f"{site_base_url}/announcements"
    body = _sync(c, list_url)
    assert body["changes_recorded"] == 0
    assert _changes(c, list_url) == []

    baseline = _baseline(c, list_url)["I-001"]
    assert baseline["chain_status"] == "ok"
    assert baseline["confirmed_deadline"] == I001_DEFAULT
    assert baseline["needs_review"] is False
    assert baseline["head_notice_no"] == "I-001"


def test_deadline_advanced_then_postponed_records_old_new_and_source(
    client, site_base_url
):
    c, _ = client
    list_url = f"{site_base_url}/announcements"
    _sync(c, list_url)

    # 提前
    _deadline_toggle(site_base_url, I001_EARLIER)
    body = _sync(c, list_url)
    assert body["status"] == "success"
    assert body["changes_recorded"] == 1
    found = _changes(c, list_url)
    assert len(found) == 1
    change = found[0]
    assert change["chain_id"] == "I-001"
    assert change["change_type"] == "advanced"
    assert change["old_deadline"] == I001_DEFAULT
    assert change["new_deadline"] == I001_EARLIER
    assert change["notice_no"] == "I-001"
    assert change["source_url"].endswith("/notice/I-001")
    # 基准推进到新状态
    assert _baseline(c, list_url)["I-001"]["confirmed_deadline"] == I001_EARLIER

    # 延后
    _deadline_toggle(site_base_url, I001_LATER)
    _sync(c, list_url)
    found = _changes(c, list_url)
    assert len(found) == 2
    latest = found[0]  # 倒序返回，最新在前
    assert latest["change_type"] == "postponed"
    assert latest["old_deadline"] == I001_EARLIER
    assert latest["new_deadline"] == I001_LATER
    assert _baseline(c, list_url)["I-001"]["confirmed_deadline"] == I001_LATER


def test_repeated_sync_same_content_records_nothing(client, site_base_url):
    c, _ = client
    list_url = f"{site_base_url}/announcements"
    _sync(c, list_url)
    _deadline_toggle(site_base_url, I001_EARLIER)
    _sync(c, list_url)
    assert len(_changes(c, list_url)) == 1

    # 内容不变再同步两次：不重复记录，基准检查时间可刷新但无新变动
    _sync(c, list_url)
    _sync(c, list_url)
    assert len(_changes(c, list_url)) == 1


def test_revocation_records_revoked_then_restore(client, site_base_url):
    c, _ = client
    list_url = f"{site_base_url}/announcements"
    _sync(c, list_url)

    # I-002 切换为撤销公告
    _revoke_toggle(site_base_url, True)
    _sync(c, list_url)
    found = _changes(c, list_url)
    assert len(found) == 1
    change = found[0]
    assert change["change_type"] == "revoked"
    assert change["old_deadline"] == I001_DEFAULT
    assert change["new_deadline"] is None
    assert change["notice_no"] == "I-002"
    assert change["source_url"].endswith("/notice/I-002")

    baseline = _baseline(c, list_url)["I-001"]
    assert baseline["chain_status"] == "cancelled"
    assert baseline["confirmed_deadline"] is None

    # I-002 恢复为更正公告 -> 链恢复，记录 restored
    _revoke_toggle(site_base_url, False)
    _sync(c, list_url)
    found = _changes(c, list_url)
    assert len(found) == 2
    latest = found[0]
    assert latest["change_type"] == "restored"
    assert latest["old_deadline"] is None
    assert latest["new_deadline"] == I001_DEFAULT
    assert _baseline(c, list_url)["I-001"]["chain_status"] == "ok"


def test_detail_failure_does_not_advance_baseline_and_recovery_detects_change(
    client, site_base_url, set_fail
):
    c, sample_server = client
    list_url = f"{site_base_url}/announcements"
    _sync(c, list_url)

    # 截止时间已提前，但同时 H-001 详情故障 -> 本次为部分同步
    _deadline_toggle(site_base_url, I001_EARLIER)
    set_fail(True)
    partial = _sync(c, list_url)
    assert partial["status"] == "partial"
    assert partial["changes_recorded"] == 0
    # 基准不推进、不记录变动
    assert _changes(c, list_url) == []
    assert _baseline(c, list_url)["I-001"]["confirmed_deadline"] == I001_DEFAULT

    # 故障恢复后完整采集：此前部分同步已写入的更正仍须被发现
    set_fail(False)
    healed = _sync(c, list_url)
    assert healed["status"] == "success"
    assert healed["changes_recorded"] == 1
    found = _changes(c, list_url)
    assert len(found) == 1
    assert found[0]["change_type"] == "advanced"
    assert found[0]["old_deadline"] == I001_DEFAULT
    assert found[0]["new_deadline"] == I001_EARLIER


def test_list_page_failure_keeps_baseline_unchanged(client):
    c, _ = client
    bad_list = "http://127.0.0.1:1/announcements"
    body = _sync(c, bad_list)
    assert body["status"] == "failed"
    assert body["changes_recorded"] == 0
    assert c.get(
        "/api/v1/deadline-changes", params={"list_url": bad_list}
    ).json()["changes"] == []
    assert c.get(
        "/api/v1/baselines", params={"list_url": bad_list}
    ).json()["baselines"] == []


def test_deadline_changes_can_be_filtered_and_limited(client, site_base_url):
    c, _ = client
    list_url = f"{site_base_url}/announcements"
    _sync(c, list_url)
    _deadline_toggle(site_base_url, I001_EARLIER)
    _sync(c, list_url)

    # 按目录过滤
    assert len(_changes(c, list_url)) == 1
    # 不存在的目录 -> 空
    other = c.get(
        "/api/v1/deadline-changes",
        params={"list_url": "http://other.example/list"},
    ).json()
    assert other["changes"] == []
    # limit 生效
    limited = c.get("/api/v1/deadline-changes", params={"limit": 1}).json()
    assert len(limited["changes"]) == 1


# ---------------------------------------------------------------------------
# 单元测试：冲突链待核实，解除冲突后再与保留的旧基准比较
# ---------------------------------------------------------------------------

def _open_conn(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setenv("DB_PATH", str(tmp_path / "changes.db"))
    config.DB_PATH = tmp_path / "changes.db"
    conn = db.connect()
    db.init_db(conn)
    return conn


def _insert(conn, no, ntype, ref=None, deadline=None, project="P",
            list_url="http://x/list", chain_status=None):
    conn.execute(
        "INSERT INTO announcements (notice_no, project_no, notice_type, "
        "deadline, ref_notice_no, source_url, list_url, chain_id, "
        "chain_status, effective_deadline, last_synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            no, project, ntype, deadline, ref, f"http://x/notice/{no}",
            list_url, chain_status, chain_status,
            deadline if chain_status == "ok" else None,
            "2026-11-01 00:00:00",
        ),
    )
    conn.commit()


def test_conflict_chain_pending_keeps_old_baseline_then_compares_on_resolve(
    tmp_path, monkeypatch
):
    conn = _open_conn(tmp_path, monkeypatch)
    list_url = "http://x/list"

    # 首次完整采集：正常链，建立基准 11-20
    _insert(conn, "K-1", chains.FIRST, deadline="2026-11-20 09:30:00",
            chain_status="ok")
    chains.recompute(conn)
    run1 = "run-1"
    found = changes.track_changes(conn, list_url, run1)
    conn.commit()
    assert found == []
    baseline = conn.execute(
        "SELECT * FROM chain_baselines WHERE chain_id = 'K-1'"
    ).fetchone()
    assert baseline["needs_review"] == 0
    assert baseline["confirmed_deadline"] == "2026-11-20 09:30:00"

    # 并行更正出现 -> 链冲突：待核实，旧基准保留，不记录变动
    conn.execute(
        "INSERT INTO announcements (notice_no, project_no, notice_type, "
        "deadline, ref_notice_no, source_url, list_url, last_synced_at) "
        "VALUES ('K-2', 'P', 'correction', '2026-12-01 09:30:00', 'K-1', "
        "'http://x/notice/K-2', ?, '2026-11-02 00:00:00')",
        (list_url,),
    )
    conn.execute(
        "INSERT INTO announcements (notice_no, project_no, notice_type, "
        "deadline, ref_notice_no, source_url, list_url, last_synced_at) "
        "VALUES ('K-3', 'P', 'correction', '2026-12-02 09:30:00', 'K-1', "
        "'http://x/notice/K-3', ?, '2026-11-02 00:00:00')",
        (list_url,),
    )
    conn.commit()
    chains.recompute(conn)
    found = changes.track_changes(conn, list_url, "run-2")
    conn.commit()
    assert found == []
    baseline = conn.execute(
        "SELECT * FROM chain_baselines WHERE chain_id = 'K-1'"
    ).fetchone()
    assert baseline["needs_review"] == 1
    # 旧基准原封不动
    assert baseline["confirmed_deadline"] == "2026-11-20 09:30:00"
    assert baseline["chain_status"] == "ok"

    # 解除冲突（删掉一条并行更正，保留 K-2，截止时间晚于旧基准）
    conn.execute("DELETE FROM announcements WHERE notice_no = 'K-3'")
    conn.commit()
    chains.recompute(conn)
    found = changes.track_changes(conn, list_url, "run-3")
    conn.commit()
    assert len(found) == 1
    assert found[0]["change_type"] == "postponed"
    assert found[0]["old_deadline"] == "2026-11-20 09:30:00"
    assert found[0]["new_deadline"] == "2026-12-01 09:30:00"
    baseline = conn.execute(
        "SELECT * FROM chain_baselines WHERE chain_id = 'K-1'"
    ).fetchone()
    assert baseline["needs_review"] == 0
    assert baseline["chain_status"] == "ok"
    assert baseline["confirmed_deadline"] == "2026-12-01 09:30:00"


def test_new_conflict_chain_creates_pending_baseline_without_change(
    tmp_path, monkeypatch
):
    conn = _open_conn(tmp_path, monkeypatch)
    list_url = "http://x/list"
    # 自环更正：冲突孤立，不构成链；改用并行更正构造冲突链
    _insert(conn, "P-1", chains.FIRST, deadline="2026-11-20 09:30:00")
    _insert(conn, "P-2", chains.CORRECTION, ref="P-1",
            deadline="2026-12-01 09:30:00")
    _insert(conn, "P-3", chains.CORRECTION, ref="P-1",
            deadline="2026-12-02 09:30:00")
    chains.recompute(conn)
    found = changes.track_changes(conn, list_url, "run-1")
    conn.commit()
    assert found == []
    baseline = conn.execute(
        "SELECT * FROM chain_baselines WHERE chain_id = 'P-1'"
    ).fetchone()
    assert baseline["needs_review"] == 1
    assert baseline["chain_status"] is None
    assert baseline["confirmed_deadline"] is None

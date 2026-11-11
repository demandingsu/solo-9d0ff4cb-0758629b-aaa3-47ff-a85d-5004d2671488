"""端到端：同步接口、链查询、冲突、撤销与失败保留。"""
from __future__ import annotations


def _chains_by_id(payload: dict) -> dict:
    return {c["chain_id"]: c for c in payload["chains"]}


def _orphans_by_no(payload: dict) -> dict:
    return {o["notice_no"]: o for o in payload["orphans"]}


def test_full_sync_and_counts(client, site_base_url):
    c, _ = client
    resp = c.post("/api/v1/sync", json={"list_url": f"{site_base_url}/announcements"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["total_listed"] == 17
    assert body["fetched_ok"] == 17
    assert body["failed_count"] == 0

    result = c.get("/api/v1/chains").json()
    # 6 条链：A、B、C、G-001、G-101、H
    assert len(result["chains"]) == 6
    # 5 条孤立异常：D-001、E-001、E-002、F-001
    assert len(result["orphans"]) == 4


def test_normal_chain_status_and_effective_deadline(client, site_base_url):
    c, _ = client
    c.post("/api/v1/sync", json={"list_url": f"{site_base_url}/announcements"})
    chain = _chains_by_id(c.get("/api/v1/chains").json())["A-001"]

    assert chain["status"] == "ok"
    # A-002 未给出截止时间 -> 沿用链上此前的截止时间
    # A-003 给出更晚的截止时间 -> 有效截止时间取最新
    assert chain["effective_deadline"] == "2026-11-25 15:00:00"
    assert [a["notice_no"] for a in chain["announcements"]] == [
        "A-001", "A-002", "A-003"
    ]
    # 链上公告都带有来源链接
    assert all(a["source_url"].endswith(f"/notice/{a['notice_no']}")
               for a in chain["announcements"])


def test_revocation_invalidates_chain(client, site_base_url):
    c, _ = client
    c.post("/api/v1/sync", json={"list_url": f"{site_base_url}/announcements"})
    chain = _chains_by_id(c.get("/api/v1/chains").json())["B-001"]

    assert chain["status"] == "cancelled"
    assert chain["effective_deadline"] is None
    assert [a["notice_no"] for a in chain["announcements"]] == [
        "B-001", "B-002", "B-003"
    ]


def test_parallel_corrections_conflict_without_guessing_deadline(
    client, site_base_url
):
    c, _ = client
    c.post("/api/v1/sync", json={"list_url": f"{site_base_url}/announcements"})
    chain = _chains_by_id(c.get("/api/v1/chains").json())["C-001"]

    assert chain["status"] == "conflict"
    # 冲突时不推测截止时间
    assert chain["effective_deadline"] is None
    for ann in chain["announcements"]:
        assert "parallel_correction" in ann["conflict_reasons"]
        assert ann["effective_deadline"] is None


def test_same_project_first_notices_remain_independent(client, site_base_url):
    c, _ = client
    c.post("/api/v1/sync", json={"list_url": f"{site_base_url}/announcements"})
    chains = _chains_by_id(c.get("/api/v1/chains").json())

    g1 = chains["G-001"]
    g2 = chains["G-101"]
    assert g1["project_no"] == g2["project_no"] == "PRJ-G"
    assert [a["notice_no"] for a in g1["announcements"]] == ["G-001"]
    assert [a["notice_no"] for a in g2["announcements"]] == ["G-101", "G-102"]
    assert g1["status"] == g2["status"] == "ok"
    assert g2["effective_deadline"] == "2026-11-28 09:30:00"


def test_orphans_missing_reference_and_cycle(client, site_base_url):
    c, _ = client
    c.post("/api/v1/sync", json={"list_url": f"{site_base_url}/announcements"})
    orphans = _orphans_by_no(c.get("/api/v1/chains").json())

    assert "missing_reference" in orphans["D-001"]["conflict_reasons"]
    assert "missing_reference" in orphans["F-001"]["conflict_reasons"]

    for no in ("E-001", "E-002"):
        assert "reference_cycle" in orphans[no]["conflict_reasons"]
        assert orphans[no]["effective_deadline"] is None
        assert orphans[no]["status"] == "orphan"


def test_detail_failure_keeps_last_complete_result(
    client, site_base_url, set_fail
):
    c, _ = client
    list_url = f"{site_base_url}/announcements"

    # 第一次同步：全部成功
    first = c.post("/api/v1/sync", json={"list_url": list_url}).json()
    assert first["status"] == "success"
    before = c.get("/api/v1/announcements/H-001").json()
    assert before["deadline"] == "2026-11-30 09:30:00"
    assert before["last_fetch_error"] is None

    # 打开详情页故障后再次同步
    set_fail(True)
    second = c.post("/api/v1/sync", json={"list_url": list_url}).json()
    assert second["status"] == "partial"
    assert second["failed_count"] == 1
    assert second["kept_last"] == 1

    after = c.get("/api/v1/announcements/H-001").json()
    # 上次完整结果保留
    assert after["deadline"] == "2026-11-30 09:30:00"
    assert after["notice_type"] == "first"
    assert "500" in after["last_fetch_error"]
    # 链 H 仍可正常计算
    chains = _chains_by_id(c.get("/api/v1/chains").json())
    assert chains["H-001"]["status"] == "ok"

    # 故障恢复后同步，错误信息清空
    set_fail(False)
    third = c.post("/api/v1/sync", json={"list_url": list_url}).json()
    assert third["status"] == "success"
    healed = c.get("/api/v1/announcements/H-001").json()
    assert healed["last_fetch_error"] is None


def test_sync_runs_recorded_and_health(client, site_base_url):
    c, _ = client
    assert c.get("/health").json() == {"status": "ok"}

    c.post("/api/v1/sync", json={"list_url": f"{site_base_url}/announcements"})
    runs = c.get("/api/v1/sync-runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["total_listed"] == 17


def test_sync_bad_url_rejected(client):
    c, _ = client
    resp = c.post("/api/v1/sync", json={"list_url": "not-a-url"})
    assert resp.status_code == 400


def test_sync_list_page_unreachable(client):
    c, _ = client
    # 监听一个保证无人监听的端口
    resp = c.post(
        "/api/v1/sync",
        json={"list_url": "http://127.0.0.1:1/announcements"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["total_listed"] == 0

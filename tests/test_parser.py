"""解析器单元测试。"""
from __future__ import annotations

from app.parser import (
    normalize_datetime,
    normalize_type,
    parse_detail,
    parse_list,
)


def test_normalize_type():
    assert normalize_type("更正公告") == "correction"
    assert normalize_type("首次公告") == "first"
    assert normalize_type("撤销公告") == "revocation"
    assert normalize_type("未知类型") is None
    assert normalize_type(None) is None


def test_normalize_datetime_formats():
    assert normalize_datetime("2026-11-20 09:30:00") == "2026-11-20 09:30:00"
    assert normalize_datetime("2026/11/20 9:30") == "2026-11-20 09:30:00"
    assert normalize_datetime("2026年11月20日") == "2026-11-20 00:00:00"
    assert normalize_datetime("  ") is None
    # 无法识别的格式原样保留
    assert normalize_datetime("待定") == "待定"


def test_parse_list_extracts_notices_and_absolute_urls():
    html = """
    <table class="announcement-list">
      <tr><td><a href="/notice/A-001">A-001 公告</a></td></tr>
      <tr><td><a href="/notice/B-002">B-002 公告</a></td></tr>
      <tr><td><a href="/notice/A-001">A-001 重复入口</a></td></tr>
    </table>
    """
    items = parse_list(html, "http://example.com/announcements")
    assert [i.notice_no for i in items] == ["A-001", "B-002"]
    assert items[0].detail_url == "http://example.com/notice/A-001"


def test_parse_detail_maps_all_fields():
    html = """
    <table class="detail-table">
      <tr><th>公告编号</th><td>A-001</td></tr>
      <tr><th>项目编号</th><td>PRJ-A</td></tr>
      <tr><th>公告类型</th><td>首次公告</td></tr>
      <tr><th>发布时间</th><td>2026-10-20 09:00:00</td></tr>
      <tr><th>投标截止时间</th><td>2026/11/20 09:30</td></tr>
      <tr><th>引用编号</th><td></td></tr>
    </table>
    """
    detail = parse_detail(html, "http://example.com/notice/A-001")
    assert detail.notice_no == "A-001"
    assert detail.project_no == "PRJ-A"
    assert detail.notice_type == "first"
    assert detail.publish_time == "2026-10-20 09:00:00"
    assert detail.deadline == "2026-11-20 09:30:00"
    assert detail.ref_notice_no is None
    assert detail.source_url == "http://example.com/notice/A-001"

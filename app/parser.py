"""公共采购公告页面解析（BeautifulSoup）。

解析规则与示例公告站（sample_site/）的页面结构对应，同时对字段标签做
别名兼容，便于适配标签措辞略有差异的真实站点。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# 公告类型关键词 -> 规范类型
TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("撤销", "revocation"),
    ("更正", "correction"),
    ("首次", "first"),
]

# 详情页字段标签别名（标签文本去掉冒号/空白后匹配）
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "notice_no": ("公告编号",),
    "project_no": ("项目编号",),
    "notice_type": ("公告类型", "类型"),
    "publish_time": ("发布时间",),
    "deadline": ("投标截止时间", "截止时间"),
    "ref_notice_no": ("引用编号",),
}


@dataclass
class ListedNotice:
    """列表页上的一条公告入口。"""
    notice_no: str
    detail_url: str


@dataclass
class NoticeDetail:
    """详情页解析结果。"""
    notice_no: str
    project_no: Optional[str] = None
    notice_type: Optional[str] = None
    publish_time: Optional[str] = None
    deadline: Optional[str] = None
    ref_notice_no: Optional[str] = None
    source_url: Optional[str] = None


# 站点对空字段常用的占位符，解析时一律视为空值
EMPTY_MARKERS = {"—", "–", "-", "/", "无", "空", "暂无"}


def _clean(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    value = text.replace("\xa0", " ").strip()
    if not value or value in EMPTY_MARKERS:
        return None
    return value


def normalize_type(raw: Optional[str]) -> Optional[str]:
    """将中文类型标签映射为 first / correction / revocation。"""
    if not raw:
        return None
    for keyword, canonical in TYPE_KEYWORDS:
        if keyword in raw:
            return canonical
    return None


_DT_PATTERNS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M",
    "%Y年%m月%d日",
)


def normalize_datetime(raw: Optional[str]) -> Optional[str]:
    """把多种常见时间写法统一为 `YYYY-MM-DD HH:MM:SS`。"""
    import datetime as _dt

    value = _clean(raw)
    if not value:
        return None
    for pattern in _DT_PATTERNS:
        try:
            parsed = _dt.datetime.strptime(value, pattern)
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    # 无法识别时原样保留，避免静默丢数据
    return value


def parse_list(html: str, list_url: str) -> list[ListedNotice]:
    """解析列表页，返回公告编号与详情页绝对 URL。"""
    soup = BeautifulSoup(html, "html.parser")
    results: list[ListedNotice] = []
    seen: set[str] = set()
    for link in soup.select("table.announcement-list a, ul.announcement-list a"):
        href = link.get("href")
        label = _clean(link.get_text())
        if not href or not label:
            continue
        match = re.search(r"([A-Za-z0-9\-_]+)", label)
        if not match:
            continue
        notice_no = match.group(1)
        if notice_no in seen:
            continue
        seen.add(notice_no)
        results.append(
            ListedNotice(notice_no=notice_no, detail_url=urljoin(list_url, href))
        )
    return results


def _field_map(soup: BeautifulSoup) -> dict[str, str]:
    """从详情页表格中抽取字段，兼容 th/td 与 dt/dd 两种结构。"""
    fields: dict[str, str] = {}

    for tr in soup.select("table.detail-table tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2:
            label = _clean(cells[0].get_text()) or ""
            value = _clean(cells[1].get_text())
            if label and value is not None:
                fields[label.rstrip("：:").strip()] = value

    for dt in soup.select("dl.detail-list dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            label = _clean(dt.get_text()) or ""
            fields[label.rstrip("：:").strip()] = _clean(dd.get_text()) or ""

    return fields


def _pick(fields: dict[str, str], key: str) -> Optional[str]:
    for alias in FIELD_ALIASES[key]:
        if alias in fields:
            return _clean(fields[alias])
    return None


def parse_detail(html: str, source_url: str) -> NoticeDetail:
    """解析公告详情页。"""
    soup = BeautifulSoup(html, "html.parser")
    fields = _field_map(soup)

    detail = NoticeDetail(
        notice_no=_pick(fields, "notice_no") or "",
        project_no=_pick(fields, "project_no"),
        notice_type=normalize_type(_pick(fields, "notice_type")),
        publish_time=normalize_datetime(_pick(fields, "publish_time")),
        deadline=normalize_datetime(_pick(fields, "deadline")),
        ref_notice_no=_pick(fields, "ref_notice_no"),
        source_url=source_url,
    )
    if not detail.notice_no:
        raise ValueError("详情页缺少公告编号字段")
    return detail

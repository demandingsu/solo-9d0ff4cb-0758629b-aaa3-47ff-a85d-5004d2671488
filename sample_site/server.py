"""示例公共采购公告站（仅依赖 Python 标准库）。

用于在本地/容器内演示采集 API，覆盖以下场景：
  链 A：首次 + 更正（含截止时间继承）
  链 B：首次 + 更正 + 撤销（撤销使链失效）
  链 C：并行更正（同一公告有两个后继 -> 冲突）
  链 D：引用编号不存在（missing_reference -> 冲突 + 孤立）
  链 E：引用成环（reference_cycle -> 孤立）
  链 F：更正无引用编号（missing_reference -> 孤立）
  链 G：同一项目两个独立首次公告（保持两条独立链）
  公告 H：可通过 /fail-toggle 控制详情页返回 500，验证失败保留上次结果

控制接口：
  GET /fail-toggle?enabled=1|0   开关 H-001 详情页的 500 故障
  GET /fail-status               查询当前故障开关
"""
from __future__ import annotations

import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 每条公告：编号、项目编号、类型、发布时间、截止时间、引用编号
NOTICES: list[dict] = [
    # 链 A：正常链，COR-A-02 不带截止时间（应继承首次的截止时间）
    {"no": "A-001", "project": "PRJ-A", "type": "首次公告",
     "publish": "2026-10-20 09:00:00", "deadline": "2026-11-20 09:30:00",
     "ref": ""},
    {"no": "A-002", "project": "PRJ-A", "type": "更正公告",
     "publish": "2026-10-25 14:00:00", "deadline": "",
     "ref": "A-001"},
    {"no": "A-003", "project": "PRJ-A", "type": "更正公告",
     "publish": "2026-11-01 10:00:00", "deadline": "2026-11-25 15:00:00",
     "ref": "A-002"},

    # 链 B：撤销使整条链失效
    {"no": "B-001", "project": "PRJ-B", "type": "首次公告",
     "publish": "2026-10-21 09:00:00", "deadline": "2026-11-21 09:30:00",
     "ref": ""},
    {"no": "B-002", "project": "PRJ-B", "type": "更正公告",
     "publish": "2026-10-26 14:00:00", "deadline": "2026-11-22 09:30:00",
     "ref": "B-001"},
    {"no": "B-003", "project": "PRJ-B", "type": "撤销公告",
     "publish": "2026-10-28 16:00:00", "deadline": "",
     "ref": "B-002"},

    # 链 C：并行更正（C-002 与 C-003 同时引用 C-001）
    {"no": "C-001", "project": "PRJ-C", "type": "首次公告",
     "publish": "2026-10-22 09:00:00", "deadline": "2026-11-22 09:30:00",
     "ref": ""},
    {"no": "C-002", "project": "PRJ-C", "type": "更正公告",
     "publish": "2026-10-27 10:00:00", "deadline": "2026-11-23 09:30:00",
     "ref": "C-001"},
    {"no": "C-003", "project": "PRJ-C", "type": "更正公告",
     "publish": "2026-10-27 15:00:00", "deadline": "2026-11-24 09:30:00",
     "ref": "C-001"},

    # 链 D：引用了不存在的公告 X-999
    {"no": "D-001", "project": "PRJ-D", "type": "更正公告",
     "publish": "2026-10-23 09:00:00", "deadline": "2026-11-23 09:30:00",
     "ref": "X-999"},

    # 链 E：成环 E-001 -> E-002 -> E-001
    {"no": "E-001", "project": "PRJ-E", "type": "更正公告",
     "publish": "2026-10-24 09:00:00", "deadline": "2026-11-24 09:30:00",
     "ref": "E-002"},
    {"no": "E-002", "project": "PRJ-E", "type": "更正公告",
     "publish": "2026-10-24 10:00:00", "deadline": "2026-11-25 09:30:00",
     "ref": "E-001"},

    # 链 F：更正公告没有引用编号
    {"no": "F-001", "project": "PRJ-F", "type": "更正公告",
     "publish": "2026-10-25 09:00:00", "deadline": "2026-11-26 09:30:00",
     "ref": ""},

    # 链 G：同一项目两个独立首次公告
    {"no": "G-001", "project": "PRJ-G", "type": "首次公告",
     "publish": "2026-10-26 09:00:00", "deadline": "2026-11-26 09:30:00",
     "ref": ""},
    {"no": "G-101", "project": "PRJ-G", "type": "首次公告",
     "publish": "2026-10-27 09:00:00", "deadline": "2026-11-27 09:30:00",
     "ref": ""},
    {"no": "G-102", "project": "PRJ-G", "type": "更正公告",
     "publish": "2026-10-29 09:00:00", "deadline": "2026-11-28 09:30:00",
     "ref": "G-101"},

    # H：详情页故障开关目标（默认正常）
    {"no": "H-001", "project": "PRJ-H", "type": "首次公告",
     "publish": "2026-10-30 09:00:00", "deadline": "2026-11-30 09:30:00",
     "ref": ""},
]

NOTICE_BY_NO = {n["no"]: n for n in NOTICES}

# 运行期故障开关（内存态，重启容器即恢复）
FAIL_ENABLED = {"H-001": False}

PAGE_CSS = """
body { font-family: sans-serif; margin: 2em; color: #222; }
h1 { font-size: 1.4em; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #999; padding: 6px 10px; text-align: left; }
th { background: #f0f3f7; }
a { color: #1a56c4; }
.meta { color: #666; margin-bottom: 1em; }
"""


def render_list() -> str:
    rows = "\n".join(
        '<tr><td><a href="/notice/{no}">{no}</a></td>'
        '<td>{project}</td><td>{type}</td><td>{publish}</td></tr>'.format(
            no=escape(n["no"]), project=escape(n["project"]),
            type=escape(n["type"]), publish=escape(n["publish"]),
        )
        for n in NOTICES
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>公共采购公告列表</title><style>{PAGE_CSS}</style></head>
<body>
<h1>公共采购公告列表（示例站）</h1>
<p class="meta">共 {len(NOTICES)} 条公告 · 仅用于演示采集 API</p>
<table class="announcement-list">
<thead><tr><th>公告编号</th><th>项目编号</th><th>类型</th><th>发布时间</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""


def _row(label: str, value: str) -> str:
    return (
        f"<tr><th>{escape(label)}</th>"
        f"<td>{escape(value) if value else '—'}</td></tr>"
    )


def render_detail(no: str) -> str:
    n = NOTICE_BY_NO[no]
    rows = "".join([
        _row("公告编号", n["no"]),
        _row("项目编号", n["project"]),
        _row("公告类型", n["type"]),
        _row("发布时间", n["publish"]),
        _row("投标截止时间", n["deadline"]),
        _row("引用编号", n["ref"]),
    ])
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>公告 {escape(n['no'])}</title><style>{PAGE_CSS}</style></head>
<body>
<h1>公告详情 {escape(n['no'])}</h1>
<p><a href="/announcements">← 返回列表</a></p>
<table class="detail-table"><tbody>{rows}</tbody></table>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, status: int = 200,
              content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path in ("/", "/announcements"):
            self._send(render_list())
            return

        if path == "/fail-toggle":
            enabled = (query.get("enabled", ["0"])[0] in {"1", "true", "yes"})
            FAIL_ENABLED["H-001"] = enabled
            self._send(
                f'{{"H-001_fail_enabled": {str(enabled).lower()}}}',
                content_type="application/json; charset=utf-8",
            )
            return

        if path == "/fail-status":
            enabled = FAIL_ENABLED["H-001"]
            self._send(
                f'{{"H-001_fail_enabled": {str(enabled).lower()}}}',
                content_type="application/json; charset=utf-8",
            )
            return

        if path.startswith("/notice/"):
            no = path.rsplit("/", 1)[-1]
            notice = NOTICE_BY_NO.get(no)
            if notice is None:
                self._send("<h1>404 公告不存在</h1>", status=404)
                return
            if FAIL_ENABLED.get(no):
                self._send("<h1>500 服务器内部错误（模拟故障）</h1>", status=500)
                return
            self._send(render_detail(no))
            return

        self._send("<h1>404</h1>", status=404)

    def log_message(self, fmt: str, *args) -> None:  # 精简日志
        print(f"[sample-site] {self.address_string()} {fmt % args}")


def main() -> None:
    port = int(os.getenv("SAMPLE_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[sample-site] 示例公告站监听 0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

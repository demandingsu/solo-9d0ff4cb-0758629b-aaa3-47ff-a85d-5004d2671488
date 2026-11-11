"""测试夹具：临时 SQLite + 后台线程运行的示例公告站 + FastAPI TestClient。"""
from __future__ import annotations

import importlib
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# 隔离环境（无 pip 常规安装路径）下定位依赖目录
PYLIBS = ROOT / ".pylibs" / "lib" / "python3.11" / "site-packages"
if PYLIBS.exists() and str(PYLIBS) not in sys.path:
    sys.path.insert(0, str(PYLIBS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from http.server import ThreadingHTTPServer  # noqa: E402

from sample_site import server as sample_server  # noqa: E402


@pytest.fixture(scope="session")
def site_base_url() -> str:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), sample_server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    # 每个测试会话从正常状态开始
    sample_server.FAIL_ENABLED["H-001"] = False
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试使用独立数据库，避免相互污染。"""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    import app.config as config
    import app.db as db
    import app.main as main

    importlib.reload(config)
    importlib.reload(db)
    importlib.reload(main)

    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c, sample_server


def _fail_toggle(base_url: str, enabled: bool) -> None:
    conn = HTTPConnection("127.0.0.1", base_url.rsplit(":", 1)[1])
    conn.request("GET", f"/fail-toggle?enabled={'1' if enabled else '0'}")
    resp = conn.getresponse()
    resp.read()
    conn.close()


@pytest.fixture()
def set_fail(site_base_url):
    def _set(enabled: bool) -> None:
        _fail_toggle(site_base_url, enabled)

    return _set

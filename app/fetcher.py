"""HTTP 抓取层：带超时与有限重试。"""
import logging
import time

import httpx

from . import config

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """页面抓取失败（网络错误或非 2xx 响应）。"""


def fetch(url: str) -> str:
    """抓取页面文本。对网络错误和 5xx 做有限次重试，4xx 立即失败。"""
    last_error: Exception | None = None
    for attempt in range(config.FETCH_RETRIES + 1):
        try:
            resp = httpx.get(url, timeout=config.FETCH_TIMEOUT, follow_redirects=True)
            if resp.status_code >= 500:
                raise FetchError(f"服务端错误 HTTP {resp.status_code}")
            if resp.status_code != 200:
                # 404 等客户端错误不重试
                raise FetchError(f"HTTP {resp.status_code}")
            resp.encoding = resp.charset_encoding or "utf-8"
            return resp.text
        except (httpx.HTTPError, FetchError) as exc:
            last_error = exc
            retryable = isinstance(exc, httpx.HTTPError) or "5" in str(exc)
            if not retryable or attempt == config.FETCH_RETRIES:
                break
            sleep_for = config.RETRY_BACKOFF * (attempt + 1)
            logger.warning("抓取 %s 第 %d 次失败：%s，%.1fs 后重试",
                           url, attempt + 1, exc, sleep_for)
            time.sleep(sleep_for)

    raise FetchError(f"抓取 {url} 失败：{last_error}")

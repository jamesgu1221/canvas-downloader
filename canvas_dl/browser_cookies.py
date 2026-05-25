from __future__ import annotations

from urllib.parse import urlparse

import requests


class BrowserCookieError(RuntimeError):
    pass


def load_browser_cookies(session: requests.Session, urls: list[str]) -> int:
    """Load cookies for the given URLs from local browsers into session.

    This uses the optional browser-cookie3 package. Cookies are kept in memory
    only; callers must avoid logging their values.
    """
    try:
        import browser_cookie3
    except ImportError as e:
        raise BrowserCookieError(
            "需要安装 browser-cookie3 才能读取本机浏览器 Cookie：pip install browser-cookie3"
        ) from e

    domains = _domains_for_urls(urls)
    loaded = 0
    errors: list[str] = []
    for domain in domains:
        try:
            jar = browser_cookie3.load(domain_name=domain)
        except Exception as e:  # noqa: BLE001 - browser stores vary by OS/browser
            errors.append(f"{domain}: {e}")
            continue
        for cookie in jar:
            session.cookies.set_cookie(cookie)
            loaded += 1
    if loaded == 0:
        detail = "; ".join(errors[-3:]) if errors else "未找到匹配域名的 Cookie"
        raise BrowserCookieError(f"未能从浏览器读取可用 Cookie：{detail}")
    return loaded


def _domains_for_urls(urls: list[str]) -> list[str]:
    domains: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        if parsed.hostname:
            domains.append(parsed.hostname)
    domains.extend(["oc.sjtu.edu.cn", "jaccount.sjtu.edu.cn", "v.sjtu.edu.cn"])

    result: list[str] = []
    seen: set[str] = set()
    for domain in domains:
        if domain not in seen:
            seen.add(domain)
            result.append(domain)
    return result

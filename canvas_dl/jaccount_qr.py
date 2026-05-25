from __future__ import annotations

import json
import re
import base64
import contextlib
import hashlib
import os
import socket
import ssl
import struct
import threading
from dataclasses import dataclass
from html import unescape
from urllib.parse import unquote, urljoin, urlparse

import requests


class JAccountQrError(RuntimeError):
    pass


@dataclass
class QrLoginState:
    uuid: str
    login_url: str


def start_qr_login(session: requests.Session, url: str) -> QrLoginState:
    resp = session.get(url, headers={"accept-language": "zh-CN"}, timeout=30)
    resp.raise_for_status()
    resp = _ensure_jaccount_login_page(session, resp)
    uuid = _extract_uuid(resp.text)
    if not uuid:
        raise JAccountQrError(f"登录页中未找到二维码 uuid，当前 URL：{resp.url}")
    return QrLoginState(uuid=uuid, login_url=resp.url)


def fetch_qr_code(session: requests.Session, uuid: str, ts: str, sig: str) -> bytes:
    resp = session.get(
        "https://jaccount.sjtu.edu.cn/jaccount/qrcode",
        params={"uuid": uuid, "ts": ts, "sig": sig},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def finish_qr_login(session: requests.Session, uuid: str) -> None:
    resp = session.get(
        "https://jaccount.sjtu.edu.cn/jaccount/expresslogin",
        params={"uuid": uuid},
        headers={"accept-language": "zh-CN"},
        timeout=30,
    )
    resp.raise_for_status()
    if resp.url.startswith("https://jaccount.sjtu.edu.cn/jaccount/expresslogin"):
        raise JAccountQrError("二维码尚未完成确认或已过期。")


def resolve_video_url(session: requests.Session, external_tool_url: str) -> str:
    resp = _get_following_login(session, external_tool_url)
    token_url = _token_url_from_response(session, resp)
    if token_url:
        return token_url
    if "v.sjtu.edu.cn" in resp.url and "tokenId=" in resp.url:
        return resp.url
    location = resp.headers.get("Location", "")
    if "v.sjtu.edu.cn" in location:
        return location
    match = re.search(r"https://v\.sjtu\.edu\.cn/[^\s\"'<>]+", resp.text or "")
    if match:
        return unquote(match.group(0))
    raise JAccountQrError(f"登录成功，但未跳转到视频页，当前 URL：{resp.url}")


def scan_canvas_video_courses(session: requests.Session, courses: list[dict], cancel_token=None) -> list[dict]:
    result: list[dict] = []
    for course in courses:
        if cancel_token is not None and cancel_token.is_cancelled():
            break
        course_id = course.get("id")
        if course_id is None:
            continue
        page = session.get(f"https://oc.sjtu.edu.cn/courses/{course_id}", timeout=30)
        if cancel_token is not None and cancel_token.is_cancelled():
            break
        if page.status_code >= 400:
            continue
        tool_url = _extract_canvas_video_tool_url(page.text, page.url)
        if not tool_url:
            continue
        result.append(
            {
                "id": int(course_id),
                "name": course.get("name") or f"course_{course_id}",
                "tool_url": tool_url,
            }
        )
    return result


def _get_following_login(session: requests.Session, url: str) -> requests.Response:
    resp = session.get(url, allow_redirects=True, timeout=30)
    resp.raise_for_status()
    if "oc.sjtu.edu.cn/login" not in resp.url:
        return resp
    login_link = _extract_jaccount_link(resp.text, resp.url)
    if login_link:
        login_resp = session.get(
            login_link,
            headers={"accept-language": "zh-CN", "Referer": resp.url},
            allow_redirects=True,
            timeout=30,
        )
        login_resp.raise_for_status()
        if "v.sjtu.edu.cn" in login_resp.url:
            return login_resp
    second = session.get(url, allow_redirects=True, timeout=30)
    second.raise_for_status()
    return second


def _token_url_from_response(session: requests.Session, resp: requests.Response) -> str:
    for candidate in (resp.url, resp.headers.get("Location", "")):
        if "v.sjtu.edu.cn" in candidate and "tokenId=" in candidate:
            return candidate
    text = resp.text or ""
    direct = re.search(r"https://v\.sjtu\.edu\.cn/[^\s\"'<>]+tokenId=[^\s\"'<>]+", text)
    if direct:
        return unquote(direct.group(0))
    form = _extract_form(text, resp.url, "lti3/lti3Auth/ivs")
    if form is None:
        form = _extract_form(text, resp.url, "oidc/login_initiations")
    if form is None:
        return ""
    action, data = form
    next_resp = session.post(action, data=data, allow_redirects=False, timeout=30)
    next_resp.raise_for_status()
    location = next_resp.headers.get("Location", "")
    if location:
        absolute = urljoin(action, location)
        if "tokenId=" in absolute:
            return absolute
        follow = session.get(absolute, allow_redirects=True, timeout=30)
        follow.raise_for_status()
        return _token_url_from_response(session, follow)
    if next_resp.text:
        return _token_url_from_response(session, next_resp)
    return ""


def _extract_form(text: str, base_url: str, action_contains: str) -> tuple[str, dict] | None:
    decoded = unescape(text).replace("\\/", "/")
    form_pattern = re.compile(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", re.IGNORECASE | re.DOTALL)
    for match in form_pattern.finditer(decoded):
        attrs = match.group("attrs")
        action_match = re.search(r'action=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if not action_match:
            continue
        action = urljoin(base_url, unquote(action_match.group(1)))
        if action_contains not in action:
            continue
        body = match.group("body")
        data: dict[str, str] = {}
        input_pattern = re.compile(r"<input\b([^>]*)>", re.IGNORECASE)
        for input_match in input_pattern.finditer(body):
            input_attrs = input_match.group(1)
            name_match = re.search(r'name=["\']([^"\']+)["\']', input_attrs, re.IGNORECASE)
            if not name_match:
                continue
            value_match = re.search(r'value=["\']([^"\']*)["\']', input_attrs, re.IGNORECASE)
            data[unescape(name_match.group(1))] = unescape(value_match.group(1)) if value_match else ""
        return action, data
    return None


def _extract_canvas_video_tool_url(text: str, base_url: str) -> str:
    decoded = unescape(text).replace("\\/", "/")
    anchor_pattern = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
    fallback = ""
    for match in anchor_pattern.finditer(decoded):
        attrs = match.group("attrs")
        href_match = re.search(r'href=["\']([^"\']*external_tools/\d+[^"\']*)["\']', attrs, re.IGNORECASE)
        if not href_match:
            continue
        href = urljoin(base_url, unquote(href_match.group(1)))
        body = re.sub(r"<[^>]+>", "", match.group("body"))
        title = attrs + " " + body
        if not fallback:
            fallback = href
        if "课堂视频" in title and "旧版" not in title:
            return href
    return fallback


def cookie_dicts(session: requests.Session) -> list[dict]:
    result: list[dict] = []
    for cookie in session.cookies:
        result.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
            }
        )
    return result


class QrLoginMonitor:
    def __init__(self, uuid: str, session: requests.Session, on_qr, on_login, on_error) -> None:
        self.uuid = uuid
        self.session = session
        self.on_qr = on_qr
        self.on_login = on_login
        self.on_error = on_error
        self._wss = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        cookie = "; ".join(
            f"{cookie.name}={cookie.value}"
            for cookie in self.session.cookies
            if "jaccount.sjtu.edu.cn" in (cookie.domain or "")
        )
        self._wss = _SimpleWebSocket.connect(
            f"wss://jaccount.sjtu.edu.cn/jaccount/sub/{self.uuid}",
            headers={"Cookie": cookie},
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.refresh()

    def refresh(self) -> None:
        if self._wss is not None:
            self._wss.send('{ "type": "UPDATE_QR_CODE" }')

    def close(self) -> None:
        if self._wss is not None:
            try:
                self._wss.close()
            except Exception:
                pass
            self._wss = None

    def _run(self) -> None:
        while self._wss is not None:
            try:
                message = self._wss.recv()
            except Exception as e:  # noqa: BLE001
                self.on_error(str(e))
                break
            if not message:
                break
            try:
                payload = json.loads(message)
            except ValueError:
                continue
            kind = payload.get("type")
            if kind == "UPDATE_QR_CODE":
                data = payload.get("payload") or {}
                self.on_qr(str(data.get("ts") or ""), str(data.get("sig") or ""))
            elif kind == "LOGIN":
                self.on_login()
                break


def _extract_uuid(text: str) -> str:
    match = re.search(r'id=["\']firefox_link["\'][^>]+href=["\'][^"\']*uuid=([^"\'&]+)', text)
    if match:
        return unquote(match.group(1))
    match = re.search(r"uuid=([0-9A-Za-z_-]+)", text)
    return unquote(match.group(1)) if match else ""


def _ensure_jaccount_login_page(session: requests.Session, resp: requests.Response) -> requests.Response:
    if "jaccount.sjtu.edu.cn/jaccount/jalogin" in resp.url:
        return resp
    link = _extract_jaccount_link(resp.text, resp.url)
    if not link:
        return resp
    next_resp = session.get(
        link,
        headers={"accept-language": "zh-CN", "Referer": resp.url},
        allow_redirects=True,
        timeout=30,
    )
    next_resp.raise_for_status()
    return next_resp


def _extract_jaccount_link(text: str, base_url: str) -> str:
    if not text:
        return ""
    decoded = unescape(text).replace("\\/", "/")
    patterns = (
        r'href=["\']([^"\']*jaccount\.sjtu\.edu\.cn/jaccount/jalogin[^"\']*)["\']',
        r'href=["\']([^"\']*/login/openid_connect[^"\']*)["\']',
        r'action=["\']([^"\']*jaccount\.sjtu\.edu\.cn/jaccount/jalogin[^"\']*)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, decoded, re.IGNORECASE)
        if match:
            return urljoin(base_url, unquote(match.group(1)))

    # Canvas login pages may put the URL next to the "校内用户登录" button in data attrs.
    candidates = re.findall(r'["\']([^"\']*(?:jaccount/jalogin|login/openid_connect)[^"\']*)["\']', decoded)
    for candidate in candidates:
        if "jaccount" in candidate or "openid_connect" in candidate:
            return urljoin(base_url, unquote(candidate))
    return ""


class _SimpleWebSocket:
    def __init__(self, sock: ssl.SSLSocket) -> None:
        self._sock = sock

    @classmethod
    def connect(cls, url: str, headers: dict[str, str] | None = None) -> "_SimpleWebSocket":
        parsed = urlparse(url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise JAccountQrError(f"不支持的 WebSocket URL：{url}")
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        raw = socket.create_connection((parsed.hostname, port), timeout=30)
        raw.settimeout(30)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
        sock.settimeout(30)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request_headers = {
            "Host": parsed.hostname,
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": key,
            "Sec-WebSocket-Version": "13",
            "User-Agent": "CanvasDownloader",
            **(headers or {}),
        }
        request = [f"GET {path} HTTP/1.1", *(f"{k}: {v}" for k, v in request_headers.items()), "", ""]
        sock.sendall("\r\n".join(request).encode("ascii", errors="replace"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if len(response) > 65536:
                break
        header = response.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1", errors="replace")
        if " 101 " not in header.split("\r\n", 1)[0]:
            with contextlib.suppress(OSError):
                sock.close()
            raise JAccountQrError("连接 jAccount 二维码状态通道失败。")

        accept = ""
        for line in header.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.lower() == "sec-websocket-accept":
                accept = value.strip()
                break
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if accept != expected:
            with contextlib.suppress(OSError):
                sock.close()
            raise JAccountQrError("jAccount WebSocket 握手校验失败。")
        return cls(sock)

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self) -> str:
        while True:
            first = self._read_exact(2)
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x1:
                return payload.decode("utf-8", errors="replace")
            if opcode == 0x8:
                return ""
            if opcode == 0x9:
                self._send_frame(0xA, payload)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._send_frame(0x8, b"")
        with contextlib.suppress(OSError):
            self._sock.close()

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126])
            header.extend(struct.pack("!H", length))
        else:
            header.extend([0x80 | 127])
            header.extend(struct.pack("!Q", length))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + mask + masked)

    def _read_exact(self, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            chunk = self._sock.recv(length - len(data))
            if not chunk:
                raise JAccountQrError("jAccount WebSocket 连接已关闭。")
            data.extend(chunk)
        return bytes(data)

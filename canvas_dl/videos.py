from __future__ import annotations

import datetime
import hashlib
import html
import contextlib
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
import requests.exceptions
from canvasapi.exceptions import ResourceDoesNotExist

from .client import CanvasClient, safe_course_name
from .config import AppConfig, parse_course_ids
from .browser_cookies import BrowserCookieError, load_browser_cookies
from .cookie_cache import load_cookie_cache
from .events import (
    CourseFinished,
    CourseProgressStarted,
    CourseProgressTick,
    FilePostfix,
    FileProgressEnded,
    FileProgressStarted,
    FileProgressTick,
    LogEvent,
    NullReporter,
    Reporter,
    RunFinished,
    RunStarted,
    VideoBytesFinished,
    VideoBytesProgress,
    VideoBytesStarted,
)
from .jaccount_qr import _get_following_login, _token_url_from_response
from .paths import AppPaths, get_app_paths
from .service import CancelToken, DiskFullError, filter_courses
from .state import LockHeldError, SyncState
from .stores import (
    DEFAULT_VIDEO_MAX_CONCURRENT_VIDEOS,
    DEFAULT_VIDEO_MAX_WORKERS_PER_VIDEO,
)
from .traversal import sanitize_name


ProgressCallback = Callable[[int, int], None]


SJTU_VIDEO_HOST = "v.sjtu.edu.cn"
SJTU_VIDEO_MARKER = "jy-application-canvas-sjtu-ui"


class VideoError(RuntimeError):
    pass


class VideoProviderError(VideoError):
    pass


class _DownloadCancelled(Exception):
    """Worker raises this when cancel_token fires; signals 'do not retry'."""


class _RangeNotPartial(Exception):
    """Server returned 200 Full instead of 206 Partial for a Range request; fall back to single-stream."""


class _ProgressAgg:
    """Thread-safe byte accumulator with time-throttled callback emission."""

    def __init__(
        self,
        cb: ProgressCallback | None,
        total: int,
        throttle_seconds: float,
    ) -> None:
        self._cb = cb
        self._total = max(0, int(total))
        self._downloaded = 0
        self._throttle = max(0.0, float(throttle_seconds))
        self._last_emit = 0.0
        self._lock = threading.Lock()

    def set_total(self, total: int) -> None:
        with self._lock:
            self._total = max(self._total, int(total))

    def add(self, delta: int) -> None:
        if self._cb is None or delta <= 0:
            return
        emit = False
        downloaded = total = 0
        with self._lock:
            self._downloaded += delta
            now = time.monotonic()
            if now - self._last_emit >= self._throttle:
                self._last_emit = now
                emit = True
                downloaded = self._downloaded
                total = self._total
        if emit:
            try:
                self._cb(downloaded, total)
            except Exception:
                pass  # never let progress callback errors break downloads

    def flush(self) -> None:
        if self._cb is None:
            return
        with self._lock:
            downloaded = self._downloaded
            total = self._total
            self._last_emit = time.monotonic()
        try:
            self._cb(downloaded, total)
        except Exception:
            pass


def _split_byte_ranges(total: int, parts: int) -> list[tuple[int, int]]:
    """Split [0, total) into `parts` inclusive (start, end) byte ranges."""
    parts = max(1, min(int(parts), max(1, total)))
    chunk = total // parts
    ranges: list[tuple[int, int]] = []
    for i in range(parts):
        start = i * chunk
        end = (start + chunk - 1) if i < parts - 1 else total - 1
        ranges.append((start, end))
    return ranges


@dataclass
class _SyntheticCourse:
    """Minimal stand-in for a canvasapi Course when Canvas API is unavailable."""
    id: int
    name: str
    access_restricted: bool = False


@dataclass(frozen=True)
class LectureFilter:
    ranges: tuple[tuple[int, int], ...] = ()

    def matches(self, index: int) -> bool:
        if not self.ranges:
            return True
        return any(start <= index <= end for start, end in self.ranges)


@dataclass(frozen=True)
class VideoAsset:
    kind: str
    title: str
    url: str
    asset_id: str
    extension: str = ".mp4"
    size: int | None = None
    modified_at: str | None = None


@dataclass(frozen=True)
class VideoLecture:
    index: int
    title: str
    lecture_id: str
    assets: tuple[VideoAsset, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class VideoEntry:
    url: str
    token_id: str
    canvas_course_id: int | None = None


@dataclass
class VideoRunOptions:
    dry_run: bool = False
    only_courses: list[int] = field(default_factory=list)
    skip_courses: list[int] = field(default_factory=list)
    download_dir: Path | None = None
    lecture_filter: LectureFilter = field(default_factory=LectureFilter)
    per_course_lectures: dict[int, set[int]] | None = None
    video_urls: list[str] = field(default_factory=list)
    browser_cookies: bool = False
    cached_cookies: bool = False
    session_cookies: list[dict] = field(default_factory=list)
    max_concurrent_videos: int = DEFAULT_VIDEO_MAX_CONCURRENT_VIDEOS
    max_workers_per_video: int = DEFAULT_VIDEO_MAX_WORKERS_PER_VIDEO


def parse_lecture_filter(value: str | None) -> LectureFilter:
    if value is None or not value.strip():
        return LectureFilter()
    ranges: list[tuple[int, int]] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            raise VideoError("节次范围格式错误：存在空片段。")
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            if not start_text.strip().isdigit() or not end_text.strip().isdigit():
                raise VideoError(f"节次范围格式错误：{part!r}")
            start = int(start_text)
            end = int(end_text)
        else:
            if not part.isdigit():
                raise VideoError(f"节次范围格式错误：{part!r}")
            start = end = int(part)
        if start <= 0 or end <= 0 or start > end:
            raise VideoError(f"节次范围格式错误：{part!r}")
        ranges.append((start, end))
    return LectureFilter(tuple(ranges))


def parse_sjtu_token(url: str) -> str | None:
    parsed = urlparse(url)
    candidates = [parsed.query, parsed.fragment]
    if "?" in parsed.fragment:
        candidates.append(parsed.fragment.split("?", 1)[1])
    for query in candidates:
        params = parse_qs(query)
        token = params.get("tokenId") or params.get("tokenid")
        if token and token[0].strip():
            return token[0].strip()
    return None


def extract_sjtu_video_links(text: str) -> list[str]:
    if not text:
        return []
    decoded = html.unescape(text).replace("\\/", "/")
    pattern = re.compile(
        r"https://v\.sjtu\.edu\.cn/[^\s\"'<>]*jy-application-canvas-sjtu-ui[^\s\"'<>]*",
        re.IGNORECASE,
    )
    links: list[str] = []
    seen: set[str] = set()
    for match in pattern.findall(decoded):
        link = unquote(match).rstrip(").,;")
        if parse_sjtu_token(link) and link not in seen:
            seen.add(link)
            links.append(link)
    return links


def extract_canvas_external_tool_links(text: str, canvas_url: str) -> list[str]:
    if not text:
        return []
    decoded = html.unescape(text).replace("\\/", "/")
    pattern = re.compile(
        r"(?:https://[^/\s\"'<>]+)?/courses/\d+/external_tools/\d+(?:\?[^\s\"'<>]*)?",
        re.IGNORECASE,
    )
    links: list[str] = []
    seen: set[str] = set()
    for match in pattern.findall(decoded):
        link = unquote(match).rstrip(").,;")
        absolute = urljoin(canvas_url.rstrip("/") + "/", link.lstrip("/"))
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


def _iter_values(obj) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_values(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _iter_values(value)
    else:
        for exporter in ("to_json", "to_dict"):
            method = getattr(obj, exporter, None)
            if callable(method):
                try:
                    yield from _iter_values(method())
                except Exception:  # noqa: BLE001 - best-effort object inspection
                    pass
        raw = getattr(obj, "_attributes", None) or getattr(obj, "attributes", None)
        if isinstance(raw, dict):
            yield from _iter_values(raw)
        raw_dict = getattr(obj, "__dict__", None)
        if isinstance(raw_dict, dict):
            public = {
                key: value
                for key, value in raw_dict.items()
                if not key.startswith("_") and key not in {"requester", "canvas"}
            }
            yield from _iter_values(public)
        for attr in ("url", "html_url", "external_url", "body", "description", "title"):
            value = getattr(obj, attr, None)
            if isinstance(value, str):
                yield value


class VideoLinkDiscovery:
    def __init__(self, client: CanvasClient):
        self.client = client
        self.last_resolution_errors: list[str] = []

    def discover(self, course) -> list[VideoEntry]:
        links: list[str] = []
        external_tool_links: list[str] = []
        seen_external_tools: set[str] = set()
        for source in self._course_sources(course):
            for value in _iter_values(source):
                links.extend(extract_sjtu_video_links(value))
                for tool_link in extract_canvas_external_tool_links(value, self.client.config.canvas_url):
                    if tool_link not in seen_external_tools:
                        seen_external_tools.add(tool_link)
                        external_tool_links.append(tool_link)
        links.extend(self._resolve_external_tool_links(external_tool_links))
        result: list[VideoEntry] = []
        seen_tokens: set[str] = set()
        for link in links:
            token = parse_sjtu_token(link)
            if token and token not in seen_tokens:
                seen_tokens.add(token)
                result.append(VideoEntry(url=link, token_id=token))
        return result

    def entries_from_urls(self, urls: Iterable[str]) -> list[VideoEntry]:
        self.last_resolution_errors = []
        direct_links: list[str] = []
        external_tool_links: list[str] = []
        for url in urls:
            direct_links.extend(extract_sjtu_video_links(url))
            external_tool_links.extend(
                extract_canvas_external_tool_links(url, self.client.config.canvas_url)
            )
        direct_links.extend(self._resolve_external_tool_links(external_tool_links))
        entries: list[VideoEntry] = []
        seen_tokens: set[str] = set()
        for link in direct_links:
            token = parse_sjtu_token(link)
            if token and token not in seen_tokens:
                seen_tokens.add(token)
                entries.append(VideoEntry(url=link, token_id=token))
        return entries

    def load_browser_cookies(self, urls: list[str]) -> int:
        return load_browser_cookies(self.client.session, urls)

    def _course_sources(self, course) -> Iterable:
        yield course
        yield from self._rest_course_sources(course)
        yield from self._safe_list(lambda: course.get_pages())
        for module in self._safe_list(lambda: course.get_modules()):
            yield module
            item_getter = getattr(module, "get_module_items", None)
            if callable(item_getter):
                for item in self._safe_list(item_getter):
                    yield item
                    page_url = getattr(item, "page_url", None)
                    if page_url:
                        page_getter = getattr(course, "get_page", None)
                        if callable(page_getter):
                            yield from self._safe_list(lambda p=page_url: [page_getter(p)])

    def _rest_course_sources(self, course) -> Iterable:
        course_id = getattr(course, "id", None)
        if course_id is None:
            return
        yield from self._rest_get_paginated(
            f"/api/v1/courses/{course_id}/modules",
            params={"include[]": "items", "per_page": 100},
        )
        yield from self._rest_get_paginated(
            f"/api/v1/courses/{course_id}/tabs",
            params={},
        )
        yield from self._rest_get_paginated(
            f"/api/v1/courses/{course_id}/external_tools",
            params={"per_page": 100},
        )
        yield from self._rest_get_paginated(
            f"/api/v1/courses/{course_id}/front_page",
            params={},
        )
        yield from self._html_get(f"/courses/{course_id}")
        pages = list(
            self._rest_get_paginated(
                f"/api/v1/courses/{course_id}/pages",
                params={"per_page": 100},
            )
        )
        yield from pages
        for page in pages:
            page_url = page.get("url") if isinstance(page, dict) else None
            if page_url:
                yield from self._rest_get_paginated(
                    f"/api/v1/courses/{course_id}/pages/{page_url}",
                    params={},
                )

    def _resolve_external_tool_links(self, urls: Iterable[str]) -> list[str]:
        session = getattr(self.client, "session", None)
        if session is None:
            return []
        # LTI resolution needs cookie auth only — API token causes login redirect
        auth = session.headers.pop("Authorization", None)
        links: list[str] = []
        seen: set[str] = set()
        try:
            for url in urls:
                time.sleep(getattr(self.client.config, "request_delay", 0))
                try:
                    resp = _get_following_login(session, url)
                    resolved = _token_url_from_response(session, resp)
                    if resolved and resolved not in seen:
                        seen.add(resolved)
                        links.append(resolved)
                        continue
                except (requests.exceptions.RequestException, RuntimeError, OSError):
                    pass
                if not links or all(parse_sjtu_token(link) is None for link in links):
                    self.last_resolution_errors.append(
                        f"{url}: 跳转到 Canvas 登录页，API token 无法完成浏览器 LTI 登录"
                    )
        finally:
            if auth:
                session.headers["Authorization"] = auth
        return links

    def _rest_get_paginated(self, path: str, params: dict) -> Iterator:
        session = getattr(self.client, "session", None)
        config = getattr(self.client, "config", None)
        canvas_url = getattr(config, "canvas_url", "")
        if session is None or not canvas_url:
            return
        url = urljoin(canvas_url.rstrip("/") + "/", path.lstrip("/"))
        next_params = dict(params)
        while url:
            time.sleep(getattr(config, "request_delay", 0))
            try:
                resp = session.get(url, params=next_params, timeout=30)
                if resp.status_code in (401, 403, 404):
                    return
                resp.raise_for_status()
                payload = resp.json()
            except (requests.exceptions.RequestException, ValueError):
                return
            if isinstance(payload, list):
                yield from payload
            else:
                yield payload
            next_params = {}
            url = _next_link(resp.headers.get("Link", ""))

    def _html_get(self, path: str) -> Iterator[str]:
        session = getattr(self.client, "session", None)
        config = getattr(self.client, "config", None)
        canvas_url = getattr(config, "canvas_url", "")
        if session is None or not canvas_url:
            return
        url = urljoin(canvas_url.rstrip("/") + "/", path.lstrip("/"))
        time.sleep(getattr(config, "request_delay", 0))
        try:
            resp = session.get(url, timeout=30, allow_redirects=True)
            if resp.status_code in (401, 403, 404):
                return
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return
        text = getattr(resp, "text", "")
        if isinstance(text, str):
            yield text

    def _safe_list(self, getter) -> list:
        time.sleep(self.client.config.request_delay)
        try:
            return list(getter())
        except (AttributeError, ResourceDoesNotExist, requests.exceptions.RequestException):
            return []


class SjtuiVsProvider:
    """Provider for SJTU's Canvas video app.

    The public app URL exposes a tokenId. The exact JSON endpoint can drift, so
    endpoint discovery is intentionally isolated here and backed by tests around
    the parser. If the live service changes, patch this class only.
    """

    _CANDIDATE_ENDPOINTS = (
        "/jy-application-canvas-sjtu-api/api/ivsModules/index",
        "/jy-application-canvas-sjtu-api/ivsModules/index",
        "/jy-application-canvas-sjtu-api/api/ivsModules/list",
        "/jy-application-canvas-sjtu-api/ivsModules/list",
    )

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.cookies: dict[str, str] | None = None

    def list_lectures(self, entry: VideoEntry) -> list[VideoLecture]:
        token_error = None
        try:
            lectures = self._list_lectures_from_token(entry)
            if lectures:
                return lectures
        except Exception as e:
            token_error = e

        try:
            payload = self._fetch_payload(entry)
            lectures = list(self._parse_lectures(payload))
            if lectures:
                return lectures
        except Exception as e:
            if token_error is not None:
                raise VideoProviderError(
                    f"directOnDemandPlay 路径失败：{token_error}；legacy 路径也失败：{e}"
                ) from token_error
            raise VideoProviderError(f"视频列表接口失败：{e}") from e

        message = "SJTU 视频接口返回中没有识别到课堂/录屏资源。"
        if token_error is not None:
            message += f" directOnDemandPlay 路径失败：{token_error}"
        raise VideoProviderError(message)

    def _list_lectures_from_token(self, entry: VideoEntry) -> list[VideoLecture]:
        token_resp = self.session.get(
            f"https://{SJTU_VIDEO_HOST}/jy-application-canvas-sjtu/lti3/getAccessTokenByTokenId",
            params={"tokenId": entry.token_id},
            cookies=self.cookies,
            timeout=30,
        )
        token_resp.raise_for_status()
        token_payload = token_resp.json()
        if str(token_payload.get("code", "")) == "-1" and "过期" in str(token_payload.get("message", "")):
            raise VideoProviderError("tokenId 已过期，请重新解析视频入口。")
        token_data = _extract_token_data(token_payload)
        if not token_data:
            raise VideoProviderError(f"getAccessTokenByTokenId 未返回可识别 token 数据：{_payload_summary(token_payload)}")

        access_token = (
            token_data.get("token")
            or token_data.get("accessToken")
            or token_data.get("access_token")
        )
        if not access_token:
            raise VideoProviderError(f"getAccessTokenByTokenId 未返回 token：{_payload_summary(token_payload)}")

        params = token_data.get("params") or token_data.get("param") or token_data
        lti_course_id = (
            params.get("courId")
            or params.get("canvasCourseId")
            or params.get("courseId")
            or params.get("ltiCourseId")
        )
        if not lti_course_id:
            raise VideoProviderError("getAccessTokenByTokenId 未返回课程 ID。")

        headers = {"token": str(access_token)}
        records = self._request_direct_video_list(
            str(lti_course_id),
            headers,
            canvas_course_id=entry.canvas_course_id,
        )
        lectures: list[VideoLecture] = []
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                continue
            detail = self._request_direct_video_detail(record, headers)
            lecture = self._lecture_from_direct_detail(index, record, detail)
            if lecture.assets:
                lectures.append(lecture)
        return lectures

    def _request_direct_video_list(
        self, lti_course_id: str, headers: dict, canvas_course_id: int | None = None
    ) -> list:
        candidate_ids: list[str] = []
        for value in (lti_course_id, str(canvas_course_id) if canvas_course_id else None):
            if value is None:
                continue
            if value not in candidate_ids:
                candidate_ids.append(value)
            if value.isdigit():
                trimmed = value.lstrip("0")
                if trimmed and trimmed not in candidate_ids:
                    candidate_ids.append(trimmed)
        bodies: list[dict] = []
        for cid in candidate_ids:
            bodies.extend([
                {"canvasCourseId": cid},
                {"canvasCourseId": cid, "pageIndex": 1, "pageSize": 1000},
                {"courId": cid},
                {"courId": cid, "pageIndex": 1, "pageSize": 1000},
                {"courseId": cid},
                {"ltiCourseId": cid},
            ])
        last_payload = None
        last_empty: list | None = None
        for body in bodies:
            resp = self.session.post(
                f"https://{SJTU_VIDEO_HOST}/jy-application-canvas-sjtu/directOnDemandPlay/findVodVideoList",
                json=body,
                headers=headers,
                cookies=self.cookies,
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            last_payload = payload
            records = _extract_video_records(payload)
            if isinstance(records, list):
                if records:
                    return records
                last_empty = records  # keep trying; remember last empty as fallback
        if last_empty is not None:
            return last_empty
        raise VideoProviderError(f"视频列表接口未返回可识别列表：{_payload_summary(last_payload)}")

    def _request_direct_video_detail(self, record: dict, headers: dict) -> dict:
        video_id = record.get("videoId") or record.get("id")
        if not video_id:
            raise VideoProviderError("视频列表记录缺少 videoId。")
        resp = self.session.post(
            f"https://{SJTU_VIDEO_HOST}/jy-application-canvas-sjtu/directOnDemandPlay/getVodVideoInfos",
            data={
                "playTypeHls": "true",
                "id": video_id,
                "isAudit": "true",
            },
            headers=headers,
            cookies=self.cookies,
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        detail = _first_dict(payload, ("data", "body"))
        if not detail:
            raise VideoProviderError(f"视频详情接口未返回可识别对象：{_payload_summary(payload)}")
        return detail

    def _lecture_from_direct_detail(self, index: int, record: dict, detail: dict) -> VideoLecture:
        title = str(
            detail.get("courName")
            or record.get("courName")
            or record.get("name")
            or record.get("title")
            or f"第{index}节"
        )
        lecture_id = str(record.get("videoId") or record.get("id") or detail.get("id") or _stable_id(title))
        play_list = detail.get("videoPlayResponseVoList") or detail.get("videoPlayList") or []
        if isinstance(play_list, dict):
            play_list = [play_list]
        play_list = sorted(
            [item for item in play_list if isinstance(item, dict)],
            key=lambda item: _coerce_int(item.get("cdviViewNum"), 0),
        )
        assets: list[VideoAsset] = []
        for offset, item in enumerate(play_list):
            url = (
                item.get("rtmpUrlHdv")
                or item.get("videoUrl")
                or item.get("playUrl")
                or item.get("url")
            )
            if not isinstance(url, str) or not _looks_like_video_url(url):
                continue
            view_num = _coerce_int(item.get("cdviViewNum"), offset)
            kind = "课堂" if view_num == 0 else "录屏"
            asset_id = str(item.get("id") or item.get("videoId") or f"{lecture_id}:{view_num}:{offset}")
            assets.append(
                VideoAsset(
                    kind=kind,
                    title=kind,
                    url=url,
                    asset_id=f"{kind}:{asset_id}",
                    extension=_extension_from_url(url),
                    size=_optional_int(item.get("fileSize") or item.get("size")),
                    modified_at=_optional_str(item.get("updateTime") or item.get("updatedAt")),
                )
            )
        return VideoLecture(index=index, title=title, lecture_id=lecture_id, assets=tuple(assets))

    def _fetch_payload(self, entry: VideoEntry):
        headers = {"Referer": entry.url}
        errors: list[str] = []
        for endpoint in self._CANDIDATE_ENDPOINTS:
            url = f"https://{SJTU_VIDEO_HOST}{endpoint}"
            try:
                resp = self.session.get(
                    url,
                    params={"tokenId": entry.token_id},
                    headers=headers,
                    cookies=self.cookies,
                    timeout=30,
                )
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                return resp.json()
            except (requests.exceptions.RequestException, ValueError) as e:
                errors.append(f"{endpoint}: {e}")
        joined = "; ".join(errors[-3:]) if errors else "所有候选接口均不可用"
        raise VideoProviderError(f"无法读取 SJTU 视频节次列表：{joined}")

    def _parse_lectures(self, payload) -> Iterator[VideoLecture]:
        nodes = list(_walk_json_nodes(payload))
        lecture_nodes = [node for node in nodes if isinstance(node, dict) and _node_has_video(node)]
        if not lecture_nodes and isinstance(payload, dict):
            lecture_nodes = [payload]

        for fallback_index, node in enumerate(lecture_nodes, start=1):
            if not isinstance(node, dict):
                continue
            assets = tuple(_assets_from_node(node))
            if not assets:
                continue
            index = _coerce_int(
                _first_value(node, ("index", "idx", "sort", "order", "lessonIndex", "moduleIndex")),
                fallback_index,
            )
            title = str(
                _first_value(
                    node,
                    ("title", "name", "moduleName", "lessonName", "coursewareName"),
                )
                or f"第{index}节"
            )
            lecture_id = str(
                _first_value(node, ("id", "lectureId", "moduleId", "lessonId"))
                or _stable_id(f"{index}:{title}")
            )
            yield VideoLecture(index=index, title=title, lecture_id=lecture_id, assets=assets)


class VideoDownloader:
    """Multi-threaded video downloader.

    Strategies:
    - Single MP4: HEAD probe → if server supports Range, split into N temp chunks
      downloaded in parallel and concatenated. Otherwise fall back to single stream.
    - HLS (.m3u8): parse all segment URLs, download N segments in parallel into
      temp files, concatenate in playlist order.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        retries: int = 3,
        retry_base_delay: float = 1.0,
        progress_throttle_seconds: float = 0.1,
        chunk_size: int = 256 * 1024,
        range_threshold_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self.session = session or requests.Session()
        self.retries = retries
        self.retry_base_delay = retry_base_delay
        self.progress_throttle_seconds = progress_throttle_seconds
        self.chunk_size = chunk_size
        # Below this size, the Range-split overhead is not worth it.
        self.range_threshold_bytes = range_threshold_bytes

    def download(
        self,
        asset: VideoAsset,
        dest: Path,
        *,
        workers: int = DEFAULT_VIDEO_MAX_WORKERS_PER_VIDEO,
        progress_cb: ProgressCallback | None = None,
        cancel_token: CancelToken | None = None,
    ) -> None:
        attempts = max(1, self.retries)
        workers = max(1, int(workers))
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                self._download_once(asset, dest, workers, progress_cb, cancel_token)
                return
            except _DownloadCancelled:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts - 1 or not self._should_retry(exc):
                    raise
                if cancel_token is not None and cancel_token.is_cancelled():
                    raise _DownloadCancelled("已取消") from exc
                time.sleep(self.retry_base_delay * (2 ** attempt))
        if last_exc is not None:
            raise last_exc

    def _download_once(
        self,
        asset: VideoAsset,
        dest: Path,
        workers: int,
        progress_cb: ProgressCallback | None,
        cancel_token: CancelToken | None,
    ) -> None:
        if _is_hls(asset.url):
            self._download_hls(asset.url, dest, workers, progress_cb, cancel_token)
        else:
            self._download_stream(asset.url, dest, workers, progress_cb, cancel_token)

    def _should_retry(self, exc: Exception) -> bool:
        if isinstance(exc, _DownloadCancelled):
            return False
        if isinstance(exc, VideoError):
            return False
        if isinstance(exc, requests.exceptions.HTTPError):
            response = exc.response
            return response is None or response.status_code >= 500
        if isinstance(exc, requests.exceptions.RequestException):
            return True
        if isinstance(exc, OSError):
            return "No space left" not in str(exc) and "磁盘空间不足" not in str(exc)
        return False

    @staticmethod
    def _check_cancel(cancel_token: CancelToken | None) -> None:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise _DownloadCancelled("已取消")

    # ── Single-file stream ──
    def _download_stream(
        self,
        url: str,
        dest: Path,
        workers: int,
        progress_cb: ProgressCallback | None,
        cancel_token: CancelToken | None,
    ) -> None:
        self._validate_url(url)
        self._check_cancel(cancel_token)

        total_size, accept_ranges = self._probe_range_support(url)

        if (
            not accept_ranges
            or total_size <= self.range_threshold_bytes
            or workers <= 1
        ):
            self._download_stream_single(url, dest, total_size, progress_cb, cancel_token)
            return

        try:
            self._download_stream_ranges(
                url, dest, total_size, workers, progress_cb, cancel_token
            )
        except _RangeNotPartial:
            self._download_stream_single(url, dest, 0, progress_cb, cancel_token)

    def _probe_range_support(self, url: str) -> tuple[int, bool]:
        """Returns (content_length, server_supports_byte_ranges).

        Best-effort: if HEAD fails for any reason, return (0, False) so we
        fall through to single-stream download.
        """
        try:
            resp = self.session.head(url, timeout=30, allow_redirects=True)
        except requests.exceptions.RequestException:
            return 0, False
        if resp.status_code >= 400:
            return 0, False
        ar = resp.headers.get("Accept-Ranges", "").lower()
        accepts = "bytes" in ar
        cl = resp.headers.get("Content-Length", "")
        total = int(cl) if cl.isdigit() else 0
        return total, accepts

    def _download_stream_single(
        self,
        url: str,
        dest: Path,
        total_hint: int,
        progress_cb: ProgressCallback | None,
        cancel_token: CancelToken | None,
    ) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            with self.session.get(
                url, stream=True, timeout=60, allow_redirects=True
            ) as resp:
                resp.raise_for_status()
                ct = resp.headers.get("Content-Type", "").lower()
                if ct.startswith(("text/html", "application/xhtml")):
                    raise VideoError(
                        f"视频下载失败：服务器返回 HTML，Content-Type={ct!r}"
                    )
                total = total_hint
                if not total:
                    cl = resp.headers.get("Content-Length", "")
                    if cl.isdigit():
                        total = int(cl)
                agg = _ProgressAgg(progress_cb, total, self.progress_throttle_seconds)
                with open(part, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=self.chunk_size):
                        self._check_cancel(cancel_token)
                        if chunk:
                            f.write(chunk)
                            agg.add(len(chunk))
                agg.flush()
            self._replace_part(part, dest)
        finally:
            if part.exists():
                with contextlib.suppress(OSError):
                    part.unlink()

    def _download_stream_ranges(
        self,
        url: str,
        dest: Path,
        total: int,
        workers: int,
        progress_cb: ProgressCallback | None,
        cancel_token: CancelToken | None,
    ) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        agg = _ProgressAgg(progress_cb, total, self.progress_throttle_seconds)

        ranges = _split_byte_ranges(total, workers)
        temp_dir = dest.with_suffix(dest.suffix + ".parts")
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_paths = [temp_dir / f"part_{i:04d}" for i in range(len(ranges))]
        local_cancel = cancel_token or CancelToken()

        try:
            with ThreadPoolExecutor(
                max_workers=min(workers, len(ranges))
            ) as pool:
                futures = [
                    pool.submit(
                        self._fetch_byte_range,
                        url,
                        start,
                        end,
                        temp_paths[i],
                        agg,
                        local_cancel,
                    )
                    for i, (start, end) in enumerate(ranges)
                ]
                first_exc: Exception | None = None
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as exc:
                        if first_exc is None:
                            first_exc = exc
                        # cancel siblings so they exit promptly
                        if cancel_token is None:
                            local_cancel.cancel()
                if first_exc is not None:
                    raise first_exc

            agg.flush()
            with open(part, "wb") as out:
                for p in temp_paths:
                    with open(p, "rb") as src:
                        shutil.copyfileobj(src, out, length=1024 * 1024)
            self._replace_part(part, dest)
        finally:
            for p in temp_paths:
                with contextlib.suppress(OSError):
                    p.unlink()
            with contextlib.suppress(OSError):
                temp_dir.rmdir()
            if part.exists():
                with contextlib.suppress(OSError):
                    part.unlink()

    def _fetch_byte_range(
        self,
        url: str,
        start: int,
        end: int,
        dest_path: Path,
        agg: _ProgressAgg,
        cancel_token: CancelToken,
    ) -> None:
        self._check_cancel(cancel_token)
        headers = {"Range": f"bytes={start}-{end}"}
        with self.session.get(
            url, headers=headers, stream=True, timeout=60, allow_redirects=True
        ) as resp:
            if resp.status_code == 200:
                raise _RangeNotPartial("服务器忽略 Range 请求返回完整文件（200），回退单流。")
            if resp.status_code != 206:
                resp.raise_for_status()
                raise VideoError(
                    f"Range 请求返回意外状态：{resp.status_code}"
                )
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=self.chunk_size):
                    self._check_cancel(cancel_token)
                    if chunk:
                        f.write(chunk)
                        agg.add(len(chunk))

    # ── HLS ──
    def _download_hls(
        self,
        url: str,
        dest: Path,
        workers: int,
        progress_cb: ProgressCallback | None,
        cancel_token: CancelToken | None,
    ) -> None:
        self._validate_url(url)
        self._check_cancel(cancel_token)
        playlist = self._get_text(url)
        segment_urls = self._parse_hls_segments(url, playlist)
        if not segment_urls:
            raise VideoError("视频下载失败：m3u8 中没有可下载分片。")

        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        # HLS doesn't expose total bytes upfront; agg stays at total=0
        # (UI shows indeterminate or just "X.X MB downloaded").
        agg = _ProgressAgg(progress_cb, 0, self.progress_throttle_seconds)

        temp_dir = dest.with_suffix(dest.suffix + ".segs")
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_paths = [
            temp_dir / f"seg_{i:05d}.ts" for i in range(len(segment_urls))
        ]
        local_cancel = cancel_token or CancelToken()

        try:
            with ThreadPoolExecutor(
                max_workers=min(workers, len(segment_urls))
            ) as pool:
                futures = [
                    pool.submit(
                        self._fetch_segment,
                        seg_url,
                        temp_paths[i],
                        agg,
                        local_cancel,
                    )
                    for i, seg_url in enumerate(segment_urls)
                ]
                first_exc: Exception | None = None
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as exc:
                        if first_exc is None:
                            first_exc = exc
                        if cancel_token is None:
                            local_cancel.cancel()
                if first_exc is not None:
                    raise first_exc

            agg.flush()
            with open(part, "wb") as out:
                for p in temp_paths:
                    with open(p, "rb") as src:
                        shutil.copyfileobj(src, out, length=1024 * 1024)
            self._replace_part(part, dest)
        finally:
            for p in temp_paths:
                with contextlib.suppress(OSError):
                    p.unlink()
            with contextlib.suppress(OSError):
                temp_dir.rmdir()
            if part.exists():
                with contextlib.suppress(OSError):
                    part.unlink()

    def _fetch_segment(
        self,
        url: str,
        dest_path: Path,
        agg: _ProgressAgg,
        cancel_token: CancelToken,
    ) -> None:
        self._check_cancel(cancel_token)
        with self.session.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=self.chunk_size):
                    self._check_cancel(cancel_token)
                    if chunk:
                        f.write(chunk)
                        agg.add(len(chunk))

    # ── Helpers ──
    def _replace_part(self, part: Path, dest: Path) -> None:
        os.replace(part, dest)

    def _get_text(self, url: str) -> str:
        with self.session.get(url, timeout=30) as resp:
            resp.raise_for_status()
            return resp.text

    def _parse_hls_segments(self, playlist_url: str, playlist: str) -> list[str]:
        if "#EXT-X-KEY" in playlist.upper():
            raise VideoError("视频下载失败：暂不支持加密 m3u8。")
        lines = [line.strip() for line in playlist.splitlines() if line.strip()]
        nested = [line for line in lines if not line.startswith("#") and line.endswith(".m3u8")]
        if nested:
            nested_url = urljoin(playlist_url, nested[0])
            return self._parse_hls_segments(nested_url, self._get_text(nested_url))
        return [urljoin(playlist_url, line) for line in lines if not line.startswith("#")]

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise VideoError("视频下载失败：视频 URL 必须使用 HTTPS。")
        if not parsed.netloc:
            raise VideoError("视频下载失败：视频 URL 缺少域名。")


class VideoService:
    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths | None = None,
        client: CanvasClient | None = None,
        discovery: VideoLinkDiscovery | None = None,
        provider: SjtuiVsProvider | None = None,
        downloader: VideoDownloader | None = None,
    ) -> None:
        self.config = config
        self.paths = paths or get_app_paths()
        self.client = client or CanvasClient(config)
        self.discovery = discovery or VideoLinkDiscovery(self.client)
        self.provider = provider or SjtuiVsProvider()
        self.downloader = downloader or VideoDownloader()

    def _apply_session_cookies(self, cookies: list[dict]) -> None:
        if not cookies:
            return
        for target in (self.client.session, self.provider.session, self.downloader.session):
            apply_session_cookies(target, cookies)

    def run(
        self,
        options: VideoRunOptions | None = None,
        reporter: Reporter | None = None,
        cancel_token: CancelToken | None = None,
    ) -> int:
        options = options or VideoRunOptions()
        reporter = reporter or NullReporter()
        cancel_token = cancel_token or CancelToken()

        if options.download_dir is not None:
            self.config.download_dir = options.download_dir
        self.config.dry_run = options.dry_run
        self._apply_session_cookies(options.session_cookies)
        if options.session_cookies:
            # _apply_session_cookies only populates the session jars; mirror the
            # dict form so provider requests that pass cookies=... explicitly
            # also see the fresh cookies.
            self.provider.cookies = {
                c["name"]: c["value"]
                for c in options.session_cookies
                if isinstance(c, dict) and "name" in c and "value" in c
            }

        if options.cached_cookies:
            # Disk cookies are a fallback for callers that did not supply
            # session_cookies (e.g. CLI --cached-cookies). With fresh session
            # cookies in hand, a DPAPI read failure must not abort the run.
            if not options.session_cookies:
                try:
                    cached = load_cookie_cache(self.paths.video_cookies_file)
                    self._apply_session_cookies(cached)
                    self.provider.cookies = {
                        c["name"]: c["value"]
                        for c in cached
                        if isinstance(c, dict) and "name" in c and "value" in c
                    }
                    reporter.emit(LogEvent(f"已从缓存加载 {len(cached)} 个 Cookie"))
                except (OSError, ValueError) as e:
                    reporter.emit(LogEvent(f"加载 Cookie 缓存失败：{e}"))
                    return 1
            if not options.only_courses:
                try:
                    import json as _json

                    raw = self.paths.video_auto_courses_file.read_text(encoding="utf-8")
                    course_list = _json.loads(raw)
                except (OSError, ValueError) as e:
                    reporter.emit(LogEvent(f"无自动下载课程列表：{e}"))
                    return 1
                if not isinstance(course_list, list):
                    reporter.emit(LogEvent("自动下载课程列表格式不合法。"))
                    return 1
                if not course_list:
                    reporter.emit(LogEvent("自动下载课程列表为空，请先扫码登录扫描课程。"))
                    return 0
                if isinstance(course_list[0], dict):
                    enabled_items = [
                        item
                        for item in course_list
                        # Old cache entries did not have an enabled flag.
                        if isinstance(item, dict) and item.get("enabled", True)
                    ]
                    if not enabled_items:
                        reporter.emit(LogEvent("自动下载课程列表中没有启用的课程。"))
                        return 0
                    options.only_courses = [item["id"] for item in enabled_items if "id" in item]
                    tool_urls = [item["tool_url"] for item in enabled_items if "tool_url" in item]
                    options.video_urls = list(dict.fromkeys(tool_urls + options.video_urls))
                    reporter.emit(LogEvent(f"从缓存加载自动下载课程列表（{len(options.only_courses)} 门，{len(tool_urls)} 个视频入口）"))
                else:
                    options.only_courses = course_list
                    reporter.emit(LogEvent(f"从缓存加载自动下载课程列表（{len(options.only_courses)} 门）"))

        state = SyncState(self.paths.state_file)
        state_loaded = False
        downloaded = 0
        try:
            try:
                state.load()
                state_loaded = True
            except LockHeldError as e:
                reporter.emit(LogEvent(f"跳过本次运行：{e}"))
                return 0

            reporter.emit(
                RunStarted(
                    canvas_url=self.config.canvas_url,
                    download_dir=self.config.download_dir,
                    dry_run=self.config.dry_run,
                )
            )
            if options.cached_cookies and options.only_courses:
                try:
                    import json as _json2
                    _raw = self.paths.video_auto_courses_file.read_text(encoding="utf-8")
                    _cache_items = {
                        int(item["id"]): item.get("name", f"course_{item['id']}")
                        for item in _json2.loads(_raw)
                        if isinstance(item, dict) and "id" in item
                    }
                except (OSError, ValueError):
                    _cache_items = {}
                skip_set = set(options.skip_courses or [])
                courses = [
                    _SyntheticCourse(id=cid, name=_cache_items.get(cid, f"course_{cid}"))
                    for cid in options.only_courses
                    if cid not in skip_set
                ]
            else:
                courses = self.client.get_courses()
                courses = filter_courses(courses, options.only_courses, options.skip_courses)
            if not courses:
                reporter.emit(LogEvent("未找到任何课程。"))
                return 0

            reporter.emit(LogEvent(f"共 {len(courses)} 门课程（视频模式）"))
            reporter.emit(CourseProgressStarted(len(courses)))
            for course in courses:
                if cancel_token.is_cancelled():
                    break
                downloaded += self._process_course(course, options, state, reporter, cancel_token)

            if self.config.dry_run:
                state.close()
            else:
                state.save()
            reporter.emit(RunFinished(downloaded=downloaded, cancelled=cancel_token.is_cancelled()))
            return downloaded
        except KeyboardInterrupt:
            reporter.emit(LogEvent("视频下载已中断，正在保存状态。"))
            if state_loaded:
                if self.config.dry_run:
                    state.close()
                else:
                    state.save()
            raise
        finally:
            state.close()

    def _process_course(
        self,
        course,
        options: VideoRunOptions,
        state: SyncState,
        reporter: Reporter,
        cancel_token: CancelToken,
    ) -> int:
        course_name = sanitize_name(safe_course_name(course) or f"course_{course.id}")
        course_dir = self.config.download_dir / course_name
        downloaded = skipped = failed = 0
        progress_started = False

        try:
            lectures: list[VideoLecture] = []
            cache_key = str(course.id)
            cache_data = _load_lecture_cache(self.paths.video_lectures_cache_file)
            cached_entry = cache_data.get(cache_key)
            if options.cached_cookies and isinstance(cached_entry, dict) and cached_entry.get("lectures"):
                lectures = _lectures_from_cache_entry(cached_entry)
                if lectures:
                    reporter.emit(LogEvent(f"  [{course_name}] 从缓存加载 {len(lectures)} 个视频节次"))

            if not lectures:
                entries = self.discovery.discover(course)
                for message in getattr(self.discovery, "last_resolution_errors", []):
                    reporter.emit(LogEvent(f"  [{course_name}] external_tool 解析失败：{message}"))
                if options.video_urls:
                    if options.browser_cookies:
                        try:
                            count = self.discovery.load_browser_cookies(options.video_urls)
                            reporter.emit(LogEvent(f"  [{course_name}] 已从本机浏览器读取 {count} 个 Cookie"))
                        except BrowserCookieError as e:
                            reporter.emit(LogEvent(f"  [{course_name}] 读取浏览器 Cookie 失败：{e}"))
                    # Filter video_urls to only those matching the current course
                    course_urls = [
                        u for u in options.video_urls
                        if f"/courses/{course.id}/" in u or "/courses/" not in u
                    ] or options.video_urls
                    manual_entries = self.discovery.entries_from_urls(course_urls)
                    known_tokens = {entry.token_id for entry in entries}
                    entries.extend(
                        entry for entry in manual_entries if entry.token_id not in known_tokens
                    )
                    if not manual_entries:
                        for message in getattr(self.discovery, "last_resolution_errors", []):
                            reporter.emit(LogEvent(f"  [{course_name}] 手动视频入口未解析：{message}"))
                        reporter.emit(
                            LogEvent(
                                f"  [{course_name}] 如果 external_tools 链接需要网页登录，"
                                "请在浏览器登录后复制最终 v.sjtu.edu.cn 链接传给 --video-url。"
                            )
                        )
                if not entries:
                    reporter.emit(LogEvent(f"  [{course_name}] 未发现 SJTU 课堂视频链接"))
                    reporter.emit(FileProgressStarted(course_name, 0))
                    progress_started = True
                    reporter.emit(FileProgressEnded())
                    progress_started = False
                    return 0

                # Inject Canvas course_id so provider can try multi-source candidates
                from dataclasses import replace as _replace

                entries = [
                    _replace(entry, canvas_course_id=course.id)
                    for entry in entries
                ]
                for entry in entries:
                    try:
                        lectures.extend(self.provider.list_lectures(entry))
                    except VideoProviderError as e:
                        reporter.emit(LogEvent(f"  [{course_name}] 读取视频列表失败：{e}"))
                        failed += 1
                if lectures:
                    _save_lecture_cache(self.paths.video_lectures_cache_file, course.id, course_name, lectures)

            lecture_filter = options.lecture_filter
            if options.per_course_lectures is not None:
                selected = options.per_course_lectures.get(int(course.id))
                if selected is None:
                    lectures = []
                else:
                    lecture_filter = LectureFilter(tuple((i, i) for i in sorted(selected)))
            lectures = [lecture for lecture in lectures if lecture_filter.matches(lecture.index)]
            assets = [
                (lecture, asset)
                for lecture in sorted(lectures, key=lambda item: item.index)
                for asset in lecture.assets
            ]
            reporter.emit(FileProgressStarted(course_name, len(assets)))
            progress_started = True
            if not assets:
                reporter.emit(LogEvent(f"  [{course_name}] 没有匹配节次的视频"))
                return 0

            used_paths: set[Path] = set()
            # Pre-resolve every asset's local path and short-circuit "already
            # current" / "dry-run" cases on the main thread. This keeps the
            # state-file I/O serialized and ensures FileProgressTick is emitted
            # in deterministic order for non-downloaded items.
            pending: list[tuple[VideoLecture, VideoAsset, Path, str]] = []
            for lecture, asset in assets:
                if cancel_token.is_cancelled():
                    break
                local_path = _asset_path(course_dir, lecture, asset, used_paths)
                state_key = _state_key(course.id, lecture, asset)
                if state.is_video_current(state_key, asset, local_path):
                    skipped += 1
                    reporter.emit(FileProgressTick())
                    continue
                if self.config.dry_run:
                    reporter.emit(LogEvent(f"  [DRY] {local_path}"))
                    reporter.emit(FileProgressTick())
                    continue
                pending.append((lecture, asset, local_path, state_key))

            K = max(1, int(getattr(options, "max_concurrent_videos", 1) or 1))
            N = max(1, int(getattr(options, "max_workers_per_video", 1) or 1))
            if K > 1 and len(pending) > 1:
                reporter.emit(FilePostfix(f"{min(K, len(pending))} 个视频并行"))

            state_lock = threading.Lock()
            disk_full_exc: DiskFullError | None = None
            with ThreadPoolExecutor(max_workers=K) as pool:
                future_to_info: dict = {}
                for lecture, asset, local_path, state_key in pending:
                    if cancel_token.is_cancelled():
                        break
                    fut = pool.submit(
                        self._download_one,
                        lecture,
                        asset,
                        local_path,
                        state_key,
                        N,
                        reporter,
                        cancel_token,
                        state,
                        state_lock,
                        single_mode=(K == 1),
                    )
                    future_to_info[fut] = (lecture, asset, local_path)

                for fut in as_completed(future_to_info):
                    lecture, asset, local_path = future_to_info[fut]
                    try:
                        result = fut.result()
                    except DiskFullError as e:
                        disk_full_exc = e
                        cancel_token.cancel()
                        result = "fail"
                    except Exception as e:  # noqa: BLE001 - last-resort guard
                        reporter.emit(
                            LogEvent(f"  [错误] {lecture.title} {asset.kind}: {e}")
                        )
                        result = "fail"
                    if result == "ok":
                        downloaded += 1
                    elif result == "fail":
                        failed += 1
                    # result == "cancelled" → silently absorbed
                    reporter.emit(FileProgressTick())

            if disk_full_exc is not None:
                raise disk_full_exc
        finally:
            if progress_started:
                reporter.emit(FileProgressEnded())
            reporter.emit(CourseFinished(course_name, downloaded, skipped, failed))
            summary = f"  [{course_name}] 视频下载 {downloaded}，跳过 {skipped}"
            if failed:
                summary += f"，失败 {failed}"
            reporter.emit(LogEvent(summary))
            reporter.emit(CourseProgressTick())
            if not self.config.dry_run:
                state.save(release_lock=False)
        return downloaded

    def _download_one(
        self,
        lecture: VideoLecture,
        asset: VideoAsset,
        local_path: Path,
        state_key: str,
        workers: int,
        reporter: Reporter,
        cancel_token: CancelToken,
        state: SyncState,
        state_lock: threading.Lock,
        *,
        single_mode: bool,
    ) -> str:
        """Run one video download in a worker thread.

        Returns "ok" / "fail" / "cancelled". DiskFullError is allowed to
        propagate so the outer loop can abort the whole course.
        """
        label = f"{lecture.index}-{asset.kind}"[:30]
        if single_mode:
            reporter.emit(FilePostfix(label))
        reporter.emit(
            VideoBytesStarted(asset_key=state_key, label=label, total_bytes=0)
        )

        def progress_cb(downloaded_bytes: int, total_bytes: int) -> None:
            reporter.emit(
                VideoBytesProgress(
                    asset_key=state_key,
                    downloaded=downloaded_bytes,
                    total=total_bytes,
                )
            )

        success = False
        try:
            try:
                self.downloader.download(
                    asset,
                    local_path,
                    workers=workers,
                    progress_cb=progress_cb,
                    cancel_token=cancel_token,
                )
            except _DownloadCancelled:
                return "cancelled"
            with state_lock:
                state.record_video(state_key, asset)
            success = True
            return "ok"
        except OSError as e:
            if "No space left" in str(e) or "磁盘空间不足" in str(e):
                raise DiskFullError(
                    f"磁盘空间不足，已终止。最后尝试的视频：{local_path}"
                ) from e
            reporter.emit(
                LogEvent(f"  [错误] {lecture.title} {asset.kind}: {e}")
            )
            return "fail"
        except (VideoError, requests.exceptions.RequestException) as e:
            reporter.emit(
                LogEvent(f"  [错误] {lecture.title} {asset.kind}: {e}")
            )
            return "fail"
        finally:
            reporter.emit(
                VideoBytesFinished(asset_key=state_key, success=success)
            )


def parse_video_course_ids(ids: list | None, flag: str) -> list[int]:
    return parse_course_ids(ids or [], flag)


def _load_lecture_cache(path: Path) -> dict:
    try:
        import json as _json
        raw = path.read_text(encoding="utf-8")
        data = _json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_lecture_cache(path: Path, course_id: int, course_name: str, lectures: list[VideoLecture]) -> None:
    try:
        import json as _json
        now = datetime.datetime.now().isoformat()
        cache = _load_lecture_cache(path)
        cache[str(course_id)] = {
            "course_name": course_name,
            "cached_at": now,
            "lectures": [
                {
                    "index": lec.index,
                    "title": lec.title,
                    "lecture_id": lec.lecture_id,
                    "assets": [
                        {
                            "kind": a.kind,
                            "title": a.title,
                            "url": a.url,
                            "asset_id": a.asset_id,
                            "extension": a.extension,
                            "size": a.size,
                            "modified_at": a.modified_at,
                        }
                        for a in lec.assets
                    ],
                }
                for lec in lectures
            ],
        }
        path.write_text(_json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _lectures_from_cache_entry(entry: dict) -> list[VideoLecture]:
    raw_lectures = entry.get("lectures") if isinstance(entry, dict) else None
    if not isinstance(raw_lectures, list):
        return []
    lectures: list[VideoLecture] = []
    for lec_data in raw_lectures:
        if not isinstance(lec_data, dict):
            continue
        assets: list[VideoAsset] = []
        for a in lec_data.get("assets") or []:
            if not isinstance(a, dict):
                continue
            assets.append(VideoAsset(
                kind=str(a.get("kind", "")),
                title=str(a.get("title", "")),
                url=str(a.get("url", "")),
                asset_id=str(a.get("asset_id", "")),
                extension=str(a.get("extension", ".mp4")),
                size=_optional_int(a.get("size")),
                modified_at=_optional_str(a.get("modified_at")),
            ))
        if assets:
            lectures.append(VideoLecture(
                index=_coerce_int(lec_data.get("index"), 1),
                title=str(lec_data.get("title", "")),
                lecture_id=str(lec_data.get("lecture_id", "")),
                assets=tuple(assets),
            ))
    return lectures


def apply_session_cookies(session: requests.Session, cookies: list[dict]) -> None:
    for item in cookies:
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "")
        path = str(item.get("path") or "/")
        if not name or not domain:
            continue
        session.cookies.set(name, value, domain=domain, path=path)


def _next_link(link_header: str) -> str:
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        match = re.search(r"<([^>]+)>", section)
        if match:
            return match.group(1)
    return ""


def _walk_json_nodes(obj) -> Iterator:
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk_json_nodes(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_json_nodes(value)


def _first_dict(payload, keys: tuple[str, ...]) -> dict | None:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
    return None


def _extract_token_data(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    for path in (
        ("data",),
        ("body",),
        ("result",),
        ("data", "data"),
        ("data", "body"),
        ("body", "data"),
        ("result", "data"),
    ):
        value = _nested_value(payload, path)
        if isinstance(value, dict) and any(
            key in value for key in ("token", "accessToken", "access_token", "params", "courId")
        ):
            return value
    if any(key in payload for key in ("token", "accessToken", "access_token")):
        return payload
    return None


def _extract_video_records(payload) -> list | None:
    if isinstance(payload, list):
        return payload
    paths = (
        ("data", "records"),
        ("data", "list"),
        ("data", "rows"),
        ("data", "items"),
        ("data", "page", "records"),
        ("data", "page", "list"),
        ("body", "list"),
        ("body",),
        ("data",),
    )
    for path in paths:
        value = _nested_value(payload, path)
        if isinstance(value, list):
            return value
    return None


def _nested_value(obj, path: tuple[str, ...]):
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _payload_summary(payload) -> str:
    if isinstance(payload, dict):
        return str(
            {
                "code": payload.get("code"),
                "message": payload.get("message") or payload.get("msg"),
                "data_type": type(payload.get("data")).__name__,
                "body_type": type(payload.get("body")).__name__,
                "result_type": type(payload.get("result")).__name__,
                "keys": sorted(str(key) for key in payload.keys())[:12],
            }
        )
    return type(payload).__name__


def _node_has_video(node: dict) -> bool:
    return any(_looks_like_video_url(value) for value in _iter_values(node))


def _assets_from_node(node: dict) -> Iterator[VideoAsset]:
    for kind, keys in (
        ("课堂", ("classVideoUrl", "cameraUrl", "teacherVideoUrl", "videoUrl", "playUrl")),
        ("录屏", ("screenVideoUrl", "screenUrl", "pptVideoUrl", "recordUrl", "recordingUrl")),
    ):
        url = _first_video_url(node, keys)
        if not url:
            continue
        asset_id = str(_first_value(node, ("resourceId", "videoId", "id")) or _stable_id(f"{kind}:{url}"))
        yield VideoAsset(
            kind=kind,
            title=kind,
            url=url,
            asset_id=f"{kind}:{asset_id}",
            extension=_extension_from_url(url),
            size=_optional_int(_first_value(node, ("size", "fileSize", "videoSize"))),
            modified_at=_optional_str(_first_value(node, ("updatedAt", "modifiedAt", "updateTime"))),
        )


def _first_video_url(node: dict, preferred_keys: tuple[str, ...]) -> str | None:
    for key in preferred_keys:
        value = node.get(key)
        if isinstance(value, str) and _looks_like_video_url(value):
            return value
    for key, value in node.items():
        if isinstance(value, str) and _looks_like_video_url(value):
            lowered = key.lower()
            if any(marker in lowered for marker in ("url", "play", "video", "m3u8", "mp4")):
                return value
    return None


def _first_value(node: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in node and node[key] not in (None, ""):
            return node[key]
    return None


def _looks_like_video_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("https://") and any(part in lowered for part in (".mp4", ".m3u8", "/m3u8"))


def _is_hls(url: str) -> bool:
    return ".m3u8" in url.lower()


def _extension_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    if ".m3u8" in path:
        return ".ts"
    suffix = Path(path).suffix
    return suffix if suffix else ".mp4"


def _asset_path(
    course_dir: Path,
    lecture: VideoLecture,
    asset: VideoAsset,
    used_paths: set[Path],
) -> Path:
    stem = sanitize_name(f"{lecture.index}-{lecture.title}-{asset.kind}", max_len=140)
    suffix = asset.extension if asset.extension.startswith(".") else f".{asset.extension}"
    candidate = course_dir / f"{stem}{suffix}"
    if candidate not in used_paths:
        used_paths.add(candidate)
        return candidate
    for index in range(2, len(used_paths) + 3):
        candidate = course_dir / f"{stem}_{index}{suffix}"
        if candidate not in used_paths:
            used_paths.add(candidate)
            return candidate
    raise VideoError("视频下载失败：无法为视频文件生成唯一文件名。")


def _state_key(course_id: int, lecture: VideoLecture, asset: VideoAsset) -> str:
    return f"videos:sjtu:{course_id}:{lecture.lecture_id}:{asset.asset_id}"


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value) -> str | None:
    return None if value in (None, "") else str(value)

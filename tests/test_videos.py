from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from canvas_dl.events import FileProgressStarted, LogEvent
from canvas_dl.paths import AppPaths
from canvas_dl.videos import (
    SjtuiVsProvider,
    VideoAsset,
    VideoEntry,
    VideoError,
    VideoLinkDiscovery,
    VideoLecture,
    VideoRunOptions,
    VideoService,
    apply_session_cookies,
    extract_canvas_external_tool_links,
    extract_sjtu_video_links,
    parse_lecture_filter,
    parse_sjtu_token,
    _asset_path,
)
from canvas_dl.browser_cookies import _domains_for_urls


class _Reporter:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def test_parse_lecture_filter_matches_ranges() -> None:
    parsed = parse_lecture_filter("1-3,7,10")

    assert parsed.matches(1)
    assert parsed.matches(3)
    assert parsed.matches(7)
    assert not parsed.matches(8)
    assert parsed.matches(10)


@pytest.mark.parametrize("value", ["0", "3-1", "a", "1,,2"])
def test_parse_lecture_filter_rejects_invalid(value: str) -> None:
    with pytest.raises(RuntimeError):
        parse_lecture_filter(value)


def test_extract_sjtu_video_links_and_token() -> None:
    link = (
        "https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/"
        "#/ivsModules/index?tokenId=abc123"
    )
    html = f'<a href="{link}">video</a><a href="{link}">dup</a>'

    links = extract_sjtu_video_links(html)

    assert links == [link]
    assert parse_sjtu_token(links[0]) == "abc123"


def test_extract_sjtu_video_links_handles_json_escaped_slashes() -> None:
    escaped = (
        "https:\\/\\/v.sjtu.edu.cn\\/jy-application-canvas-sjtu-ui\\/"
        "#\\/ivsModules\\/index?tokenId=abc123"
    )

    links = extract_sjtu_video_links(escaped)

    assert len(links) == 1
    assert parse_sjtu_token(links[0]) == "abc123"


class _Response:
    def __init__(
        self,
        payload=None,
        headers=None,
        status_code=200,
        url="https://oc.sjtu.edu.cn/test",
        text="",
        history=None,
    ) -> None:
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status_code
        self.url = url
        self.text = text
        self.history = history or []

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._payload


class _ProviderSession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=30, **kwargs):
        self.calls.append(("GET", url, params, kwargs))
        return self.responses.pop(0)

    def post(self, url, data=None, json=None, headers=None, timeout=30, **kwargs):
        self.calls.append(("POST", url, data or json, headers or {}))
        return self.responses.pop(0)


class _Session:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.urls = []
        self.headers = {}
        self.cookies = requests.Session().cookies

    def get(self, url, params=None, timeout=30, **kwargs):
        self.urls.append((url, params, timeout, kwargs))
        if not self.responses:
            return _Response(status_code=404, url=url)
        return self.responses.pop(0)


def test_discovery_reads_canvas_rest_modules_and_page_bodies() -> None:
    link = (
        "https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/"
        "#/ivsModules/index?tokenId=rest-token"
    )
    session = _Session(
        [
            _Response([{"name": "module", "items": [{"external_url": link}]}]),
            _Response([]),
            _Response([]),
            _Response(status_code=404),
            _Response(status_code=404),
            _Response([{"url": "intro"}]),
            _Response({"body": f'<iframe src="{link}"></iframe>'}),
        ]
    )
    client = SimpleNamespace(
        session=session,
        config=SimpleNamespace(canvas_url="https://oc.sjtu.edu.cn", request_delay=0),
    )
    course = SimpleNamespace(
        id=87629,
        get_pages=lambda: [],
        get_modules=lambda: [],
    )

    entries = VideoLinkDiscovery(client).discover(course)

    assert [entry.token_id for entry in entries] == ["rest-token"]
    assert any("/modules" in url for url, _params, _timeout, _kwargs in session.urls)


def test_extract_canvas_external_tool_links() -> None:
    links = extract_canvas_external_tool_links(
        '<a href="/courses/87629/external_tools/8329?display=borderless">课堂视频</a>',
        "https://oc.sjtu.edu.cn",
    )

    assert links == ["https://oc.sjtu.edu.cn/courses/87629/external_tools/8329?display=borderless"]


def test_discovery_follows_canvas_external_tool_redirect() -> None:
    final_url = (
        "https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/"
        "#/ivsModules/index?tokenId=tool-token"
    )
    session = _Session(
        [
            _Response([{"items": [{"url": "/courses/87629/external_tools/8329?display=borderless"}]}]),
            _Response([]),
            _Response([]),
            _Response(status_code=404),
            _Response(status_code=404),
            _Response([]),
            _Response(status_code=200, url=final_url, headers={"Content-Type": "text/html"}),
        ]
    )
    client = SimpleNamespace(
        session=session,
        config=SimpleNamespace(canvas_url="https://oc.sjtu.edu.cn", request_delay=0),
    )
    course = SimpleNamespace(
        id=87629,
        get_pages=lambda: [],
        get_modules=lambda: [],
    )

    entries = VideoLinkDiscovery(client).discover(course)

    assert [entry.token_id for entry in entries] == ["tool-token"]
    assert any(
        url == "https://oc.sjtu.edu.cn/courses/87629/external_tools/8329?display=borderless"
        and kwargs.get("allow_redirects") is True
        for url, _params, _timeout, kwargs in session.urls
    )


def test_discovery_accepts_manual_external_tool_url() -> None:
    final_url = (
        "https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/"
        "#/ivsModules/index?tokenId=manual-token"
    )
    session = _Session(
        [
            _Response(status_code=200, url=final_url, headers={"Content-Type": "text/html"}),
        ]
    )
    client = SimpleNamespace(
        session=session,
        config=SimpleNamespace(canvas_url="https://oc.sjtu.edu.cn", request_delay=0),
    )

    entries = VideoLinkDiscovery(client).entries_from_urls(
        ["https://oc.sjtu.edu.cn/courses/87629/external_tools/8329?display=borderless"]
    )

    assert [entry.token_id for entry in entries] == ["manual-token"]


def test_manual_external_tool_login_redirect_records_error() -> None:
    session = _Session(
        [
            _Response(
                status_code=200,
                url="https://oc.sjtu.edu.cn/login/canvas",
                headers={"Content-Type": "text/html"},
                text="<html>login</html>",
            ),
        ]
    )
    client = SimpleNamespace(
        session=session,
        config=SimpleNamespace(canvas_url="https://oc.sjtu.edu.cn", request_delay=0),
    )
    discovery = VideoLinkDiscovery(client)

    entries = discovery.entries_from_urls(
        ["https://oc.sjtu.edu.cn/courses/87629/external_tools/8329?display=borderless"]
    )

    assert entries == []
    assert "Canvas 登录页" in discovery.last_resolution_errors[0]


def test_browser_cookie_domains_include_sjtu_hosts() -> None:
    domains = _domains_for_urls(
        ["https://oc.sjtu.edu.cn/courses/87629/external_tools/8329?display=borderless"]
    )

    assert domains[:3] == ["oc.sjtu.edu.cn", "jaccount.sjtu.edu.cn", "v.sjtu.edu.cn"]


def test_apply_session_cookies_adds_cookies_to_requests_session() -> None:
    session = requests.Session()

    apply_session_cookies(
        session,
        [{"name": "canvas_session", "value": "secret", "domain": "oc.sjtu.edu.cn", "path": "/"}],
    )

    assert session.cookies.get("canvas_session", domain="oc.sjtu.edu.cn", path="/") == "secret"


class _HeadStub:
    """Minimal HEAD response for VideoDownloader._probe_range_support."""

    def __init__(self, *, status_code: int = 200, headers=None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


def test_video_downloader_retries_transient_stream_failure(tmp_path: Path) -> None:
    from canvas_dl.videos import VideoDownloader

    class _StreamResponse:
        def __init__(self) -> None:
            self.headers = {"Content-Type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            yield b"video"

    class _StreamSession:
        def __init__(self) -> None:
            self.get_calls = 0
            self.head_calls = 0

        def head(self, url, **kwargs):
            self.head_calls += 1
            # No Accept-Ranges → fall back to single-stream path.
            return _HeadStub()

        def get(self, *args, **kwargs):
            self.get_calls += 1
            if self.get_calls == 1:
                raise requests.exceptions.ConnectionError("temporary network failure")
            return _StreamResponse()

    session = _StreamSession()
    downloader = VideoDownloader(session=session, retry_base_delay=0)
    asset = VideoAsset(
        kind="课堂",
        title="课堂",
        url="https://v.sjtu.edu.cn/media/class.mp4",
        asset_id="class",
    )

    downloader.download(asset, tmp_path / "class.mp4")

    assert (tmp_path / "class.mp4").read_bytes() == b"video"
    assert session.get_calls == 2


def _make_range_response(body: bytes, status: int = 206, headers=None):
    class _RangeResp:
        def __init__(self, body, status, headers) -> None:
            self._body = body
            self.status_code = status
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(f"status {self.status_code}")

        def iter_content(self, chunk_size: int):
            for i in range(0, len(self._body), chunk_size):
                yield self._body[i : i + chunk_size]

    return _RangeResp(body, status, headers)


def test_download_stream_uses_range_when_supported(tmp_path: Path) -> None:
    from canvas_dl.videos import VideoDownloader

    full = b"".join(bytes([i % 256]) for i in range(5 * 1024 * 1024 + 1))

    class _RangeSession:
        def __init__(self) -> None:
            self.head_calls = []
            self.get_calls = []

        def head(self, url, **kwargs):
            self.head_calls.append(url)
            return _HeadStub(
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(full)),
                }
            )

        def get(self, url, headers=None, stream=False, timeout=60, **kwargs):
            self.get_calls.append((url, headers))
            rng = (headers or {}).get("Range", "")
            assert rng.startswith("bytes="), "Range path should always send a Range header"
            a, b = rng[len("bytes=") :].split("-")
            return _make_range_response(full[int(a) : int(b) + 1], status=206)

    session = _RangeSession()
    downloader = VideoDownloader(session=session, retry_base_delay=0)
    asset = VideoAsset(
        kind="课堂",
        title="课堂",
        url="https://v.sjtu.edu.cn/media/class.mp4",
        asset_id="class",
    )

    downloader.download(asset, tmp_path / "out.mp4", workers=4)

    assert (tmp_path / "out.mp4").read_bytes() == full
    assert len(session.head_calls) == 1
    range_gets = [c for c in session.get_calls if (c[1] or {}).get("Range")]
    assert len(range_gets) == 4
    # No leftover temp dir / .part
    assert not list(tmp_path.glob("*.parts"))
    assert not list(tmp_path.glob("*.part"))


def test_download_stream_falls_back_when_no_range(tmp_path: Path) -> None:
    from canvas_dl.videos import VideoDownloader

    body = b"X" * (1024 * 1024)  # 1 MB — below 2MB range threshold anyway

    class _NoRangeSession:
        def __init__(self) -> None:
            self.get_calls = []

        def head(self, url, **kwargs):
            return _HeadStub(headers={"Content-Length": str(len(body))})

        def get(self, url, **kwargs):
            self.get_calls.append((url, kwargs.get("headers")))
            return _make_range_response(
                body,
                status=200,
                headers={"Content-Type": "video/mp4", "Content-Length": str(len(body))},
            )

    session = _NoRangeSession()
    downloader = VideoDownloader(session=session, retry_base_delay=0)
    asset = VideoAsset(
        kind="课堂",
        title="课堂",
        url="https://v.sjtu.edu.cn/media/class.mp4",
        asset_id="class",
    )

    downloader.download(asset, tmp_path / "out.mp4", workers=4)

    assert (tmp_path / "out.mp4").read_bytes() == body
    assert len(session.get_calls) == 1
    assert (session.get_calls[0][1] or {}).get("Range") is None


def test_download_hls_parallel(tmp_path: Path) -> None:
    from canvas_dl.videos import VideoDownloader

    playlist = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXTINF:5.0,\nseg0.ts\n"
        "#EXTINF:5.0,\nseg1.ts\n"
        "#EXTINF:5.0,\nseg2.ts\n"
        "#EXTINF:5.0,\nseg3.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    segments = {
        "https://v.sjtu.edu.cn/hls/seg0.ts": b"AAAA",
        "https://v.sjtu.edu.cn/hls/seg1.ts": b"BBBB",
        "https://v.sjtu.edu.cn/hls/seg2.ts": b"CCCC",
        "https://v.sjtu.edu.cn/hls/seg3.ts": b"DDDD",
    }

    class _TextResp:
        def __init__(self, text: str) -> None:
            self.text = text
            self.status_code = 200
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

    class _HlsSession:
        def __init__(self) -> None:
            self.urls = []

        def get(self, url, **kwargs):
            self.urls.append(url)
            if url.endswith(".m3u8"):
                return _TextResp(playlist)
            return _make_range_response(segments[url], status=200)

    session = _HlsSession()
    downloader = VideoDownloader(session=session, retry_base_delay=0)
    asset = VideoAsset(
        kind="录屏",
        title="录屏",
        url="https://v.sjtu.edu.cn/hls/main.m3u8",
        asset_id="hls1",
    )

    downloader.download(asset, tmp_path / "out.ts", workers=4)

    # Segments must be concatenated in playlist order, irrespective of completion order.
    assert (tmp_path / "out.ts").read_bytes() == b"AAAABBBBCCCCDDDD"
    # All 4 segments fetched (plus 1 playlist GET = 5 total)
    seg_gets = [u for u in session.urls if u.endswith(".ts")]
    assert sorted(seg_gets) == sorted(segments.keys())
    assert not list(tmp_path.glob("*.segs"))


def test_download_hls_rejects_lowercase_key_tag() -> None:
    from canvas_dl.videos import VideoDownloader

    downloader = VideoDownloader(session=object(), retry_base_delay=0)
    playlist = (
        "#EXTM3U\n"
        "#ext-x-key:METHOD=AES-128,URI=\"key.bin\"\n"
        "#EXTINF:5.0,\nseg0.ts\n"
    )

    with pytest.raises(VideoError, match="加密 m3u8"):
        downloader._parse_hls_segments("https://v.sjtu.edu.cn/hls/main.m3u8", playlist)


def test_asset_path_resolves_many_collisions_with_finite_search(tmp_path: Path) -> None:
    lecture = VideoLecture(index=1, title="Intro", lecture_id="l1")
    asset = VideoAsset(
        kind="课堂",
        title="课堂",
        url="https://v.sjtu.edu.cn/media/class.mp4",
        asset_id="class",
    )
    used_paths = {tmp_path / "1-Intro-课堂.mp4"}
    used_paths.update(tmp_path / f"1-Intro-课堂_{i}.mp4" for i in range(2, 25))

    path = _asset_path(tmp_path, lecture, asset, used_paths)

    assert path.name == "1-Intro-课堂_25.mp4"
    assert path in used_paths


def test_download_progress_reports_bytes_and_total(tmp_path: Path) -> None:
    from canvas_dl.videos import VideoDownloader

    full = b"Y" * (3 * 1024 * 1024)  # > 2MB threshold → ranges path

    class _Session:
        def head(self, url, **kwargs):
            return _HeadStub(
                headers={"Accept-Ranges": "bytes", "Content-Length": str(len(full))}
            )

        def get(self, url, headers=None, **kwargs):
            rng = (headers or {}).get("Range", "")
            a, b = rng[len("bytes=") :].split("-")
            return _make_range_response(full[int(a) : int(b) + 1], status=206)

    samples: list[tuple[int, int]] = []

    def cb(downloaded: int, total: int) -> None:
        samples.append((downloaded, total))

    downloader = VideoDownloader(
        session=_Session(),
        retry_base_delay=0,
        progress_throttle_seconds=0.0,  # emit every chunk for the test
    )
    asset = VideoAsset(
        kind="课堂",
        title="课堂",
        url="https://v.sjtu.edu.cn/media/class.mp4",
        asset_id="class",
    )

    downloader.download(asset, tmp_path / "out.mp4", workers=3, progress_cb=cb)

    # Progress must be monotonically non-decreasing and end at total.
    assert samples, "progress_cb should fire at least once"
    last_downloaded = 0
    for d, t in samples:
        assert t == len(full)
        assert d >= last_downloaded
        last_downloaded = d
    assert samples[-1] == (len(full), len(full))


def test_download_hls_cancels_promptly(tmp_path: Path) -> None:
    """Cancelling mid-download must clean up and raise, with no .part / .segs leaks."""
    from canvas_dl.videos import VideoDownloader, _DownloadCancelled
    from canvas_dl.service import CancelToken

    playlist = (
        "#EXTM3U\n#EXT-X-VERSION:3\n"
        + "".join(f"#EXTINF:5.0,\nseg{i}.ts\n" for i in range(20))
        + "#EXT-X-ENDLIST\n"
    )
    cancel = CancelToken()

    served_lock = __import__("threading").Lock()
    served = {"count": 0}

    class _TextResp:
        def __init__(self, text):
            self.text = text
            self.status_code = 200
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

    class _SlowSession:
        def get(self, url, **kwargs):
            if url.endswith(".m3u8"):
                return _TextResp(playlist)
            with served_lock:
                served["count"] += 1
                if served["count"] >= 2:
                    cancel.cancel()
            return _make_range_response(b"X" * 1024, status=200)

    downloader = VideoDownloader(session=_SlowSession(), retry_base_delay=0)
    asset = VideoAsset(
        kind="录屏",
        title="录屏",
        url="https://v.sjtu.edu.cn/hls/main.m3u8",
        asset_id="hls1",
    )

    with pytest.raises(_DownloadCancelled):
        downloader.download(asset, tmp_path / "out.ts", workers=4, cancel_token=cancel)

    assert not (tmp_path / "out.ts").exists()
    assert not list(tmp_path.glob("*.segs"))
    assert not list(tmp_path.glob("*.part"))


def test_video_service_runs_videos_concurrently(tmp_path: Path) -> None:
    """K=2 means two videos must enter the downloader simultaneously."""
    import threading

    barrier = threading.Barrier(2, timeout=5.0)

    class _StubDownloader:
        def __init__(self) -> None:
            self.session = requests.Session()
            self.calls: list[str] = []
            self._lock = threading.Lock()

        def download(self, asset, dest, **kwargs):
            with self._lock:
                self.calls.append(asset.asset_id)
            # Both workers must reach here within the barrier timeout.
            barrier.wait()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"data")

    course = SimpleNamespace(id=87629, name="C1")
    config = SimpleNamespace(
        canvas_url="https://oc.sjtu.edu.cn",
        download_dir=tmp_path / "videos",
        dry_run=False,
        request_delay=0,
    )
    client = SimpleNamespace(get_courses=lambda: [course])
    discovery = SimpleNamespace(
        discover=lambda _course: [
            VideoEntry(
                url="https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/#/ivsModules/index?tokenId=t",
                token_id="t",
            )
        ]
    )
    provider = SimpleNamespace(
        list_lectures=lambda _entry: [
            VideoLecture(
                index=i,
                title=f"L{i}",
                lecture_id=f"l{i}",
                assets=(
                    VideoAsset(
                        kind="课堂",
                        title="t",
                        url=f"https://v.sjtu.edu.cn/{i}.mp4",
                        asset_id=f"a{i}",
                    ),
                ),
            )
            for i in (1, 2)
        ]
    )
    stub = _StubDownloader()
    service = VideoService(
        config,
        AppPaths(tmp_path),
        client,
        discovery,
        provider,
        stub,
    )
    reporter = _Reporter()

    downloaded = service.run(
        VideoRunOptions(
            only_courses=[course.id],
            max_concurrent_videos=2,
            max_workers_per_video=1,
        ),
        reporter,
    )

    assert downloaded == 2
    assert sorted(stub.calls) == ["a1", "a2"]


def test_jaccount_extracts_uuid_from_firefox_link() -> None:
    from canvas_dl.jaccount_qr import _extract_uuid

    html = '<a id="firefox_link" href="jaccount://qrlogin?uuid=abc-123">open</a>'

    assert _extract_uuid(html) == "abc-123"


def test_jaccount_extracts_openid_connect_link_from_canvas_login() -> None:
    from canvas_dl.jaccount_qr import _extract_jaccount_link

    html = '<a class="Button" href="/login/openid_connect">校内用户登录</a>'

    assert (
        _extract_jaccount_link(html, "https://oc.sjtu.edu.cn/login/canvas")
        == "https://oc.sjtu.edu.cn/login/openid_connect"
    )


def test_jaccount_extracts_direct_jalogin_link() -> None:
    from canvas_dl.jaccount_qr import _extract_jaccount_link

    html = '<a href="https://jaccount.sjtu.edu.cn/jaccount/jalogin?sid=x&amp;client=y">校内用户登录</a>'

    assert _extract_jaccount_link(html, "https://oc.sjtu.edu.cn/login/canvas").startswith(
        "https://jaccount.sjtu.edu.cn/jaccount/jalogin"
    )


def test_simple_websocket_rejects_non_wss_url() -> None:
    from canvas_dl.jaccount_qr import JAccountQrError, _SimpleWebSocket

    with pytest.raises(JAccountQrError):
        _SimpleWebSocket.connect("https://jaccount.sjtu.edu.cn/jaccount/sub/x")


def test_resolve_video_url_retries_external_tool_after_canvas_login() -> None:
    from canvas_dl.jaccount_qr import resolve_video_url

    class _LoginSession:
        def __init__(self) -> None:
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if len(self.calls) == 1:
                return _Response(
                    status_code=200,
                    url="https://oc.sjtu.edu.cn/login/canvas",
                    text='<a href="/login/openid_connect">校内用户登录</a>',
                )
            if len(self.calls) == 2:
                return _Response(status_code=200, url="https://oc.sjtu.edu.cn/")
            return _Response(
                status_code=200,
                url="https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/#/ivsModules/index?tokenId=ok",
            )

    session = _LoginSession()

    url = resolve_video_url(session, "https://oc.sjtu.edu.cn/courses/87629/external_tools/8329")

    assert "tokenId=ok" in url
    assert len(session.calls) == 3


def test_resolve_video_url_submits_lti_auth_form_for_token() -> None:
    from canvas_dl.jaccount_qr import resolve_video_url

    class _LtiSession:
        def __init__(self) -> None:
            self.posts = []

        def get(self, url, **kwargs):
            return _Response(
                status_code=200,
                url="https://v.sjtu.edu.cn/jy-application-canvas-sjtu/oidc/login_initiations",
                text=(
                    '<form action="https://v.sjtu.edu.cn/jy-application-canvas-sjtu/lti3/lti3Auth/ivs">'
                    '<input name="state" value="s"><input name="id_token" value="i"></form>'
                ),
            )

        def post(self, url, data=None, **kwargs):
            self.posts.append((url, data, kwargs))
            return _Response(
                status_code=302,
                url=url,
                headers={
                    "Location": "https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/#/ivsModules/index?tokenId=form-token"
                },
            )

    session = _LtiSession()

    url = resolve_video_url(session, "https://oc.sjtu.edu.cn/courses/87629/external_tools/8329")

    assert "tokenId=form-token" in url
    assert session.posts[0][1] == {"state": "s", "id_token": "i"}


def test_scan_canvas_video_courses_keeps_tool_url_without_resolving_token() -> None:
    from canvas_dl.jaccount_qr import scan_canvas_video_courses

    class _ScanSession:
        def __init__(self) -> None:
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return _Response(
                status_code=200,
                url=url,
                text='<a href="/courses/87629/external_tools/8329?display=borderless">课堂视频</a>',
            )

    session = _ScanSession()

    courses = scan_canvas_video_courses(session, [{"id": 87629, "name": "大学物理"}])

    assert courses == [
        {
            "id": 87629,
            "name": "大学物理",
            "tool_url": "https://oc.sjtu.edu.cn/courses/87629/external_tools/8329?display=borderless",
        }
    ]
    assert len(session.calls) == 1


def test_scan_canvas_video_courses_honors_cancel_token() -> None:
    from canvas_dl.jaccount_qr import scan_canvas_video_courses

    class _Cancelled:
        def is_cancelled(self) -> bool:
            return True

    class _ScanSession:
        def get(self, url, **kwargs):
            raise AssertionError("cancelled scan should not issue requests")

    courses = scan_canvas_video_courses(
        _ScanSession(),
        [{"id": 87629, "name": "大学物理"}],
        _Cancelled(),
    )

    assert courses == []


def test_provider_parses_two_video_assets_from_payload() -> None:
    payload = {
        "data": {
            "modules": [
                {
                    "id": "m1",
                    "index": 2,
                    "title": "绪论",
                    "cameraUrl": "https://v.sjtu.edu.cn/media/camera.mp4",
                    "screenUrl": "https://v.sjtu.edu.cn/media/screen.m3u8",
                }
            ]
        }
    }
    provider = SjtuiVsProvider()

    lectures = list(provider._parse_lectures(payload))

    assert len(lectures) == 1
    assert lectures[0].index == 2
    assert lectures[0].title == "绪论"
    assert [asset.kind for asset in lectures[0].assets] == ["课堂", "录屏"]


def test_provider_uses_direct_on_demand_api_from_token() -> None:
    session = _ProviderSession(
        [
            _Response({"data": {"token": "access-token", "params": {"courId": "course-1"}}}),
            _Response({"data": {"list": [{"videoId": "v1", "courName": "第一讲"}]}}),
            _Response(
                {
                    "data": {
                        "id": "detail-1",
                        "courName": "第一讲",
                        "videoPlayResponseVoList": [
                            {"id": "a0", "cdviViewNum": 0, "rtmpUrlHdv": "https://v.sjtu.edu.cn/a0.mp4"},
                            {"id": "a1", "cdviViewNum": 1, "rtmpUrlHdv": "https://v.sjtu.edu.cn/a1.mp4"},
                        ],
                    }
                }
            ),
        ]
    )
    provider = SjtuiVsProvider(session)

    lectures = provider.list_lectures(
        VideoEntry(
            url="https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/#/ivsModules/index?tokenId=t",
            token_id="t",
        )
    )

    assert len(lectures) == 1
    assert lectures[0].title == "第一讲"
    assert [asset.kind for asset in lectures[0].assets] == ["课堂", "录屏"]
    assert any("getAccessTokenByTokenId" in call[1] for call in session.calls)
    assert any("findVodVideoList" in call[1] for call in session.calls)
    assert any("getVodVideoInfos" in call[1] for call in session.calls)


def test_provider_accepts_nested_token_payload() -> None:
    session = _ProviderSession(
        [
            _Response({"result": {"data": {"accessToken": "access-token", "courId": "course-1"}}}),
            _Response({"body": {"list": [{"videoId": "v1", "courName": "第一讲"}]}}),
            _Response(
                {
                    "body": {
                        "courName": "第一讲",
                        "videoPlayResponseVoList": [
                            {"id": "a0", "cdviViewNum": 0, "rtmpUrlHdv": "https://v.sjtu.edu.cn/a0.mp4"},
                        ],
                    }
                }
            ),
        ]
    )
    provider = SjtuiVsProvider(session)

    lectures = provider.list_lectures(
        VideoEntry(
            url="https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/#/ivsModules/index?tokenId=t",
            token_id="t",
        )
    )

    assert len(lectures) == 1
    assert lectures[0].assets[0].url == "https://v.sjtu.edu.cn/a0.mp4"


def test_video_service_dry_run_uses_provider_and_filters_lectures(tmp_path: Path) -> None:
    course = SimpleNamespace(id=87629, name="大学物理")
    config = SimpleNamespace(
        canvas_url="https://oc.sjtu.edu.cn",
        download_dir=tmp_path / "videos",
        dry_run=True,
        request_delay=0,
    )
    client = SimpleNamespace(get_courses=lambda: [course])
    discovery = SimpleNamespace(
        discover=lambda _course: [
            VideoEntry(
                url="https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/#/ivsModules/index?tokenId=redacted",
                token_id="redacted",
            )
        ]
    )
    provider = SimpleNamespace(
        list_lectures=lambda _entry: [
            VideoLecture(
                index=1,
                title="第一讲",
                lecture_id="l1",
                assets=(
                    VideoAsset(
                        kind="课堂",
                        title="课堂",
                        url="https://v.sjtu.edu.cn/media/class.mp4",
                        asset_id="class",
                    ),
                    VideoAsset(
                        kind="录屏",
                        title="录屏",
                        url="https://v.sjtu.edu.cn/media/screen.mp4",
                        asset_id="screen",
                    ),
                ),
            ),
            VideoLecture(
                index=2,
                title="第二讲",
                lecture_id="l2",
                assets=(
                    VideoAsset(
                        kind="课堂",
                        title="课堂",
                        url="https://v.sjtu.edu.cn/media/class2.mp4",
                        asset_id="class2",
                    ),
                ),
            ),
        ]
    )
    reporter = _Reporter()
    service = VideoService(config, AppPaths(tmp_path), client, discovery, provider)

    downloaded = service.run(
        VideoRunOptions(dry_run=True, lecture_filter=parse_lecture_filter("1")),
        reporter,
    )

    assert downloaded == 0
    assert any(isinstance(event, FileProgressStarted) and event.total == 2 for event in reporter.events)
    dry_logs = [event.message for event in reporter.events if isinstance(event, LogEvent) and "[DRY]" in event.message]
    assert len(dry_logs) == 2
    assert all("第一讲" in message for message in dry_logs)


def test_video_service_per_course_lectures_overrides_global_filter(tmp_path: Path) -> None:
    course = SimpleNamespace(id=87629, name="操作系统")
    config = SimpleNamespace(
        canvas_url="https://oc.sjtu.edu.cn",
        download_dir=tmp_path / "videos",
        dry_run=True,
        request_delay=0,
    )
    client = SimpleNamespace(get_courses=lambda: [course])
    discovery = SimpleNamespace(
        discover=lambda _course: [
            VideoEntry(
                url="https://v.sjtu.edu.cn/jy-application-canvas-sjtu-ui/#/ivsModules/index?tokenId=t",
                token_id="t",
            )
        ]
    )
    provider = SimpleNamespace(
        list_lectures=lambda _entry: [
            VideoLecture(
                index=i,
                title=f"第{i}节",
                lecture_id=f"l{i}",
                assets=(
                    VideoAsset(
                        kind="课堂",
                        title="课堂",
                        url=f"https://v.sjtu.edu.cn/media/{i}.mp4",
                        asset_id=f"a{i}",
                    ),
                ),
            )
            for i in range(1, 6)
        ]
    )
    reporter = _Reporter()
    service = VideoService(config, AppPaths(tmp_path), client, discovery, provider)

    service.run(
        VideoRunOptions(
            dry_run=True,
            only_courses=[course.id],
            # Global filter would let 1,2,3 through; per_course_lectures should win.
            lecture_filter=parse_lecture_filter("1-3"),
            per_course_lectures={course.id: {2, 4}},
        ),
        reporter,
    )

    dry_logs = [
        event.message for event in reporter.events
        if isinstance(event, LogEvent) and "[DRY]" in event.message
    ]
    assert len(dry_logs) == 2
    assert any("2-第2节" in message for message in dry_logs)
    assert any("4-第4节" in message for message in dry_logs)
    for forbidden in ("1-第1节", "3-第3节", "5-第5节"):
        assert all(forbidden not in message for message in dry_logs)

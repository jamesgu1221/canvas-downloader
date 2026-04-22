import time
import os
import requests
from pathlib import Path
from canvasapi import Canvas
from canvasapi.exceptions import ResourceDoesNotExist
from .config import AppConfig


def safe_course_name(course):
    """Return course.name, or None if the course is inaccessible.

    access_restricted=True courses: `getattr(course, "name", None)` does NOT
    help because canvasapi may raise ResourceDoesNotExist (not AttributeError)
    when reading `.name`. Check the flag first, then guard both exceptions.
    """
    if getattr(course, "access_restricted", False):
        return None
    try:
        return course.name
    except (AttributeError, ResourceDoesNotExist):
        return None


class CanvasClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.canvas = Canvas(config.canvas_url, config.api_token)
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {config.api_token}"

    def get_courses(self):
        try:
            user = self.canvas.get_current_user()
            courses = list(user.get_courses(enrollment_state="active"))
        except Exception as e:
            raise SystemExit(f"获取课程列表失败：{e}\n请检查 API token 和 Canvas URL 是否正确。")
        return courses

    def get_course_folders(self, course):
        time.sleep(self.config.request_delay)
        try:
            return list(course.get_folders())
        except ResourceDoesNotExist:
            return []

    def get_folder_files(self, folder):
        time.sleep(self.config.request_delay)
        try:
            return list(folder.get_files())
        except ResourceDoesNotExist:
            return []

    def get_subfolders(self, folder):
        time.sleep(self.config.request_delay)
        try:
            return list(folder.get_folders())
        except ResourceDoesNotExist:
            return []

    def download_file(self, url: str, dest: Path) -> None:
        """Stream-download url to dest. Raises on failure."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")

        def _write_body(resp):
            resp.raise_for_status()
            # SSO 重定向可能返回 200 HTML 登录页，不应写入文件
            # #23: broaden SSO-page detection to cover xhtml and case variants
            ct = resp.headers.get("Content-Type", "").lower()
            if ct.startswith(("text/html", "application/xhtml")):
                raise RuntimeError(
                    f"下载失败：服务器返回 HTML（可能是登录页），Content-Type={ct!r}"
                )
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

        try:
            needs_token = False
            for attempt in range(3):
                try:
                    if not needs_token:
                        with self.session.get(url, stream=True, timeout=60, allow_redirects=True) as resp:
                            # #23: detect SSO redirects by URL keyword, not just /login
                            sso_redirect = any(k in resp.url for k in ("/login", "/sso", "/cas", "/saml", "/oauth"))
                            if resp.status_code in (401, 403) or sso_redirect:
                                # 登录重定向：后续所有重试直接带 access_token，跳过首次探测
                                needs_token = True
                            else:
                                try:
                                    _write_body(resp)
                                except RuntimeError:
                                    # 首探返回 200 + text/html，通常是 SSO 把下载 URL
                                    # 渲染成登录页（状态码无法识别）。切到 token 模式再试一次；
                                    # 此时 part 还未被打开，无需额外清理。
                                    needs_token = True
                                    continue
                                os.replace(part, dest)
                                return
                    if needs_token:
                        with self.session.get(
                            url,
                            stream=True,
                            timeout=60,
                            allow_redirects=True,
                            params={"access_token": self.config.api_token},
                        ) as resp:
                            # 带 token 仍返回 HTML → 真正的认证失败或文件被锁；
                            # RuntimeError 在此不可恢复，直接抛出给上层。
                            _write_body(resp)
                        os.replace(part, dest)
                        return
                except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as exc:
                    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None and exc.response.status_code < 500:
                        raise
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise
        finally:
            if part.exists():
                part.unlink()

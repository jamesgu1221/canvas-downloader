from dataclasses import dataclass
from pathlib import Path
import shutil
import uuid

import pytest

from canvas_dl.client import CanvasClient
from canvas_dl.config import AppConfig


@dataclass
class FakeResponse:
    headers: dict
    body: bytes
    url: str = "https://canvas.example.edu/files/1"
    status_code: int = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        yield self.body


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


def make_client(response):
    config = AppConfig(
        api_token="tok",
        canvas_url="https://canvas.example.edu",
        download_dir=Path("."),
    )
    client = CanvasClient.__new__(CanvasClient)
    client.config = config
    client.session = FakeSession(response)
    client._canvas_origin = client._origin(config.canvas_url)
    return client


def test_download_file_rejects_html_body_with_text_plain_content_type():
    root = Path(".test_tmp") / f"client_{uuid.uuid4().hex}"
    try:
        response = FakeResponse(
            headers={"Content-Type": "text/plain"},
            body=b"  <!doctype html><html><body>login</body></html>",
        )
        client = make_client(response)
        dest = root / "file.pdf"

        with pytest.raises(RuntimeError, match="HTML"):
            client.download_file("https://canvas.example.edu/files/1", dest)

        assert not dest.exists()
        assert not dest.with_suffix(".pdf.part").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)

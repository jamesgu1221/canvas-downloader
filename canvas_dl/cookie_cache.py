"""Windows DPAPI Cookie 缓存。

用当前用户登录凭证加密/解密 cookie，供 headless 自动下载使用。
通过 ctypes 直接调用 crypt32.dll，不增加第三方依赖。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
from pathlib import Path

# DPAPI flags
CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_encrypt(plain: bytes) -> bytes:
    """Encrypt data with CryptProtectData (user-level, no UI)."""
    in_buf = ctypes.create_string_buffer(plain, len(plain))
    in_blob = _DATA_BLOB(len(plain), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,  # description
        None,  # entropy
        None,  # reserved
        None,  # prompt struct
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise OSError("CryptProtectData 失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(encrypted: bytes) -> bytes:
    """Decrypt data with CryptUnprotectData."""
    in_buf = ctypes.create_string_buffer(encrypted, len(encrypted))
    in_blob = _DATA_BLOB(len(encrypted), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,  # description out
        None,  # entropy
        None,  # reserved
        None,  # prompt struct
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise OSError("CryptUnprotectData 失败 — 可能不是当前用户加密的数据")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def save_cookie_cache(cookies: list[dict], path: Path) -> None:
    """将 cookie dict 列表加密后写入文件。"""
    plain = json.dumps(cookies, ensure_ascii=False).encode("utf-8")
    encrypted = _dpapi_encrypt(plain)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypted)


def load_cookie_cache(path: Path) -> list[dict]:
    """从加密文件读取 cookie dict 列表。"""
    encrypted = path.read_bytes()
    plain = _dpapi_decrypt(encrypted)
    return json.loads(plain)

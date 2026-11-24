"""按链接读取在线表格（表格比对页「粘贴任意链接」用）。

浏览器直接读外站文件会被跨域拦截，所以由后端代取。代取等于让服务器替用户发请求，所以要防止被用来探测内网（SSRF）：
只允许 http/https，每一跳（含重定向）都要解析域名，目标是内网 / 本机 / 链路本地等非公网地址一律拒绝；
并限制重定向次数、超时和文件大小。
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from sheetmate.core.errors import ServiceError

MAX_BYTES = 25 * 1024 * 1024
MAX_REDIRECTS = 5
TABLE_SUFFIXES = (".xlsx", ".xlsm", ".csv")


@dataclass
class RemoteTable:
    filename: str
    content: bytes


def normalize_url(raw: str) -> str:
    """补全协议并校验是 http / https 链接。"""
    url = raw.strip()
    if not url:
        raise ServiceError("请输入表格链接")
    if not re.match(r"^[a-z][a-z\d+\-.]*://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ServiceError("只支持 http / https 链接")
    return url


def _assert_public_host(host: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ServiceError(f"无法解析域名「{host}」，请检查链接是否正确") from None
    if not infos:
        raise ServiceError(f"无法解析域名「{host}」")
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        mapped = getattr(address, "ipv4_mapped", None)
        if not (mapped or address).is_global:
            raise ServiceError("不允许访问内网或本机地址，请使用公网可访问的表格链接")


def _filename_from_response(url: str, response: httpx.Response) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*\s*=\s*[\w-]+'[^']*'([^;]+)", disposition, re.I)
    if match:
        name = unquote(match.group(1).strip().strip('"'))
    else:
        match = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)
        name = match.group(1).strip() if match else ""
        if name:
            try:                                                   # 部分服务把 UTF-8 文件名按 latin-1 回传
                name = name.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
    if not name:
        query = parse_qs(urlparse(url).query)
        name = (query.get("filename") or query.get("name") or [""])[0]
    if not name:
        name = unquote(PurePosixPath(urlparse(url).path).name)
    return re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name).strip()


def _with_table_suffix(name: str, content: bytes, content_type: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".xls"):
        raise ServiceError(f"「{name}」是旧版 .xls 格式，暂不支持在线比对，请另存为 .xlsx 后再试")
    if lowered.endswith(TABLE_SUFFIXES):
        return name
    stem = name or "在线表格"
    if content[:4] == b"PK\x03\x04":                                  # xlsx 本质是 zip
        return f"{stem}.xlsx"
    if content[:4] == b"\xd0\xcf\x11\xe0":
        raise ServiceError("这是旧版 .xls 格式，暂不支持在线比对，请另存为 .xlsx 后再试")
    if "csv" in content_type.lower() or "text/plain" in content_type.lower():
        return f"{stem}.csv"
    raise ServiceError("这个链接指向的不是表格文件（支持 .xlsx / .xlsm / .csv）。网页形式的在线表格请先下载后再比对")


def fetch_table(raw_url: str) -> RemoteTable:
    url = normalize_url(raw_url)
    headers = {"User-Agent": "SheetMate/1.0 (+table-compare)", "Accept": "*/*"}
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=False, headers=headers) as client:
            for _ in range(MAX_REDIRECTS + 1):
                parsed = urlparse(url)
                if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
                    raise ServiceError("只支持 http / https 链接")
                _assert_public_host(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
                with client.stream("GET", url) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code in (401, 403):
                        raise ServiceError("链接需要登录或没有访问权限，请改用公开链接，或下载后再比对")
                    if response.status_code == 404:
                        raise ServiceError("链接指向的文件不存在（404）")
                    if response.status_code >= 400:
                        raise ServiceError(f"读取链接失败（HTTP {response.status_code}）")
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > MAX_BYTES:
                        raise ServiceError("文件超过 25 MB，暂不支持在线比对")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise ServiceError("文件超过 25 MB，暂不支持在线比对")
                    data = bytes(content)
                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type.lower() and data[:4] != b"PK\x03\x04":
                        raise ServiceError("这个链接打开的是网页而不是表格文件，请使用文件的直接下载链接")
                    name = _with_table_suffix(_filename_from_response(url, response), data, content_type)
                    return RemoteTable(filename=name, content=data)
            raise ServiceError("链接重定向次数过多")
    except httpx.TimeoutException:
        raise ServiceError("读取链接超时，请稍后重试或下载后再比对") from None
    except httpx.HTTPError:
        raise ServiceError("无法连接到该链接，请检查地址是否正确") from None

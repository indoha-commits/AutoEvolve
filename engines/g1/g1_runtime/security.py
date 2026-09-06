from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse


def safe_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("only absolute HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("embedded credentials are prohibited")
    for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global:
            raise ValueError(f"non-public destination blocked: {address}")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text.lower())).strip()


def similarity(left: str, right: str) -> float:
    a, b = set(normalize_text(left).split()), set(normalize_text(right).split())
    return len(a & b) / len(a | b) if a or b else 1.0


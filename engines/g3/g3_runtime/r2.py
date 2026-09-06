from __future__ import annotations

import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .errors import G3Error
from .handoff import Media


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise G3Error(f"{name} is required")
    return value


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "media"


def _content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def verify_public_media(url: str, media: Media, mime: str, attempts: int = 3) -> None:
    """Prove that an unauthenticated external client can read the uploaded media."""
    last_error = "unknown error"
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={
                    "Accept": "*/*",
                    "Range": "bytes=0-4095",
                    "User-Agent": "Example Company-G3-Media-Probe/2.1",
                },
            )
            with urlopen(request, timeout=20) as response:
                status = getattr(response, "status", response.getcode())
                final_url = response.geturl()
                actual_type = _content_type(response.headers.get("Content-Type"))
                challenge = (response.headers.get("cf-mitigated") or "").lower()
                sample = response.read(4096)
            if status not in {200, 206}:
                raise G3Error(f"public media URL returned HTTP {status}")
            if not final_url.startswith("https://"):
                raise G3Error(f"public media URL redirected to non-HTTPS URL: {final_url}")
            if challenge == "challenge":
                raise G3Error("Cloudflare challenge blocks anonymous media access")
            if actual_type != mime:
                raise G3Error(
                    f"public media URL returned Content-Type {actual_type or 'missing'}; expected {mime}"
                )
            if mime == "video/mp4" and b"ftyp" not in sample[:64]:
                raise G3Error("public media URL did not return MP4 bytes (missing ftyp header)")
            return
        except HTTPError as exc:
            last_error = f"HTTP {exc.code} {exc.reason}"
        except URLError as exc:
            last_error = str(exc.reason)
        except (OSError, G3Error) as exc:
            last_error = str(exc)
        if attempt + 1 < attempts:
            time.sleep(0.5 * (attempt + 1))
    raise G3Error(
        f"R2 public media check failed for {url}: {last_error}. "
        "Confirm R2_PUBLIC_BASE_URL is attached to R2_BUCKET and allows anonymous GET requests "
        "without Cloudflare Access, WAF challenges, or redirects."
    )


class R2Uploader:
    def __init__(self):
        self.account_id = _required("R2_ACCOUNT_ID")
        self.access_key = _required("R2_ACCESS_KEY_ID")
        self.secret_key = _required("R2_SECRET_ACCESS_KEY")
        self.bucket = _required("R2_BUCKET")
        self.public_base = _required("R2_PUBLIC_BASE_URL").rstrip("/")
        if not self.public_base.startswith("https://"):
            raise G3Error("R2_PUBLIC_BASE_URL must use HTTPS")
        try:
            import boto3
        except ImportError as exc:
            raise G3Error("boto3 is required; run: python -m pip install -e .") from exc
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{self.account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name="auto",
        )

    def upload(self, campaign_id: str, media: Media) -> str:
        key = f"g3/{_safe_name(campaign_id)}/{media.sha256[:16]}-{_safe_name(media.path.name)}"
        mime = mimetypes.guess_type(media.path.name)[0] or "application/octet-stream"
        needs_upload = True
        try:
            existing = self.client.head_object(Bucket=self.bucket, Key=key)
            needs_upload = not (
                int(existing.get("ContentLength", -1)) == media.path.stat().st_size
                and _content_type(existing.get("ContentType")) == mime
            )
        except Exception as exc:
            response = getattr(exc, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = str(response.get("Error", {}).get("Code", ""))
            if status not in {404} and code not in {"404", "NoSuchKey", "NotFound"}:
                raise G3Error(f"R2 lookup failed for {media.path.name}: {exc}") from exc
        if needs_upload:
            try:
                self.client.upload_file(
                    str(media.path), self.bucket, key,
                    ExtraArgs={"ContentType": mime, "CacheControl": "public, max-age=31536000, immutable"},
                )
            except Exception as upload_exc:
                raise G3Error(f"R2 upload failed for {media.path.name}: {upload_exc}") from upload_exc
        url = f"{self.public_base}/{quote(key, safe='/')}"
        verify_public_media(url, media, mime)
        return url

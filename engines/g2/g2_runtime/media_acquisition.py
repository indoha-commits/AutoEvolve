from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from .fingerprint import file_sha256, perceptual_hash
from .models import AssetCandidate, AssetRecord


DOWNLOAD_HOSTS = {
    "pexels": {"pexels.com", "videos.pexels.com", "images.pexels.com"},
    "pixabay": {"pixabay.com", "cdn.pixabay.com", "player.vimeo.com"},
    "coverr": {"api.coverr.co", "coverr.co", "storage.googleapis.com"},
    "wikimedia": {"wikimedia.org", "upload.wikimedia.org"},
    "lordicon": {"lordicon.com", "cdn.lordicon.com", "media.lordicon.com"},
}
MAX_BYTES = {"image": 30_000_000, "video": 300_000_000, "lottie": 5_000_000, "svg": 5_000_000}
SUFFIXES = {"image": ".jpg", "video": ".mp4", "lottie": ".json", "svg": ".svg"}


def _allowed(host: str, roots: set[str]) -> bool:
    return any(host == root or host.endswith("." + root) for root in roots)


def _download(candidate: AssetCandidate, destination: Path, timeout: int = 45) -> None:
    if not candidate.download_url or candidate.provider not in DOWNLOAD_HOSTS:
        raise ValueError("candidate has no approved download route")
    parsed = urlparse(candidate.download_url)
    if parsed.scheme != "https" or not _allowed((parsed.hostname or "").lower(), DOWNLOAD_HOSTS[candidate.provider]):
        raise ValueError("download URL is outside the provider allowlist")
    download_url = candidate.download_url
    if candidate.provider == "coverr":
        api_key = os.getenv("COVERR_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("COVERR_API_KEY is not configured")
        signed_request = urllib.request.Request(download_url, headers={
            "API_KEY": api_key,
            "Accept": "application/json",
            "User-Agent": "company-core-g2/0.8.2",
        })
        with urllib.request.urlopen(signed_request, timeout=timeout) as response:
            signed_payload = json.loads(response.read(100_000))
        download_url = signed_payload if isinstance(signed_payload, str) else signed_payload.get("url")
        if not download_url:
            raise ValueError("Coverr returned no signed download URL")
        signed = urlparse(download_url)
        if signed.scheme != "https" or not _allowed((signed.hostname or "").lower(), DOWNLOAD_HOSTS["coverr"]):
            raise ValueError("Coverr signed URL is outside the provider allowlist")
    request = urllib.request.Request(download_url, headers={"User-Agent": "company-core-g2/0.8.2"})
    limit = MAX_BYTES[candidate.media_type]
    temporary = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
        final = urlparse(response.geturl())
        if final.scheme != "https" or not _allowed((final.hostname or "").lower(), DOWNLOAD_HOSTS[candidate.provider]):
            raise ValueError("download redirect left the provider allowlist")
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError(f"{candidate.media_type} exceeds download limit")
            output.write(chunk)
    temporary.replace(destination)


def _probe_video(path: Path) -> tuple[int, int, float]:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe is required to inspect video candidates")
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    value = json.loads(result.stdout)
    stream = (value.get("streams") or [{}])[0]
    return int(stream.get("width") or 0), int(stream.get("height") or 0), float(value.get("format", {}).get("duration") or 0)


def _acquire_scene(scene: dict, root: Path, timeout: int) -> tuple[AssetRecord | None, dict]:
    selected = scene.get("selected")
    if not selected:
        return None, {"scene": scene.get("scene"), "status": scene.get("status", "unresolved")}
    candidate = AssetCandidate.model_validate(selected)
    suffix = SUFFIXES[candidate.media_type]
    path = root / f"scene_{int(scene['scene']):02d}_{candidate.provider}{suffix}"
    try:
        if candidate.provider == "local" and candidate.local_path:
            source = Path(candidate.local_path).resolve()
            if not source.is_file():
                raise ValueError("catalogued local asset no longer exists")
            shutil.copy2(source, path)
        else:
            _download(candidate, path, timeout)
        width, height = candidate.width, candidate.height
        duration = candidate.duration_seconds
        phash = None
        if candidate.media_type == "image":
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
            phash = perceptual_hash(path)
        elif candidate.media_type == "video":
            width, height, duration = _probe_video(path)
            if width < 1 or height < 1 or duration <= 0:
                raise ValueError("video probe returned invalid dimensions or duration")
        elif candidate.media_type == "lottie":
            json.loads(path.read_text(encoding="utf-8"))
        elif candidate.media_type == "svg":
            if "<svg" not in path.read_text(encoding="utf-8", errors="ignore")[:4096].lower():
                raise ValueError("download is not an SVG document")
        clip = scene.get("clip_window") or {}
        record = AssetRecord(
            slide_number=int(scene["scene"]), local_path=path.name,
            source_url=candidate.source_url, provider=candidate.provider,
            license=candidate.license, approved=False, candidate_id=candidate.candidate_id,
            source_type="stock" if candidate.source_type == "stock" else "illustration",
            sha256=file_sha256(path), perceptual_hash=phash, width=width, height=height,
            media_type=candidate.media_type, duration_seconds=duration,
            clip_start_seconds=float(clip.get("start_seconds") or 0),
            clip_end_seconds=clip.get("end_seconds"),
        )
        return record, {
            "scene": scene["scene"],
            "status": "downloaded_for_founder_review",
            "path": path.name,
        }
    except Exception as exc:
        path.unlink(missing_ok=True)
        return None, {
            "scene": scene.get("scene"),
            "status": "download_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def acquire_selected_media(
    search_result: dict,
    output: str | Path,
    timeout: int = 45,
    workers: int = 1,
) -> dict:
    """Download only the deterministic winner for each scene; never auto-approve it."""
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    scenes = list(search_result.get("scenes", []))
    worker_count = max(1, min(int(workers), 8))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        outcomes = list(executor.map(lambda scene: _acquire_scene(scene, root, timeout), scenes))
    records = [record for record, _ in outcomes if record is not None]
    report = [item for _, item in outcomes]
    manifest = [record.model_dump(mode="json") for record in records]
    (root / "asset_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    result = {
        "campaign_id": search_result.get("campaign_id"), "assets": manifest, "resolution": report,
        "status": "needs_founder_media_review", "publish_allowed": False,
    }
    (root / "acquisition_report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result

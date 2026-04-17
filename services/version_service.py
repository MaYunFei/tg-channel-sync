from __future__ import annotations

import json
import urllib.request

from app_paths import version_file


GITHUB_REPO = "RRHTY/tg-channel-sync"


def normalize_version_tag(value: str) -> str:
    return str(value or "").strip().lower().lstrip("v")


def version_key(value: str):
    normalized = normalize_version_tag(value)
    parts = []
    for chunk in normalized.replace("-", ".").split("."):
        if not chunk:
            continue
        if chunk.isdigit():
            parts.append((0, int(chunk)))
            continue
        number_prefix = ""
        for ch in chunk:
            if ch.isdigit():
                number_prefix += ch
            else:
                break
        if number_prefix:
            parts.append((0, int(number_prefix)))
            suffix = chunk[len(number_prefix):]
            if suffix:
                parts.append((1, suffix))
        else:
            parts.append((1, chunk))
    return tuple(parts)


def has_valid_version(value: str) -> bool:
    normalized = normalize_version_tag(value)
    if not normalized:
        return False
    first = normalized.split(".", 1)[0].split("-", 1)[0]
    return first.isdigit()


def is_version_at_least(current_version: str, latest_version: str) -> bool:
    if not latest_version or not has_valid_version(current_version) or not has_valid_version(latest_version):
        return False
    return version_key(current_version) >= version_key(latest_version)


def get_local_version() -> str:
    try:
        version = version_file().read_text(encoding="utf-8").strip()
        if version:
            return version
    except Exception:
        pass
    return "unknown"


def fetch_github_json(url: str):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "tg-channel-sync-version-check",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get_remote_version_info() -> dict:
    latest_release_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    tags_url = f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=1"
    try:
        release = fetch_github_json(latest_release_url)
        tag_name = str(release.get("tag_name") or "").strip()
        if tag_name:
            return {
                "latest_version": tag_name,
                "source": "release",
                "url": release.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/latest",
            }
    except Exception:
        pass

    tags = fetch_github_json(tags_url)
    if isinstance(tags, list) and tags:
        tag_name = str(tags[0].get("name") or "").strip()
        if tag_name:
            return {
                "latest_version": tag_name,
                "source": "tag",
                "url": f"https://github.com/{GITHUB_REPO}/tags",
            }

    return {"latest_version": "", "source": "", "url": f"https://github.com/{GITHUB_REPO}"}

"""
NewAPI OpenAI 鍏煎瑙嗛鐢熸垚鎻掍欢
鏀寔鑷畾涔?Base URL 鍜?API Key锛岃嚜鍔ㄤ粠 /v1/models 鑾峰彇鍙敤妯″瀷鍒楄〃銆?

API 娴佺▼锛圤penAI Sora 鏍煎紡锛?
  1. POST /v1/videos        鈫?鎻愪氦鐢熸垚浠诲姟锛岃幏寰?task_id
  2. GET  /v1/videos/{id}    鈫?杞浠诲姟鐘舵€侊紙completed/failed锛?
  3. GET  /v1/videos/{id}/content 鈫?涓嬭浇瑙嗛鏂囦欢

鎻掍欢鐩綍缁撴瀯锛?
  newapi_openai/
  鈹溾攢鈹€ main.py          鈫?鐢熸垚閫昏緫 + 妯″瀷鑾峰彇
  鈹溾攢鈹€ ui/
  鈹?  鈹斺攢鈹€ index.html   鈫?鍓嶇璁剧疆鐣岄潰锛坕frame 鍔犺浇锛?
  鈹斺攢鈹€ info.json        鈫?鎻掍欢鍏冧俊鎭?
"""

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

import requests

plugin_dir = Path(__file__).parent
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from plugin_utils import load_plugin_config, update_plugin_params


def _log(msg):
    """Write plugin debug logs."""
    try:
        log_dir = plugin_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"debug_{datetime.now().strftime('%Y%m%d')}.log"
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass


def _version_tuple(value: str):
    text = str(value or "").strip().lstrip("vV")
    parts = []
    for item in text.replace("-", ".").split("."):
        try:
            parts.append(int("".join(ch for ch in item if ch.isdigit()) or "0"))
        except Exception:
            parts.append(0)
    return tuple((parts + [0, 0, 0])[:3])


def _normalize_update_repo(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("https://github.com/"):
        text = text[len("https://github.com/"):]
    return text.strip("/ ")


def _get_jsdelivr_latest_version(repo: str, timeout: int = 20) -> str:
    last_error = None
    for cdn_host in ("cdn.jsdelivr.net", "gcore.jsdelivr.net"):
        url = f"https://{cdn_host}/gh/{repo}@main/main.py"
        try:
            response = requests.get(url, timeout=min(timeout, 10), proxies={"http": None, "https": None})
            response.raise_for_status()
            for line in response.text.splitlines():
                if line.strip().startswith("_PLUGIN_VERSION") and "=" in line:
                    return line.split("=", 1)[1].strip().strip("\"'")
        except requests.RequestException as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return ""


def _get_latest_release(repo: str, timeout: int = 20) -> dict:
    repo = _normalize_update_repo(repo)
    if not repo or "/" not in repo:
        return {"ok": False, "error": "插件内置更新源无效"}
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "huiju-video-plugin-updater"}
    release = {}
    tags = []
    github_errors = []
    try:
        release_url = f"https://api.github.com/repos/{repo}/releases/latest"
        release_resp = requests.get(release_url, headers=headers, timeout=timeout, proxies={"http": None, "https": None})
        release = release_resp.json() if release_resp.status_code == 200 else {}
        if release_resp.status_code not in (200, 404):
            github_errors.append(f"Release HTTP {release_resp.status_code}")
    except requests.RequestException as exc:
        github_errors.append(str(exc))
    try:
        tags_url = f"https://api.github.com/repos/{repo}/tags?per_page=30"
        tags_resp = requests.get(tags_url, headers=headers, timeout=timeout, proxies={"http": None, "https": None})
        tags = tags_resp.json() if tags_resp.status_code == 200 else []
        if tags_resp.status_code != 200:
            github_errors.append(f"Tags HTTP {tags_resp.status_code}")
    except requests.RequestException as exc:
        github_errors.append(str(exc))
    valid_tags = [item for item in tags if isinstance(item, dict) and item.get("name") and item.get("zipball_url")]
    latest_tag = max(valid_tags, key=lambda item: _version_tuple(item.get("name")), default={})

    release_tag = str(release.get("tag_name") or "")
    tag_name = str(latest_tag.get("name") or "")
    if tag_name and (not release_tag or _version_tuple(tag_name) > _version_tuple(release_tag)):
        latest = tag_name.lstrip("vV")
        return {
            "ok": True,
            "repo": repo,
            "current_version": _PLUGIN_VERSION,
            "latest_version": latest,
            "has_update": _version_tuple(latest) > _version_tuple(_PLUGIN_VERSION),
            "release_name": tag_name,
            "html_url": f"https://github.com/{repo}/tree/{tag_name}",
            "assets": [{"name": f"{tag_name}.zip", "download_url": latest_tag["zipball_url"]}],
            "update_channel": "GitHub（下载失败时自动切换 CDN）",
        }
    if not release:
        try:
            latest = _get_jsdelivr_latest_version(repo, timeout)
        except (requests.RequestException, ValueError, TypeError) as exc:
            details = "; ".join(github_errors + [f"CDN: {exc}"])
            return {"ok": False, "error": f"更新源不可用：{details}"}
        if not latest:
            return {"ok": False, "error": "更新源没有可用版本"}
        return {
            "ok": True,
            "repo": repo,
            "current_version": _PLUGIN_VERSION,
            "latest_version": latest.lstrip("vV"),
            "has_update": _version_tuple(latest) > _version_tuple(_PLUGIN_VERSION),
            "release_name": latest,
            "html_url": f"https://cdn.jsdelivr.net/gh/{repo}@{latest}/",
            "assets": [],
            "update_channel": "jsDelivr CDN",
        }

    latest = str(release.get("tag_name") or "").lstrip("v")
    assets = [
        {"name": item.get("name") or "", "download_url": item.get("browser_download_url") or ""}
        for item in (release.get("assets") or [])
        if item.get("browser_download_url")
    ]
    if not assets and release.get("zipball_url"):
        assets.append({"name": f"{release.get('tag_name') or 'source'}.zip", "download_url": release.get("zipball_url")})
    return {
        "ok": True,
        "repo": repo,
        "current_version": _PLUGIN_VERSION,
        "latest_version": latest,
        "has_update": bool(latest and _version_tuple(latest) > _version_tuple(_PLUGIN_VERSION)),
        "release_name": release.get("name") or release.get("tag_name") or "",
        "html_url": release.get("html_url") or "",
        "assets": assets,
        "update_channel": "GitHub（下载失败时自动切换 CDN）",
    }


def _choose_update_asset(release: dict, preferred_name: str = "") -> dict:
    assets = release.get("assets") or []
    preferred = str(preferred_name or "").strip()
    if preferred:
        for asset in assets:
            if asset.get("name") == preferred:
                return asset
    zip_assets = [asset for asset in assets if str(asset.get("name") or "").lower().endswith(".zip")]
    if zip_assets:
        return zip_assets[0]
    return assets[0] if assets else {}


def _copy_plugin_update(source_dir: Path) -> Path:
    backup_dir = plugin_dir.parent / f"{plugin_dir.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    ignore = shutil.ignore_patterns(".git", "__pycache__", "logs", "*.pyc", "*.pyo")
    shutil.copytree(plugin_dir, backup_dir, ignore=ignore)
    for item in source_dir.iterdir():
        if item.name in {".git", "__pycache__", "logs"}:
            continue
        target = plugin_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, ignore=ignore)
        else:
            shutil.copy2(item, target)
    return backup_dir


def _download_jsdelivr_update(repo: str, version: str, source_dir: Path, timeout: int = 60) -> None:
    if not version:
        raise ValueError("CDN 更新版本为空")
    for relative_text in ("main.py", "info.json", "ui/index.html"):
        target = source_dir / Path(relative_text)
        target.parent.mkdir(parents=True, exist_ok=True)
        file_path = requests.utils.quote(relative_text, safe="/")
        last_error = None
        for cdn_host in ("cdn.jsdelivr.net", "gcore.jsdelivr.net"):
            file_url = f"https://{cdn_host}/gh/{repo}@{version}/{file_path}"
            try:
                file_response = requests.get(
                    file_url, timeout=min(timeout, 20), proxies={"http": None, "https": None}
                )
                file_response.raise_for_status()
                target.write_bytes(file_response.content)
                last_error = None
                break
            except requests.RequestException as exc:
                last_error = exc
        if last_error is not None:
            raise last_error


def _apply_github_update(repo: str, preferred_asset_name: str = "") -> dict:
    release = _get_latest_release(repo)
    if not release.get("ok"):
        return release
    if not release.get("has_update"):
        return {"ok": True, "updated": False, **release}
    asset = _choose_update_asset(release, preferred_asset_name)

    with tempfile.TemporaryDirectory(prefix="huiju_plugin_update_") as temp_name:
        temp_dir = Path(temp_name)
        source_dir = None
        github_error = ""
        if asset and asset.get("download_url"):
            try:
                zip_path = temp_dir / (asset.get("name") or "update.zip")
                _log(f"[update] downloading {asset.get('download_url')}")
                with requests.get(asset["download_url"], stream=True, timeout=180, proxies={"http": None, "https": None}) as resp:
                    resp.raise_for_status()
                    with open(zip_path, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1024 * 512):
                            if chunk:
                                fh.write(chunk)
                extract_dir = temp_dir / "extract"
                with zipfile.ZipFile(zip_path) as archive:
                    archive.extractall(extract_dir)
                candidates = [extract_dir]
                candidates.extend([p for p in extract_dir.rglob("*") if p.is_dir()])
                source_dir = next((candidate for candidate in candidates
                                   if (candidate / "main.py").exists() and (candidate / "ui" / "index.html").exists()), None)
            except (requests.RequestException, zipfile.BadZipFile, OSError) as exc:
                github_error = str(exc)
                _log(f"[update] GitHub download failed, switching to CDN: {exc}")
        if source_dir is None:
            source_dir = temp_dir / "cdn"
            try:
                _download_jsdelivr_update(repo, release.get("latest_version") or "", source_dir)
                release["update_channel"] = "jsDelivr CDN"
            except (requests.RequestException, ValueError, OSError) as exc:
                details = f"GitHub: {github_error}; CDN: {exc}" if github_error else f"CDN: {exc}"
                return {"ok": False, "error": f"更新下载失败：{details}", **release}

        backup_dir = _copy_plugin_update(source_dir)
        update_plugin_params(_PLUGIN_FILE, {
            "update_repo": _normalize_update_repo(repo),
            "update_asset_name": asset.get("name") if asset else "",
            "last_update_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return {"ok": True, "updated": True, "backup_dir": str(backup_dir), "asset": asset.get("name") if asset else "", **release}


def _merge_plugin_params(context_params):
    """Merge defaults, saved settings, then values from the current request."""
    disk_params = load_plugin_config(_PLUGIN_FILE)
    merged = _DEFAULT_PARAMS.copy()
    for key, value in disk_params.items():
        if value not in (None, ""):
            merged[key] = value
    if isinstance(context_params, dict):
        for key, value in context_params.items():
            if value not in (None, ""):
                merged[key] = value
    return merged, disk_params, (context_params if isinstance(context_params, dict) else {})


_PLUGIN_FILE = __file__
_PLUGIN_VERSION = "1.2.13"
_UPDATE_REPO = "liushuai0109001-cell/huiju-video-plugin"

# ===================== 榛樿鍙傛暟 =====================

_DEFAULT_PARAMS = {
    "api_key": "",
    "base_url": "https://huiju.v888.art",
    "model": "sora-2",
    "aspect_ratio": "16:9",
    "duration": 6,

    "fps": 24,
    "n": 1,
    "response_format": "url",
    "timeout": 900,
    "max_poll_attempts": 300,
    "poll_interval": 10,
    "reference_mode": "first_frame",  # multi_image:澶氬浘妯″紡, first_frame:棣栧抚鍥炬ā寮?    "resolution": "720p",  # 480p / 720p
    "compliance_enabled": False,
    "compliance_mode": "colored-pencil",
    # 鍥惧簥閰嶇疆锛堢敤浜庢妸鏈湴鍥剧墖杞垚鍏綉 URL 渚涗笂娓镐娇鐢級
    "image_host_url": "https://huiju.v888.art/upload",
    "image_host_token": "huiju-upload-2026",
    "image_host_timeout": 60,
    "update_repo": _UPDATE_REPO,
    "update_asset_name": "",
}


# ===================== 姣斾緥鍒板昂瀵告槧灏?=====================

_ASPECT_RATIO_SIZE_MAP = {
    "16:9":  (1280, 720),
    "9:16":  (720, 1280),
    "1:1":   (1024, 1024),
    "4:3":   (1024, 768),
    "3:4":   (768, 1024),
    "3:2":   (1152, 768),
    "2:3":   (768, 1152),
}

_SEEDREAM_MODELS = {
    "ss-xinghe-2.0-720p",
    "ss-xinghe-fast-720p",
    "xh-sdas-fast-720p",
    "xh-sdas-fast-933-720p",
    "xh-sdas-pro-720p",
    "xh-sdas-pro-933-720p",
    "xs-sdas-fast-480p",
    "xs-sdas-fast-720p",
    "sdas-hn-sd2.0-900-720p",
    "sdas-hj-sd2.0-pro-2-933-720p",
}

_SEEDREAM_ALLOWED_DURATIONS = {
    "xs-sdas-fast-720p": (10, 15),
    "sdas-hn-sd2.0-900-720p": (15,),
}


# ===================== 宸ュ叿鍑芥暟 =====================

def _ratio_to_size(aspect_ratio: str):
    """Convert aspect ratio to width and height."""
    ratio = str(aspect_ratio).strip()
    if ratio in _ASPECT_RATIO_SIZE_MAP:
        w, h = _ASPECT_RATIO_SIZE_MAP[ratio]
        return w, h
    # 灏濊瘯瑙ｆ瀽 "W:H" 鏍煎紡
    try:
        parts = ratio.split(":")
        if len(parts) == 2:
            w_ratio, h_ratio = float(parts[0]), float(parts[1])
            base = 720
            return int(base * w_ratio / max(h_ratio, 1)), int(base * h_ratio / max(w_ratio, 1))
    except Exception:
        pass
    return 1280, 720


def _is_seedream_model(model: str) -> bool:
    """Return whether this is a Seedream/sudashui video model."""
    m = str(model or "").strip()
    return m in _SEEDREAM_MODELS or m.startswith(("xs-sdas-", "xh-sdas-", "ss-xinghe-", "sdas-"))


def _normalize_seedream_duration(model: str, duration: int) -> int:
    """Normalize Seedream duration by model limits."""
    allowed = _SEEDREAM_ALLOWED_DURATIONS.get(str(model or "").strip())
    if allowed:
        chosen = min(allowed, key=lambda x: abs(x - duration))
        if chosen != duration:
            _log(f"  [Seedream] 褰撳墠妯″瀷浠呮敮鎸?{allowed} 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 {chosen}")
        return chosen
    if duration < 5:
        _log("  [Seedream] 鏃堕暱灏忎簬 5 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 5")
        return 5
    if duration > 15:
        _log("  [Seedream] 鏃堕暱澶т簬 15 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 15")
        return 15
    return duration


def _is_grok_imagine_model(model: str) -> bool:
    return "grok-imagine" in str(model or "").strip().lower()


def _is_schat_sd20_fast_9ref_model(model: str) -> bool:
    name = str(model or "").strip().lower()
    compact = name.replace(" ", "").replace("_", "-")
    is_sd20 = any(token in compact for token in ("sd2.0", "sd-2.0", "sd20", "seedance2.0", "seedance-2.0"))
    is_nine_ref = any(token in compact for token in ("9ref", "9-ref", "9img", "9-image", "9pic", "9-pic"))
    return is_sd20 and "fast" in compact and is_nine_ref


def _is_chre_seedance_model(model: str) -> bool:
    m = str(model or "").strip().lower()
    return m in {
        "seedance-2.0-720p",
        "seedance-2.0-fast-720p",
        "sd2-c6",
        "sd2-c7",
        "sd2-c8",
        "sd2.5",
    }


_CHRE_COMPLIANCE_MODES = {
    "colored-pencil",
    "watercolor",
    "fishnet",
    "grid",
}


def _normalize_compliance_mode(value: object) -> str:
    mode = str(value or "colored-pencil").strip().lower()
    if mode not in _CHRE_COMPLIANCE_MODES:
        _log(f"  [CHRE Seedance] unsupported compliance_mode={mode!r}; using colored-pencil")
        return "colored-pencil"
    return mode


def _normalize_grok_duration(model: str, duration: int) -> int:
    model_name = str(model or "").strip().lower()
    if model_name in {"grok-imagine-video-1.5", "grok-imagine-1.5-video"}:
        allowed = (6, 10, 15)
    else:
        allowed = (6, 10)
    chosen = min(allowed, key=lambda x: abs(x - duration))
    if chosen != duration:
        _log(f"  [Grok] 褰撳墠妯″瀷浠呮敮鎸?{allowed} 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 {chosen}")
    return chosen


def _normalize_xingyao_duration(model: str, duration: int) -> int:
    m = str(model or "").strip().lower()
    if m == "wan3.0th":
        # WAN 3.0 Text-to-Video accepts user-selected durations up to 30s.
        # Keep the requested value intact instead of applying the generic 15s cap.
        if duration < 1:
            _log("  [WAN 3.0 TH] duration below 1 second; adjusted to 1")
            return 1
        if duration > 30:
            _log(f"  [WAN 3.0 TH] duration above 30 seconds; adjusted {duration} -> 30")
            return 30
        return duration
    if m == "seedance2.5":
        if duration != 14:
            _log(f"  [Seedance 2.5] duration is fixed at 14 seconds; adjusted {duration} -> 14")
        return 14
    if m == "seedance-2.5-720pv-1":
        if duration < 4:
            _log("  [Seedance 2.5] duration below 4 seconds; adjusted to 4")
            return 4
        if duration > 29:
            _log("  [Seedance 2.5] duration above 29 seconds; adjusted to 29")
            return 29
        return duration
    if m in {"sora2", "sora-2"}:
        allowed = (4, 8, 12)
        chosen = min(allowed, key=lambda x: abs(x - duration))
        if chosen != duration:
            _log(f"  [Xingyao] sora2 浠呮敮鎸?{allowed} 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 {chosen}")
        return chosen
    if m == "veo-fast":
        allowed = (4, 6, 8)
        chosen = min(allowed, key=lambda x: abs(x - duration))
        if chosen != duration:
            _log(f"  [Xingyao] veo-fast 浠呮敮鎸?{allowed} 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 {chosen}")
        return chosen
    if m in {"xinqi-2.0-fast-v5", "xinqi-2.0-v5"}:
        if duration < 10:
            _log("  [Xingyao] xinqi v5 鏃堕暱灏忎簬 10 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 10")
            return 10
        if duration > 15:
            _log("  [Xingyao] xinqi v5 鏃堕暱澶т簬 15 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 15")
            return 15
        return duration
    if m == "pl-2.0-720p-v1":
        allowed = (5, 10, 15)
        chosen = min(allowed, key=lambda x: abs(x - duration))
        if chosen != duration:
            _log(f"  [Xingyao] PL-2.0-720p-v1 浠呮敮鎸?{allowed} 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 {chosen}")
        return chosen
    if m.startswith("瀹?sd-2.0-"):
        if duration < 4:
            _log("  [Xingyao] 瀹?sd-2.0 绯诲垪鏃堕暱灏忎簬 4 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 4")
            return 4
        if duration > 15:
            _log("  [Xingyao] 瀹?sd-2.0 绯诲垪鏃堕暱澶т簬 15 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 15")
            return 15
        return duration
    if m == "xinqi-2.0-fast-v4":
        if duration < 5:
            _log("  [Xingyao] xinqi-2.0-fast-v4 鏃堕暱灏忎簬 5 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 5")
            return 5
        if duration > 15:
            _log("  [Xingyao] 鏃堕暱澶т簬 15 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 15")
            return 15
        return duration
    if duration < 1:
        return 1
    if duration > 15:
        return 15
    return duration


def _normalize_seedream_aspect_ratio(aspect_ratio: str) -> str:
    """Normalize Seedream aspect ratio."""
    ratio = str(aspect_ratio or "16:9").strip()
    supported = {"16:9", "9:16", "1:1", "4:3", "3:4"}
    if ratio in supported:
        return ratio
    mapped = "9:16" if ratio == "2:3" else "16:9"
    _log(f"  [Seedream] 瀹介珮姣?{ratio} 鍙兘涓嶆敮鎸侊紝宸茶嚜鍔ㄨ皟鏁翠负 {mapped}")
    return mapped


def _ratio_to_video_size(aspect_ratio: str, resolution: str) -> str:
    """Convert aspect ratio and resolution to video size."""
    ratio = str(aspect_ratio or "16:9").strip()
    res = str(resolution or "720p").strip().lower()
    if res == "480p":
        mapping = {
            "16:9": "854x480",
            "9:16": "480x854",
            "1:1": "480x480",
            "21:9": "1120x480",
            "4:3": "640x480",
            "3:4": "480x640",
            "3:2": "720x480",
            "2:3": "480x720",
        }
    else:
        mapping = {
            "16:9": "1280x720",
            "9:16": "720x1280",
            "1:1": "1024x1024",
            "21:9": "1680x720",
            "4:3": "1024x768",
            "3:4": "768x1024",
            "3:2": "1152x768",
            "2:3": "768x1152",
        }
    size = mapping.get(ratio, "1280x720")
    if ratio not in mapping:
        _log(f"  [NewAPI] 鏈瘑鍒楂樻瘮 {ratio}锛宻ize 宸蹭娇鐢ㄩ粯璁?{size}")
    return size


def _seedream_status_value(status_data: dict) -> str:
    data = status_data.get("data") if isinstance(status_data, dict) else None
    if isinstance(data, dict):
        return str(data.get("status") or "")
    return str(status_data.get("status") or "")


def _seedream_progress_value(status_data: dict):
    data = status_data.get("data") if isinstance(status_data, dict) else None
    progress = data.get("progress") if isinstance(data, dict) else status_data.get("progress")
    if isinstance(progress, str) and progress.endswith("%"):
        try:
            return int(float(progress[:-1]))
        except Exception:
            return None
    return progress


def _seedream_result_url(status_data: dict) -> str:
    data = status_data.get("data") if isinstance(status_data, dict) else None
    if isinstance(data, dict):
        return data.get("result_url") or data.get("url") or ""
    return status_data.get("result_url") or status_data.get("video_url") or status_data.get("url") or ""


def _seedream_failure_reason(status_data: dict) -> str:
    data = status_data.get("data") if isinstance(status_data, dict) else None
    if isinstance(data, dict):
        return data.get("fail_reason") or data.get("message") or status_data.get("message") or "浠诲姟澶辫触"
    err = status_data.get("error")
    if isinstance(err, dict):
        return err.get("message") or "浠诲姟澶辫触"
    return str(err) if err else status_data.get("message", "浠诲姟澶辫触")


# ===================== 妯″瀷鍒楄〃鑾峰彇 =====================

def _fetch_models_from_api(base_url: str, api_key: str, timeout: int = 15) -> dict:
    """Fetch model list from an OpenAI-compatible /v1/models endpoint."""
    endpoint = f"{base_url.rstrip('/')}/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(endpoint, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            return {"ok": False, "error": f"HTTP {resp.status_code}: {detail}"}
        
        data = resp.json()
        raw_models = data.get("data", [])
        if not raw_models:
            return {"ok": False, "error": "API returned an empty model list"}
        
        model_ids = sorted([m.get("id", "") for m in raw_models if m.get("id")])
        return {
            "ok": True,
            "models": model_ids,
            "default_model": model_ids[0] if model_ids else "",
        }
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Request timed out, please check the network"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "Connection failed, please check Base URL"}
    except Exception as e:
        return {"ok": False, "error": f"Failed to fetch model list: {str(e)}"}


# ===================== 鍥惧簥涓婁紶 =====================

def _upload_image_to_host(image_path: str, host_url: str, host_token: str = "", timeout: int = 60) -> str:
    """Upload a local image, audio, or video file and return a public URL."""
    if str(image_path or "").lower().startswith(("http://", "https://")):
        return str(image_path).strip()
    _log(f"  [鍥惧簥] 寮€濮嬩笂浼? {image_path}")
    clean = str(image_path).split("?")[0]
    if not os.path.exists(clean):
        raise Exception(f"PLUGIN_ERROR:::图片文件不存在: {clean}")
    if not host_url:
        raise Exception("PLUGIN_ERROR:::图床地址未配置，请在插件设置中填写 Image Host URL")

    try:
        ext = os.path.splitext(clean)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif",
            ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
            ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
            ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
            ".mkv": "video/x-matroska",
        }
        mime = mime_map.get(ext) or mimetypes.guess_type(clean)[0] or "application/octet-stream"
        filename = os.path.basename(clean)
        file_size = os.path.getsize(clean)
        _log(f"  [鍥惧簥] 鏂囦欢澶у皬: {file_size / 1024:.2f} KB, MIME: {mime}")

        headers = {}
        if host_token:
            headers["Authorization"] = f"Bearer {host_token}"
            headers["X-Upload-Key"] = host_token

        resp = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                with open(clean, "rb") as f:
                    files = {"file": (filename, f, mime)}
                    resp = requests.post(
                        host_url.rstrip("/"),
                        headers=headers,
                        files=files,
                        timeout=timeout,
                        proxies={"http": None, "https": None},
                    )
                if resp.status_code not in (429, 500, 502, 503, 504):
                    break
                if attempt == max_attempts:
                    break
                delay = attempt * 2
                _log(f"  [image-host] upload returned HTTP {resp.status_code}; retrying in {delay}s ({attempt}/{max_attempts})")
                time.sleep(delay)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                if attempt == max_attempts:
                    raise
                delay = attempt * 2
                _log(f"  [image-host] transient upload error: {exc}; retrying in {delay}s ({attempt}/{max_attempts})")
                time.sleep(delay)

        _log(f"  [鍥惧簥] 涓婁紶鍝嶅簲鐘舵€? {resp.status_code}")
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise Exception(f"PLUGIN_ERROR:::图床上传失败 {resp.status_code}: {detail}")

        try:
            data = resp.json()
        except Exception:
            # 濡傛灉鎺ュ彛杩斿洖绾枃鏈?URL锛岀洿鎺ヤ娇鐢?
            url = resp.text.strip()
            if url.startswith("http"):
                _log(f"  [鍥惧簥] 杩斿洖鏂囨湰 URL: {url}")
                return url
            raise Exception(f"PLUGIN_ERROR:::图床返回非 URL 文本: {resp.text[:200]}")

        # 灏濊瘯澶氱甯歌杩斿洖瀛楁
        url = None
        for key in ("url", "image_url", "file_url", "public_url", "link", "data"):
            val = data.get(key)
            if val and isinstance(val, str) and val.startswith("http"):
                url = val
                break
        if not url:
            # 鏈変簺杩斿洖 {data: {url: ...}}
            nested = data.get("data")
            if isinstance(nested, dict):
                for key in ("url", "image_url", "file_url", "public_url", "link"):
                    val = nested.get(key)
                    if val and isinstance(val, str) and val.startswith("http"):
                        url = val
                        break
        if not url:
            raise Exception(f"PLUGIN_ERROR:::图床响应中未找到 URL: {json.dumps(data, ensure_ascii=False)[:300]}")

        _log(f"  [鍥惧簥] 涓婁紶鎴愬姛锛孶RL: {url}")
        return url
    except Exception as e:
        _log(f"  [鍥惧簥] 涓婁紶澶辫触: {e}")
        raise


def _requires_wav_reference_audio(model: str) -> bool:
    """Return whether this upstream model accepts WAV reference audio only."""
    return str(model or "").strip().lower() == "seedance-2.0-mini"


def _find_ffmpeg_binary() -> str:
    candidates = [
        os.environ.get("FFMPEG_BINARY"),
        plugin_dir / "video-watermark-batch-tool" / "ffmpeg.exe",
        plugin_dir.parents[3] / "resources" / "ffmpeg" / "bin" / "ffmpeg.exe",
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(str(candidate)):
            return str(candidate)
    raise Exception("PLUGIN_ERROR:::当前模型仅支持 WAV 参考音频，但未找到 FFmpeg，无法自动转换")


def _convert_reference_audio_to_wav(audio_path: str) -> str:
    """Convert a local reference audio file to PCM WAV and return a temp path."""
    clean_path = str(audio_path or "").split("?", 1)[0]
    if os.path.splitext(clean_path)[1].lower() == ".wav":
        return clean_path
    if not os.path.isfile(clean_path):
        raise Exception(f"PLUGIN_ERROR:::参考音频不存在: {clean_path}")

    fd, wav_path = tempfile.mkstemp(prefix="huiju_reference_audio_", suffix=".wav")
    os.close(fd)
    command = [
        _find_ffmpeg_binary(), "-y", "-v", "error", "-i", clean_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", wav_path,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
            detail = (result.stderr or result.stdout or "unknown ffmpeg error").strip()[-500:]
            raise Exception(f"PLUGIN_ERROR:::参考音频转换 WAV 失败: {detail}")
        _log(f"  [reference-audio] converted to WAV: {wav_path}")
        return wav_path
    except Exception:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        raise


_REFERENCE_MEDIA_CONFIG = {
    "audio": {
        "extensions": {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"},
        "keys": (
            "reference_audios", "reference_audio", "reference_audio_urls", "audio_urls",
            "audio_refs", "audio_path", "reference_audio_path", "ref_audio_path",
            "voice_reference_audio", "index_tts_reference_audio", "extra_audios",
            "audio_paths", "reference_audio_map", "audios", "audio",
        ),
    },
    "video": {
        "extensions": {".mp4", ".mov", ".webm", ".mkv"},
        "keys": (
            "reference_videos", "reference_video", "reference_video_urls", "video_urls",
            "video_refs", "video_path", "reference_video_path", "current_video",
            "selected_video", "extra_videos", "video_paths", "reference_video_map",
            "videos", "video",
        ),
    },
}


def _collect_reference_media(context: dict, media_kind: str, max_items: int = 3) -> list:
    """Collect ordered local paths or public URLs from common Zizi context fields."""
    config = _REFERENCE_MEDIA_CONFIG[media_kind]
    extensions = config["extensions"]
    paths = []

    def append_value(value, enforce_type=True):
        if value in (None, "") or len(paths) >= max_items:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                append_value(item, enforce_type)
            return
        if isinstance(value, dict):
            declared_type = str(value.get("media_type") or value.get("type") or "").lower()
            if declared_type and media_kind not in declared_type and declared_type not in {"voice", "tts"}:
                return
            direct_keys = (
                "path", f"{media_kind}_path", "url", f"{media_kind}_url",
                "file", "src", "value",
            )
            direct_value = next((value.get(key) for key in direct_keys if value.get(key)), None)
            if direct_value is not None:
                append_value(direct_value, enforce_type)
            else:
                for nested in value.values():
                    append_value(nested, enforce_type)
            return
        text = str(value).strip()
        if not text:
            return
        is_url = text.lower().startswith(("http://", "https://"))
        extension = os.path.splitext(text.split("?", 1)[0])[1].lower()
        if enforce_type and not is_url and extension not in extensions:
            return
        if text not in paths:
            paths.append(text)

    for key in config["keys"]:
        append_value(context.get(key))

    for item in context.get("reference_items", []) or []:
        if isinstance(item, dict):
            declared_type = str(item.get("media_type") or item.get("type") or "").lower()
            if media_kind in declared_type or (media_kind == "audio" and declared_type in {"voice", "tts"}):
                append_value(item)

    for container_key in ("reference_media", "media_items", "assets"):
        container = context.get(container_key)
        entries = list(container.values()) if isinstance(container, dict) else (container or [])
        if not isinstance(entries, (list, tuple, set)):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            declared_type = str(item.get("media_type") or item.get("type") or "").lower()
            if media_kind in declared_type or (media_kind == "audio" and declared_type in {"voice", "tts"}):
                append_value(item)

    for container_key in ("characters", "character_list", "roles"):
        container = context.get(container_key)
        entries = list(container.values()) if isinstance(container, dict) else (container or [])
        if not isinstance(entries, (list, tuple, set)):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in config["keys"]:
                append_value(entry.get(key))
            for item in entry.get("reference_items", []) or []:
                if isinstance(item, dict):
                    declared_type = str(item.get("media_type") or item.get("type") or "").lower()
                    if media_kind in declared_type or (media_kind == "audio" and declared_type in {"voice", "tts"}):
                        append_value(item)

    return paths[:max_items]



def _read_image_as_base64(image_path: str) -> str:
    """Read an image and convert it to a base64 data URI."""
    _log(f"  [鍙傚浘] 璇诲彇鍥剧墖: {image_path}")
    if not image_path:
        _log(f"  [鍙傚浘] -> 璺緞涓虹┖")
        return None
    clean = str(image_path).split("?")[0]
    _log(f"  [鍙傚浘] -> 娓呯悊鍚庤矾寰? {clean}")
    if not os.path.exists(clean):
        _log(f"  [鍙傚浘] -> 鏂囦欢涓嶅瓨鍦? {clean}")
        return None
    try:
        ext = os.path.splitext(clean)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
        mime = mime_map.get(ext, "image/png")
        file_size = os.path.getsize(clean)
        _log(f"  [鍙傚浘] -> 鏂囦欢澶у皬: {file_size / 1024:.2f} KB, MIME: {mime}")
        with open(clean, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        b64_len = len(b64)
        _log(f"  [鍙傚浘] -> Base64 缂栫爜鎴愬姛: {b64_len} 瀛楃 ({b64_len / 1024:.2f} KB)")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        _log(f"[鍙傚浘] 璇诲彇鍥剧墖澶辫触 {clean}: {e}")
        return None


def _collect_reference_images(reference_images: dict, mode: str = "multi_image") -> list:
    """Collect reference image paths according to reference mode."""
    _log(f"  [鍙傚浘] 寮€濮嬫敹闆嗗弬鑰冨浘鐗?.. 妯″紡: {mode}")
    _log(f"  [鍙傚浘] 杈撳叆鏁版嵁: {reference_images}")
    paths = []
    
    if mode == "first_frame":
        # 棣栧抚鍥炬ā寮忥細鍙彇棣栧抚鎴栧弬鑰冨浘鐗嘙AP涓殑绗?寮?
        _log(f"  [鍙傚浘] 棣栧抚鍥炬ā寮忥細鍙敹闆嗛甯у浘")
        # 浼樺厛鍙栭甯?
        first = reference_images.get("棣栧抚")
        if first and os.path.exists(str(first).split("?")[0]):
            paths.append(str(first).split("?")[0])
            _log(f"  [鍙傚浘]   棣栧抚宸插姞鍏? {paths[0]}")
        else:
            # 娌℃湁棣栧抚锛屽彇鍙傝€冨浘鐗嘙AP绗?寮?
            ref_map = reference_images.get("鍙傝€冨浘鐗嘙AP", {})
            if isinstance(ref_map, dict) and 1 in ref_map:
                p = ref_map[1]
                if p and os.path.exists(str(p).split("?")[0]):
                    paths.append(str(p).split("?")[0])
                    _log(f"  [鍙傚浘]   MAP[1] 宸插姞鍏? {paths[0]}")
            # 灏濊瘯瀛楃涓查敭 "1"
            elif isinstance(ref_map, dict) and "1" in ref_map:
                p = ref_map["1"]
                if p and os.path.exists(str(p).split("?")[0]):
                    paths.append(str(p).split("?")[0])
                    _log(f"  [鍙傚浘]   MAP['1'] 宸插姞鍏? {paths[0]}")
        if not paths:
            _log(f"  [鍙傚浘] 棣栧抚鍥炬ā寮忥細鏈壘鍒颁换浣曢甯у浘")
    
    else:
        # 澶氬浘妯″紡锛氭敹闆嗘墍鏈夊弬鑰冨浘鐗囷紙鍙傝€冨浘鐗嘙AP + 棣栧抚 + 灏惧抚锛?
        _log("  [reference] multi-image mode: collecting all reference images")
        ref_map = reference_images.get("鍙傝€冨浘鐗嘙AP", {})
        if isinstance(ref_map, dict):
            _log(f"  [鍙傚浘] 鍙傝€冨浘鐗嘙AP 閿? {list(ref_map.keys())}")
            for idx in sorted(ref_map.keys()):
                p = ref_map[idx]
                _log(f"  [鍙傚浘]   MAP[{idx}]: {p}")
                if p and os.path.exists(str(p).split("?")[0]):
                    paths.append(str(p).split("?")[0])
                    _log(f"  [鍙傚浘]     -> 鏈夋晥锛屽凡鍔犲叆")
                else:
                    _log(f"  [鍙傚浘]     -> 鏃犳晥鎴栨枃浠朵笉瀛樺湪")
        else:
            _log(f"  [鍙傚浘] 鍙傝€冨浘鐗嘙AP 涓嶆槸瀛楀吀: {type(ref_map)}")
        for key in ["棣栧抚", "灏惧抚"]:
            p = reference_images.get(key)
            _log(f"  [鍙傚浘] {key}: {p}")
            if p and os.path.exists(str(p).split("?")[0]) and str(p).split("?")[0] not in paths:
                paths.append(str(p).split("?")[0])
                _log(f"  [鍙傚浘]   -> 鏈夋晥锛屽凡鍔犲叆")
            elif p:
                _log("  [reference] skipped: file missing or duplicated")
    
    _log(f"  [鍙傚浘] 鏀堕泦瀹屾垚: {len(paths)} 寮? {paths}")
    return paths


# ===================== 鏍稿績鐢熸垚 =====================

def generate(context):
    """
    鎻掍欢涓诲嚱鏁帮細璋冪敤 OpenAI Sora 鍏煎 API 鐢熸垚瑙嗛銆?
    
    娴佺▼:
      1. POST /v1/videos      鈫?鎻愪氦浠诲姟
      2. GET  /v1/videos/{id}  鈫?杞鐘舵€?
      3. 涓嬭浇瑙嗛鏂囦欢
    """
    _log("=" * 80)
    _log("[NewAPI Video] start generation")
    
    try:
        plugin_params, disk_params, host_params = _merge_plugin_params(context.get("plugin_params"))
        
        api_key = str(plugin_params.get("api_key", "")).strip()
        base_url = str(plugin_params.get("base_url", "https://huiju.v888.art")).strip().rstrip("/")
        model = str(plugin_params.get("model", "sora-2")).strip()
        aspect_ratio = str(plugin_params.get("aspect_ratio", "16:9")).strip()
        duration = int(plugin_params.get("duration", 6))
        fps = int(plugin_params.get("fps", 24))
        n = int(plugin_params.get("n", 1))
        response_format = str(plugin_params.get("response_format", "url")).strip()
        resolution = str(plugin_params.get("resolution", "720p")).strip()
        timeout = int(plugin_params.get("timeout", 900))
        max_poll = int(plugin_params.get("max_poll_attempts", 300))
        poll_interval = int(plugin_params.get("poll_interval", 10))
        reference_mode = str(plugin_params.get("reference_mode", "first_frame")).strip()
        compliance_enabled = str(plugin_params.get("compliance_enabled", False)).strip().lower() in ("1", "true", "yes", "on")
        compliance_mode = _normalize_compliance_mode(plugin_params.get("compliance_mode"))
        
        prompt = context.get("prompt", "")
        project_path = context.get("project_path", ".")
        viewer_index = context.get("viewer_index", 1)
        progress_callback = context.get("progress_callback")
        reference_images = context.get("reference_images", {})
        _log(f"  [context] available keys: {sorted(str(key) for key in context.keys())}")
        
        _log(f"  [鍙傛暟] 瀹夸富 duration: {host_params.get('duration')}")
        _log(f"  [鍙傛暟] 纾佺洏 duration: {disk_params.get('duration')}")
        _log(f"  [鍙傛暟] 鏈€缁?duration: {duration}")
        _log(f"  [鍙傚浘] 鍙傚浘妯″紡: {reference_mode}")
        _log(f"  [鍙傚浘] 鍘熷鍙傚浘鏁版嵁: {reference_images}")
        _log(f"  [鍙傚浘] first_frame_path: {context.get('first_frame_path')}")
        _log(f"  [鍙傚浘] end_frame_path: {context.get('end_frame_path')}")
        
        # 鏍囧噯鍖?reference_images
        if reference_images and "鍙傝€冨浘鐗嘙AP" not in reference_images:
            if all(isinstance(k, int) or (isinstance(k, str) and k.isdigit()) for k in reference_images.keys()):
                reference_images = {"鍙傝€冨浘鐗嘙AP": reference_images.copy()}
        ref_map = reference_images.get("鍙傝€冨浘鐗嘙AP")
        if isinstance(ref_map, dict):
            reference_images["鍙傝€冨浘鐗嘙AP"] = {
                (int(k) if isinstance(k, str) and k.isdigit() else k): v
                for k, v in ref_map.items()
            }
        if context.get("first_frame_path"):
            reference_images["棣栧抚"] = context["first_frame_path"]
        if context.get("end_frame_path"):
            reference_images["灏惧抚"] = context["end_frame_path"]
        
        _log(f"  [鍙傚浘] 鏍囧噯鍖栧悗: {reference_images}")
        _log(f"  [鍙傚浘] 鍙傝€冨浘鐗嘙AP: {reference_images.get('鍙傝€冨浘鐗嘙AP', {})}")
        _log(f"  [鍙傚浘] 棣栧抚: {reference_images.get('棣栧抚')}")
        _log(f"  [鍙傚浘] 灏惧抚: {reference_images.get('灏惧抚')}")
        
        width, height = _ratio_to_size(aspect_ratio)
        
        _log(f"  Base URL: {base_url}")
        _log(f"  妯″瀷: {model}")
        _log(f"  API Key 鍓嶇紑: {api_key[:8]}... (闀垮害 {len(api_key)})")
        _log(f"  灏哄: {width}x{height} ({aspect_ratio})")
        _log(f"  鍒嗚鲸鐜? {resolution}")
        _log(f"  鏃堕暱: {duration}s")
        _log(f"  甯х巼: {fps}fps")
        _log(f"  鎻愮ず璇? {prompt[:100]}...")
        _log("=" * 80)

        
        if not api_key:
            raise Exception("PLUGIN_ERROR:::API Key is not configured")
        if not base_url:
            raise Exception("PLUGIN_ERROR:::Base URL is not configured")
        if not prompt:
            raise Exception("PLUGIN_ERROR:::Prompt is empty")
        
        # ===== 1. 鎻愪氦瑙嗛鐢熸垚浠诲姟 =====
        is_schat_fast_9ref = _is_schat_sd20_fast_9ref_model(model)
        is_seedream = _is_seedream_model(model) and not is_schat_fast_9ref
        is_chre_seedance = _is_chre_seedance_model(model)
        endpoint = f"{base_url}/v1/video/generations" if is_seedream else f"{base_url}/v1/videos"
        image_urls = []
        
        # duration 闄愬埗锛氭寜妯″瀷鍖哄垎
        # grok-imagine-video-1.5 / preview: supports up to 15 seconds
        # grok-imagine-1.0-video / grok-imagine-video-1.5-fast: 鍙敮鎸?6 鎴?10
        if is_schat_fast_9ref:
            if duration != 15:
                _log(f"  [SChat SD2.0 Fast 9鍥惧弬] 鏃堕暱鍥哄畾涓?15 绉掞紝宸茶嚜鍔ㄨ皟鏁? {duration} -> 15")
            duration = 15
        elif is_seedream:
            duration = _normalize_seedream_duration(model, duration)
            aspect_ratio = _normalize_seedream_aspect_ratio(aspect_ratio)
            _log("  [Seedream] 宸茶瘑鍒负 Seedream.20/sudashui 妯″瀷锛屼娇鐢?/v1/video/generations")
        elif is_chre_seedance:
            if duration < 5:
                _log("  [CHRE Seedance] 鏃堕暱灏忎簬 5 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 5")
                duration = 5
            elif duration > 15:
                _log("  [CHRE Seedance] 鏃堕暱澶т簬 15 绉掞紝宸茶嚜鍔ㄨ皟鏁翠负 15")
                duration = 15
        elif "1.5-preview" in model:
            if duration < 1:
                duration = 1
            elif duration > 15:
                duration = 15
        elif _is_grok_imagine_model(model):
            duration = _normalize_grok_duration(model, duration)
        else:
            duration = _normalize_xingyao_duration(model, duration)

        payload = {
            "model": model,
            "prompt": prompt,
            "seconds": str(duration),          # Grok 鏂囨。鏀寔瀛楃涓叉垨 number
            "duration": duration,
            "size": _ratio_to_video_size(aspect_ratio, resolution),
            "aspect_ratio": aspect_ratio,        # 鐩存帴浼犳瘮渚嬪瓧绗︿覆
            "resolution": resolution,            # 480p / 720p
        }
        if is_schat_fast_9ref:
            payload = {
                "model": model,
                "prompt": prompt,
                "seconds": "15",
                "size": _ratio_to_video_size(aspect_ratio, resolution),
            }
        if is_chre_seedance:
            payload = {
                "model": model,
                "prompt": prompt,
                "duration": duration,
                "size": _ratio_to_video_size(aspect_ratio, "720p"),
                "aspect_ratio": aspect_ratio,
            }
            if compliance_enabled:
                payload["compliance_enabled"] = True
                payload["compliance_mode"] = compliance_mode
                _log(f"  [CHRE Seedance] 宸插紑鍚繃鐪熶汉/鍚堣绱犳潗: {compliance_mode}")
        if is_seedream:
            seedream_meta = {
                "aspectRatio": aspect_ratio,
                "mode": "references",
            }
            payload = {
                "model": model,
                "prompt": prompt,
                "duration": duration,
                "metadata": {"payload": json.dumps(seedream_meta, ensure_ascii=False)},
            }

        # 澶勭悊鍙傝€冨浘鐗囷細鍏堜笂浼犲埌鍥惧簥鑾峰彇鍏綉 URL锛屽啀浼犵粰 Grok
        image_host_url = str(plugin_params.get("image_host_url", "")).strip()
        image_host_token = str(plugin_params.get("image_host_token", "")).strip()
        image_host_timeout = int(plugin_params.get("image_host_timeout", 60) or 60)
        if not image_host_url or "img-worker.v888.art" in image_host_url:
            image_host_url = _DEFAULT_PARAMS["image_host_url"]
            _log(f"  [鍥惧簥] 宸蹭娇鐢ㄩ粯璁よ崯鑱氬浘搴? {image_host_url}")
        if not image_host_token or image_host_token == "huiju123456":
            image_host_token = _DEFAULT_PARAMS["image_host_token"]

        is_xingqi_mini = model.strip().lower() == "xingqi-mini"
        collect_mode = "multi_image" if is_schat_fast_9ref else reference_mode
        if is_schat_fast_9ref and reference_mode != "multi_image":
            _log("  [SChat SD2.0 Fast 9ref] switched to multi-image reference mode")
        ref_paths = _collect_reference_images(reference_images, collect_mode)
        _log(f"  [鍙傚浘] 鏈€缁堟敹闆嗗埌鍙傝€冨浘璺緞: {ref_paths}")
        multipart_ref_paths = []
        if ref_paths and is_schat_fast_9ref:
            multipart_ref_paths = [p for p in ref_paths if p and os.path.isfile(p)][:9]
            if len(ref_paths) > 9:
                _log(f"  [SChat SD2.0 Fast 9鍥惧弬] 鏈€澶氭敮鎸?9 寮犲弬鑰冨浘锛屽凡鎴柇: {len(ref_paths)} -> 9")
            _log(f"  [SChat SD2.0 Fast 9鍥惧弬] 灏嗙洿鎺?multipart 涓婁紶 {len(multipart_ref_paths)} 寮?input_reference")
        elif ref_paths:
            if not image_host_url:
                raise Exception("PLUGIN_ERROR:::Image host URL is not configured")


            if is_xingqi_mini and len(ref_paths) > 7:
                _log(f"  [xingqi-mini] supports up to 7 reference images, truncate {len(ref_paths)} -> 7")
                ref_paths = ref_paths[:7]
            if is_chre_seedance and len(ref_paths) > 9:
                _log(f"  [CHRE Seedance] image_refs 鏈€澶?9 寮狅紝宸叉埅鏂? {len(ref_paths)} -> 9")
                ref_paths = ref_paths[:9]

            # 1.5-preview supports one reference image.
            if "1.5-preview" in model and len(ref_paths) > 1:
                _log(f"  [reference] model {model} supports 1 reference image, truncate to first")
                ref_paths = ref_paths[:1]

            if progress_callback:
                progress_callback("上传参考图到图床...")

            image_urls = []
            for p in ref_paths:
                url = _upload_image_to_host(p, image_host_url, image_host_token, image_host_timeout)
                if url:
                    image_urls.append(url)

            if not image_urls:
                raise Exception("PLUGIN_ERROR:::所有参考图上传失败")

            _log(f"  [鍙傚浘] 鎴愬姛涓婁紶 {len(image_urls)} 寮犲浘鐗囧埌鍥惧簥")

            if is_chre_seedance:
                payload["image_refs"] = image_urls
                if image_urls and "@Image1" not in prompt:
                    placeholders = " ".join([f"@Image{i+1}" for i in range(len(image_urls))])
                    prompt = f"{placeholders} {prompt}"
                    payload["prompt"] = prompt
                    _log(f"  [CHRE Seedance] 鑷姩娣诲姞鍥剧墖寮曠敤鍒?prompt: {placeholders}")
                _log(f"  [CHRE Seedance] 浣跨敤 image_refs 浼犻€?{len(image_urls)} 寮犲弬鑰冨浘")
            elif is_seedream:
                seedream_meta = {
                    "aspectRatio": aspect_ratio,
                    "mode": "references",
                    "imageUrls": image_urls,
                }
                payload["metadata"] = {"payload": json.dumps(seedream_meta, ensure_ascii=False)}
                if len(image_urls) > 1 and "@image1" not in prompt:
                    placeholders = " ".join([f"@image{i+1}" for i in range(len(image_urls))])
                    prompt = f"{placeholders} {prompt}"
                    payload["prompt"] = prompt
                    _log(f"  [Seedream] 鑷姩娣诲姞鍥剧墖寮曠敤鍒?prompt: {placeholders}")
                _log("  [Seedream] 浣跨敤 metadata.payload.imageUrls 浼犻€?URL")
            else:
                payload["reference_images"] = image_urls
                if is_xingqi_mini:
                    _log(f"  [xingqi-mini] 使用上游文档字段 reference_images 传递 {len(image_urls)} 张参考图")
                else:
                    payload["images"] = image_urls
                    payload["image_urls"] = image_urls
                    _log(f"  [参图] 使用 reference_images/images/image_urls 字段传递 URL")

                # 澶氬浘鏃惰嚜鍔ㄨˉ鍏ㄥ崰浣嶇
                if len(image_urls) > 1:
                    if is_xingqi_mini:
                        has_placeholder = any(f"[@image{i}]" in prompt or f"@image{i}" in prompt for i in range(1, len(image_urls)+1))
                    else:
                        has_placeholder = any(f"<IMAGE_{i}>" in prompt for i in range(1, len(image_urls)+1))
                    if not has_placeholder:
                        placeholders = " ".join([f"[@image{i+1}]" for i in range(len(image_urls))]) if is_xingqi_mini else ", ".join([f"<IMAGE_{i+1}>" for i in range(len(image_urls))])
                        prompt = f"{placeholders} reference images. {prompt}"
                        payload["prompt"] = prompt
                        _log(f"  [鍙傚浘] 鑷姩娣诲姞鍗犱綅绗﹀埌 prompt: {placeholders}")
        else:
            _log("  [reference] no reference images, text-to-video mode")

        # Collect audio/video nodes exposed by different Zizi versions, upload
        # local files to the configured public media host, then use Huiju's
        # stable downstream contract. NewAPI maps these fields per provider.
        for media_kind, payload_key, progress_text in (
            ("audio", "audio_urls", "上传参考音频..."),
            ("video", "video_urls", "上传参考视频..."),
        ):
            media_paths = _collect_reference_media(context, media_kind, max_items=3)
            _log(f"  [reference-{media_kind}] collected {len(media_paths)} item(s): {media_paths}")
            if not media_paths:
                continue
            if progress_callback:
                progress_callback(progress_text)
            media_urls = []
            for media_path in media_paths:
                resolved_path = media_path
                if not str(media_path).lower().startswith(("http://", "https://")):
                    clean_path = str(media_path).split("?", 1)[0]
                    if not os.path.isabs(clean_path) and project_path:
                        project_candidate = os.path.join(project_path, clean_path)
                        if os.path.exists(project_candidate):
                            resolved_path = project_candidate
                temporary_audio_path = None
                try:
                    if media_kind == "audio" and _requires_wav_reference_audio(model):
                        if str(resolved_path).lower().startswith(("http://", "https://")):
                            url_path = str(resolved_path).split("?", 1)[0]
                            if os.path.splitext(url_path)[1].lower() != ".wav":
                                raise Exception("PLUGIN_ERROR:::seedance-2.0-mini 仅支持 WAV 参考音频，请提供 WAV 公网地址")
                        elif os.path.splitext(str(resolved_path).split("?", 1)[0])[1].lower() != ".wav":
                            temporary_audio_path = _convert_reference_audio_to_wav(resolved_path)
                            resolved_path = temporary_audio_path
                    media_urls.append(
                        _upload_image_to_host(resolved_path, image_host_url, image_host_token, max(image_host_timeout, 180))
                    )
                finally:
                    if temporary_audio_path:
                        try:
                            os.unlink(temporary_audio_path)
                        except OSError:
                            pass
            if media_urls:
                payload[payload_key] = media_urls
                _log(f"  [reference-{media_kind}] attached {len(media_urls)} public URL(s) as {payload_key}")
        
        headers = {"Authorization": f"Bearer {api_key}"}
        if not is_schat_fast_9ref:
            headers["Content-Type"] = "application/json"
        
        if progress_callback:
            progress_callback("提交任务中...")
        
        # 璁＄畻璇锋眰浣撳ぇ灏忕敤浜庤瘖鏂?
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
            req_size_kb = len(payload_json.encode('utf-8')) / 1024
            _log(f"  [NewAPI] 璇锋眰浣撳ぇ灏? {req_size_kb:.2f} KB")
            _log(f"  [NewAPI] final request JSON: {payload_json}")
        except Exception:
            pass
        
        _log(f"  鎻愪氦浠诲姟: POST {endpoint}")
        _log(f"  [NewAPI] 璇锋眰 Headers: Authorization=Bearer {api_key[:8]}...")
        
        _log(f"  [NewAPI] 鎻愪氦璇锋眰浣撴憳瑕?")
        _log(f"    model: {payload.get('model')}")
        _log(f"    seconds: {payload.get('seconds')}")
        _log(f"    duration: {payload.get('duration')}")
        _log(f"    size: {payload.get('size')}")
        _log(f"    aspect_ratio: {payload.get('aspect_ratio')}")
        _log(f"    metadata: {payload.get('metadata')}")
        _log(f"    resolution: {payload.get('resolution')}")
        _log(f"    prompt: {payload.get('prompt', '')[:200]}...")
        _log(f"    has_images: {payload.get('images') is not None}")
        _log(f"    images_count: {len(payload.get('images', []))}")
        _log(f"    audio_urls_count: {len(payload.get('audio_urls', []))}")
        _log(f"    video_urls_count: {len(payload.get('video_urls', []))}")
        
        if is_schat_fast_9ref:
            opened_files = []
            multipart_files = []
            try:
                for image_path in multipart_ref_paths:
                    file_obj = open(image_path, "rb")
                    opened_files.append(file_obj)
                    mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
                    multipart_files.append(
                        ("input_reference", (os.path.basename(image_path), file_obj, mime_type))
                    )
                resp = requests.post(
                    endpoint,
                    headers=headers,
                    data=payload,
                    files=multipart_files or None,
                    timeout=300,
                    proxies={"http": None, "https": None},
                )
            finally:
                for file_obj in opened_files:
                    file_obj.close()
        else:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=300,
                                 proxies={"http": None, "https": None})
        
        _log(f"  [NewAPI] 鎻愪氦鍝嶅簲鐘舵€? {resp.status_code}")
        if resp.status_code != 200:
            try:
                err = resp.json()
                _log(f"  [NewAPI] 鎻愪氦閿欒璇︽儏: {err}")
            except Exception:
                _log(f"  [NewAPI] 鎻愪氦閿欒鏂囨湰: {resp.text[:500]}")
            try:
                err = resp.json()
            except Exception:
                err = resp.text[:500]
            # 閽堝 403 杈撳嚭棰濆璇婃柇淇℃伅
            if resp.status_code == 403:
                _log("  [diagnostic] 403 permission denied")
                _log(f"  [diagnostic] model: {model}")
                _log("  [diagnostic] check API key group permissions")
                _log("  [diagnostic] check channel group/model settings in NewAPI")
                _log("  [diagnostic] try another model or key with proper group permissions")
            raise Exception(f"PLUGIN_ERROR:::API 错误 {resp.status_code}: {err}")
        
        result = resp.json()
        _log(f"  鎻愪氦鍝嶅簲: {json.dumps(result, ensure_ascii=False)[:500]}")
        
        task_id = result.get("task_id") or result.get("id")
        if not task_id:
            raise Exception(f"PLUGIN_ERROR:::API 响应中缺少任务 ID: {result}")
        
        _log(f"  浠诲姟 ID: {task_id}")
        
        # ===== 2. 杞浠诲姟鐘舵€?=====
        if progress_callback:
            progress_callback("生成中...", 0)
        
        status_endpoint = f"{base_url}/v1/video/generations/{task_id}" if is_seedream else f"{base_url}/v1/videos/{task_id}"
        video_url = None
        error_count = 0
        max_errors = 5
        
        for attempt in range(max_poll):
            time.sleep(poll_interval)
            
            try:
                status_resp = requests.get(
                    status_endpoint, headers=headers, timeout=60,
                    proxies={"http": None, "https": None}
                )
            except Exception as e:
                error_count += 1
                _log(f"  杞寮傚父 ({error_count}/{max_errors}): {e}")
                if error_count >= max_errors:
                    raise Exception(f"PLUGIN_ERROR:::Polling failed {max_errors} times")
                continue
            
            if status_resp.status_code != 200:
                error_count += 1
                _log(f"  鐘舵€佹煡璇㈠け璐?({error_count}/{max_errors}): {status_resp.status_code}")
                if error_count >= max_errors:
                    raise Exception(f"PLUGIN_ERROR:::Status query failed {max_errors} times")
                continue
            
            error_count = 0
            status_data = status_resp.json()
            if is_seedream:
                status = _seedream_status_value(status_data)
                progress_pct = _seedream_progress_value(status_data)
            else:
                status = (
                    status_data.get("status")
                    or status_data.get("original_status")
                    or status_data.get("state")
                    or ""
                )
                progress_pct = status_data.get("progress")
            
            _log(f"  [{attempt+1}/{max_poll}] 鐘舵€? {status}, 杩涘害: {progress_pct}")
            _log(f"  [NewAPI] 鐘舵€佸搷搴旀憳瑕? {json.dumps(status_data, ensure_ascii=False)[:300]}")
            status_key = str(status or "").strip().lower()
            if status_key in ("done", "complete", "success", "succeeded", "finished"):
                status_key = "completed"
            elif status_key in ("running", "generating"):
                status_key = "processing"
            elif status_key in ("failure", "error", "cancelled", "canceled") and not is_seedream:
                status_key = "failed"
            

            if progress_callback:
                if progress_pct is not None:
                    try:
                        progress_callback(f"生成中 ({progress_pct}%)", int(progress_pct))
                    except Exception:
                        progress_callback(f"生成中 ({progress_pct})")
                elif status_key in ("pending", "queued", "submitted"):
                    progress_callback("排队中...")
                elif status_key in ("processing", "in_progress"):
                    progress_callback("生成中...")
            
            if is_seedream and status_key in ("success", "completed", "succeeded"):
                video_url = _seedream_result_url(status_data)
                _log(f"  [Seedream] 瑙嗛鐢熸垚瀹屾垚: {video_url}")
                break

            if status_key == "completed":
                # 浼樺厛浠?output 瀛楁鑾峰彇
                output = status_data.get("output")
                if isinstance(output, dict):
                    video_url = output.get("url")
                if not video_url:
                    video_url = status_data.get("video_url") or status_data.get("url")
                if not video_url and is_schat_fast_9ref:
                    video_url = f"{base_url}/v1/videos/{task_id}/content"
                
                _log(f"  瑙嗛鐢熸垚瀹屾垚: {video_url}")
                break
            
            elif status_key == "failed":
                error_info = status_data.get("error", {})
                if isinstance(error_info, dict):
                    fail_msg = error_info.get("message", "未知错误")
                else:
                    fail_msg = str(error_info) if error_info else "任务失败"
                raise Exception(f"PLUGIN_ERROR:::视频生成失败: {fail_msg}")
            elif is_seedream and status_key in ("failure", "cancelled", "canceled"):
                raise Exception(f"PLUGIN_ERROR:::视频生成失败: {_seedream_failure_reason(status_data)}")
        else:
            raise Exception(f"PLUGIN_ERROR:::超过最大轮询次数 ({max_poll})，视频未生成")
        
        if not video_url:
            raise Exception("PLUGIN_ERROR:::任务完成但未获取到视频 URL")
        
        # ===== 3. 涓嬭浇瑙嗛 =====
        if progress_callback:
            progress_callback("下载中...", 99)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        video_name = f"{viewer_index:04d}_video_{timestamp}.mp4"
        video_path = os.path.join(project_path, video_name)
        os.makedirs(project_path, exist_ok=True)
        
        download_success = False
        
        # 鏂瑰紡1: 鐩存帴涓嬭浇 URL
        dl_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        }
        if str(video_url).startswith(base_url):
            dl_headers["Authorization"] = f"Bearer {api_key}"
        try:
            _log(f"  涓嬭浇瑙嗛: {video_url}")
            dl_resp = requests.get(video_url, headers=dl_headers, timeout=1800, stream=True)
            if dl_resp.status_code == 200:
                total = 0
                with open(video_path, "wb") as f:
                    for chunk in dl_resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            total += len(chunk)
                _log(f"  涓嬭浇瀹屾垚: {video_path} ({total / (1024*1024):.2f} MB)")
                download_success = True
        except Exception as e:
            _log(f"  URL 涓嬭浇澶辫触: {e}")
        
        # 鏂瑰紡2: 閫氳繃 content API
        if not download_success:
            try:
                content_endpoint = f"{base_url}/v1/videos/{task_id}/content"
                _log(f"  澶囩敤涓嬭浇: {content_endpoint}")
                dl_resp = requests.get(content_endpoint, headers=headers, timeout=1800, stream=True,
                                      proxies={"http": None, "https": None})
                if dl_resp.status_code == 200:
                    total = 0
                    with open(video_path, "wb") as f:
                        for chunk in dl_resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                total += len(chunk)
                    _log(f"  澶囩敤涓嬭浇瀹屾垚: {video_path} ({total / (1024*1024):.2f} MB)")
                    download_success = True
            except Exception as e:
                _log(f"  澶囩敤涓嬭浇澶辫触: {e}")
        
        if not download_success:
            raise Exception("PLUGIN_ERROR:::视频下载失败，请检查网络或稍后重试")

        if progress_callback:
            progress_callback("完成", 100)
        
        _log(f"[NewAPI Video] 鐢熸垚瀹屾垚: {video_path}")
        _log("=" * 80)
        return [video_path]
    
    except Exception as e:
        error_msg = str(e)
        _log(f"[NewAPI Video] 鐢熸垚鍑洪敊: {error_msg}")
        if error_msg.startswith("PLUGIN_ERROR:::"):
            raise
        traceback.print_exc()
        raise Exception(f"PLUGIN_ERROR:::{error_msg}")


# ===================== 鎻掍欢鎺ュ彛 =====================

def get_info():
    """Return plugin info."""
    return {
        "name": "NewAPI OpenAI 瑙嗛鎻掍欢",
        "description": (
            "OpenAI-compatible video plugin for NewAPI.\n"
            "Supports custom Base URL, API key, and /v1/models model list.\n"
            "Supports reference image, audio, and video upload through the configured media host."
        ),
        "version": _PLUGIN_VERSION,
        "author": "",
    }


def get_params():
    """Load current plugin parameters from config."""
    params = _DEFAULT_PARAMS.copy()
    params.update(load_plugin_config(_PLUGIN_FILE))
    return params


def handle_action(action, data=None):
    """Handle custom actions from the frontend."""
    if data is None:
        data = {}
    
    if action == "fetch_models":
        base_url = str(data.get("base_url", "")).strip().rstrip("/")
        api_key = str(data.get("api_key", "")).strip()
        timeout = int(data.get("timeout", 15))
        
        if not base_url:
            return {"ok": False, "error": "Base URL 涓嶈兘涓虹┖"}
        
        _log(f"[NewAPI Video] 姝ｅ湪浠?{base_url}/v1/models 鑾峰彇妯″瀷鍒楄〃...")
        result = _fetch_models_from_api(base_url, api_key, timeout)
        
        if result.get("ok"):
            models = result.get("models", [])
            default_model = result.get("default_model", models[0] if models else "")
            _log(f"[NewAPI Video] fetched {len(models)} models")
            
            # 淇濆瓨鍒?config.json
            try:
                update_plugin_params(_PLUGIN_FILE, {
                    "model_list": json.dumps(models, ensure_ascii=False),
                    "model_list_updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "model_list_default": default_model,
                })
                _log(f"[NewAPI Video] 妯″瀷鍒楄〃宸蹭繚瀛樺埌閰嶇疆")
            except Exception as save_err:
                _log(f"[NewAPI Video] 淇濆瓨妯″瀷鍒楄〃澶辫触: {save_err}")
            
            return {
                "ok": True,
                "models": models,
                "default_model": default_model,
            }
        else:
            _log(f"[NewAPI Video] 鑾峰彇妯″瀷鍒楄〃澶辫触: {result.get('error')}")
            return {"ok": False, "error": result.get("error", "鏈煡閿欒")}
    
    elif action == "check_update":
        result = _get_latest_release(_UPDATE_REPO, int(data.get("timeout", 20) or 20))
        if result.get("ok") and not result.get("has_update"):
            result["message"] = "已经是最新版本"
        return result

    elif action == "apply_update":
        asset_name = str(data.get("asset_name") or get_params().get("update_asset_name") or "").strip()
        return _apply_github_update(_UPDATE_REPO, asset_name)

    else:
        return {"ok": False, "error": f"鏈煡鍔ㄤ綔: {action}"}


# ===================== 瀹炴椂鏃ュ織宸ュ叿 =====================

def _log_progress(callback, msg, percent=None):
    """Log to file and send progress callback."""
    _log(msg)
    if callback:
        if percent is not None:
            callback(msg, int(percent))
        else:
            callback(msg)



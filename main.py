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
import shlex
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


def _get_recent_logs(lines=100):
    """Read recent plugin log lines."""
    try:
        log_dir = plugin_dir / "logs"
        log_file = log_dir / f"debug_{datetime.now().strftime('%Y%m%d')}.log"
        if not log_file.exists():
            return []
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return all_lines[-lines:]
    except Exception:
        return []


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


def _get_latest_release(repo: str, timeout: int = 20) -> dict:
    repo = _normalize_update_repo(repo)
    if not repo or "/" not in repo:
        return {"ok": False, "error": "请先填写 GitHub 仓库，例如 owner/repo"}
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "huiju-video-plugin-updater"}
    resp = requests.get(url, headers=headers, timeout=timeout, proxies={"http": None, "https": None})
    if resp.status_code != 200:
        return {"ok": False, "error": f"GitHub 返回 HTTP {resp.status_code}: {resp.text[:200]}"}
    release = resp.json()
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


def _apply_github_update(repo: str, preferred_asset_name: str = "") -> dict:
    release = _get_latest_release(repo)
    if not release.get("ok"):
        return release
    if not release.get("has_update"):
        return {"ok": True, "updated": False, **release}
    asset = _choose_update_asset(release, preferred_asset_name)
    if not asset:
        return {"ok": False, "error": "最新 Release 没有可下载的 zip 资源", **release}

    with tempfile.TemporaryDirectory(prefix="huiju_plugin_update_") as temp_name:
        temp_dir = Path(temp_name)
        zip_path = temp_dir / (asset.get("name") or "update.zip")
        _log(f"[update] downloading {asset.get('download_url')}")
        with requests.get(asset["download_url"], stream=True, timeout=180, proxies={"http": None, "https": None}) as resp:
            if resp.status_code != 200:
                return {"ok": False, "error": f"下载失败 HTTP {resp.status_code}: {resp.text[:200]}", **release}
            with open(zip_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        fh.write(chunk)
        extract_dir = temp_dir / "extract"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        candidates = [extract_dir]
        candidates.extend([p for p in extract_dir.rglob("*") if p.is_dir()])
        source_dir = None
        for candidate in candidates:
            if (candidate / "main.py").exists() and (candidate / "ui" / "index.html").exists():
                source_dir = candidate
                break
        if source_dir is None:
            return {"ok": False, "error": "更新包内没有找到插件 main.py 和 ui/index.html", **release}

        backup_dir = _copy_plugin_update(source_dir)
        update_plugin_params(_PLUGIN_FILE, {
            "update_repo": _normalize_update_repo(repo),
            "update_asset_name": asset.get("name") or "",
            "last_update_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return {"ok": True, "updated": True, "backup_dir": str(backup_dir), "asset": asset.get("name") or "", **release}


def _merge_plugin_params(context_params):
    """Prefer the saved config for core settings to avoid stale host-side params."""
    disk_params = get_params()
    merged = {}
    if isinstance(context_params, dict):
        merged.update(context_params)
    for key, value in disk_params.items():
        if value not in (None, ""):
            merged[key] = value
    return merged, disk_params, (context_params if isinstance(context_params, dict) else {})


_PLUGIN_FILE = __file__
_PLUGIN_VERSION = "1.2.2"

# ===================== 榛樿鍙傛暟 =====================

_DEFAULT_PARAMS = {
    "api_key": "",
    "base_url": "https://api.openai.com",
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
    # 鍥哄畾浣嶇疆姘村嵃娓呯悊锛氫笅杞藉畬鎴愬悗璋冪敤鏈湴 FFmpeg delogo 宸ュ叿浜屾澶勭悊瑙嗛
    "watermark_remove_enabled": False,
    "watermark_tool_path": "",
    "watermark_masks": "",
    "watermark_crf": 18,
    "watermark_preset": "medium",
    "face_cover_enabled": True,
    "face_tool_path": r"G:\自动人脸处理工具\face_tool.py",
    "face_tool_python": "py -3.10",
    "face_black_y_offset": 8,
    "face_black_height": 36,
    "face_black_pad_x": 20,
    "face_paste_eyes_enabled": True,
    "update_repo": "",
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


def _parse_watermark_masks(mask_text: str):
    masks = []
    for raw_item in str(mask_text or "").replace("\n", ";").split(";"):
        item = raw_item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split(",")]
        if len(parts) != 4:
            raise ValueError(f"閬僵鍧愭爣鏍煎紡閿欒: {item}锛屽簲涓?x,y,w,h")
        nums = [int(float(p)) for p in parts]
        if nums[0] < 0 or nums[1] < 0 or nums[2] <= 0 or nums[3] <= 0:
            raise ValueError(f"閬僵鍧愭爣涓嶈兘涓鸿礋锛屽楂樺繀椤诲ぇ浜?0: {item}")
        masks.append(",".join(str(n) for n in nums))
    return masks


def _resolve_watermark_tool_path(configured_path: str) -> str:
    configured = str(configured_path or "").strip().strip('"')
    candidates = []
    if configured:
        candidates.append(Path(configured))

    candidates.extend([
        plugin_dir / "video-watermark-batch-tool" / "remove_watermark.py",
        plugin_dir.parent / "video-watermark-batch-tool" / "remove_watermark.py",
        plugin_dir.parent.parent / "video-watermark-batch-tool" / "remove_watermark.py",
    ])

    for candidate in candidates:
        try:
            path = candidate.expanduser().resolve()
            if path.is_file():
                return str(path)
        except Exception:
            continue
    return configured


def _remove_video_watermark(video_path: str, plugin_params: dict) -> str:
    enabled = str(plugin_params.get("watermark_remove_enabled", False)).strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        return video_path

    tool_path = _resolve_watermark_tool_path(str(plugin_params.get("watermark_tool_path") or ""))
    if not tool_path:
        _log("  [watermark] enabled but remove_watermark.py was not found, skipped")
        return video_path
    if not os.path.exists(tool_path):
        _log(f"  [鍘绘按鍗癩 宸ュ叿涓嶅瓨鍦紝璺宠繃: {tool_path}")
        return video_path

    try:
        masks = _parse_watermark_masks(str(plugin_params.get("watermark_masks") or ""))
    except Exception as exc:
        _log(f"  [鍘绘按鍗癩 閬僵閰嶇疆閿欒锛岃烦杩? {exc}")
        return video_path
    if not masks:
        _log("  [鍘绘按鍗癩 宸插紑鍚絾鏈厤缃伄缃╁潗鏍囷紝璺宠繃")
        return video_path

    source = Path(video_path)
    target = source.with_name(f"{source.stem}_clean{source.suffix}")
    crf = int(plugin_params.get("watermark_crf", 18) or 18)
    preset = str(plugin_params.get("watermark_preset", "medium") or "medium").strip()
    if preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
        preset = "medium"

    command = [
        sys.executable,
        tool_path,
        "--input",
        str(source),
        "--output",
        str(target),
        "--overwrite",
        "--crf",
        str(crf),
        "--preset",
        preset,
    ]
    for mask in masks:
        command.extend(["--mask", mask])

    _log(f"  [鍘绘按鍗癩 寮€濮嬪鐞? {source.name} -> {target.name}")
    _log(f"  [鍘绘按鍗癩 閬僵: {'; '.join(masks)}, crf={crf}, preset={preset}")
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
        )
    except Exception as exc:
        _log(f"  [鍘绘按鍗癩 璋冪敤澶辫触锛屼繚鐣欏師瑙嗛: {exc}")
        return video_path

    if result.stdout:
        _log(f"  [鍘绘按鍗癩 杈撳嚭: {result.stdout.strip()[:500]}")
    if result.stderr:
        _log(f"  [鍘绘按鍗癩 閿欒杈撳嚭: {result.stderr.strip()[:500]}")
    if result.returncode != 0 or not target.exists():
        _log(f"  [鍘绘按鍗癩 澶勭悊澶辫触锛屼繚鐣欏師瑙嗛锛岄€€鍑虹爜: {result.returncode}")
        return video_path

    _log(f"  [鍘绘按鍗癩 澶勭悊瀹屾垚: {target}")
    return str(target)


def _resolve_face_tool_path(configured_path: str) -> str:
    configured = str(configured_path or "").strip().strip('"')
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([
        plugin_dir / "face_tool.py",
        Path(r"G:\自动人脸处理工具\face_tool.py"),
    ])
    for candidate in candidates:
        try:
            path = candidate.expanduser().resolve()
            if path.is_file():
                return str(path)
        except Exception:
            continue
    return configured


def _face_python_command(configured_python: str) -> list:
    configured = str(configured_python or "").strip()
    if configured:
        try:
            return shlex.split(configured, posix=False)
        except Exception:
            return [configured]
    return [sys.executable]


def _cover_reference_image_faces(image_path: str, project_path: str, plugin_params: dict) -> str:
    enabled = str(plugin_params.get("face_cover_enabled", True)).strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        return image_path

    source = Path(str(image_path).split("?")[0])
    if not source.is_file():
        return image_path

    tool_path = _resolve_face_tool_path(str(plugin_params.get("face_tool_path") or ""))
    if not tool_path or not os.path.exists(tool_path):
        _log(f"  [face-cover] enabled but face_tool.py was not found, skipped: {tool_path}")
        return image_path

    try:
        output_dir = Path(project_path or source.parent) / ".huiju_face_cover"
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        output_dir = source.parent / ".huiju_face_cover"
        output_dir.mkdir(parents=True, exist_ok=True)

    destination = output_dir / f"{source.stem}_eyes_covered{source.suffix.lower()}"
    if Path(tool_path).suffix.lower() == ".exe":
        command = [tool_path]
    else:
        command = _face_python_command(str(plugin_params.get("face_tool_python") or "")) + [tool_path]
    command.extend([
        "--cli",
        "--input",
        str(source),
        "--output",
        str(destination),
        "--mode",
        "eye-all",
        "--black-y-offset",
        str(int(plugin_params.get("face_black_y_offset", 8) or 8)),
        "--black-height",
        str(int(plugin_params.get("face_black_height", 36) or 36)),
        "--black-pad-x",
        str(int(plugin_params.get("face_black_pad_x", 20) or 20)),
    ])
    paste_enabled = str(plugin_params.get("face_paste_eyes_enabled", True)).strip().lower() in ("1", "true", "yes", "on")
    if not paste_enabled:
        command.append("--no-paste")

    _log(f"  [face-cover] processing reference image: {source}")
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except Exception as exc:
        _log(f"  [face-cover] failed to call face tool, keeping original: {exc}")
        return image_path

    if result.stdout:
        _log(f"  [face-cover] output: {result.stdout.strip()[:500]}")
    if result.stderr:
        _log(f"  [face-cover] stderr: {result.stderr.strip()[:500]}")
    if result.returncode != 0 or not destination.exists():
        _log(f"  [face-cover] processing failed, keeping original. returncode={result.returncode}")
        return image_path

    _log(f"  [face-cover] using processed reference image: {destination}")
    return str(destination)


def _cover_reference_image_faces_batch(ref_paths: list, project_path: str, plugin_params: dict) -> list:
    processed = []
    for path in ref_paths:
        processed_path = _cover_reference_image_faces(path, project_path, plugin_params)
        processed.append(processed_path)
    return processed


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
    """Upload a local image to the image host and return a public URL."""
    _log(f"  [鍥惧簥] 寮€濮嬩笂浼? {image_path}")
    clean = str(image_path).split("?")[0]
    if not os.path.exists(clean):
        raise Exception(f"PLUGIN_ERROR:::鍥剧墖鏂囦欢涓嶅瓨鍦? {clean}")
    if not host_url:
        raise Exception("PLUGIN_ERROR:::鍥惧簥鍦板潃鏈厤缃紝璇峰湪鎻掍欢璁剧疆涓～鍐?Image Host URL")

    try:
        ext = os.path.splitext(clean)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
        mime = mime_map.get(ext, "image/png")
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
            raise Exception(f"PLUGIN_ERROR:::鍥惧簥涓婁紶澶辫触 {resp.status_code}: {detail}")

        try:
            data = resp.json()
        except Exception:
            # 濡傛灉鎺ュ彛杩斿洖绾枃鏈?URL锛岀洿鎺ヤ娇鐢?
            url = resp.text.strip()
            if url.startswith("http"):
                _log(f"  [鍥惧簥] 杩斿洖鏂囨湰 URL: {url}")
                return url
            raise Exception(f"PLUGIN_ERROR:::鍥惧簥杩斿洖闈?URL 鏂囨湰: {resp.text[:200]}")

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
            raise Exception(f"PLUGIN_ERROR:::鍥惧簥鍝嶅簲涓湭鎵惧埌 URL: {json.dumps(data, ensure_ascii=False)[:300]}")

        _log(f"  [鍥惧簥] 涓婁紶鎴愬姛锛孶RL: {url}")
        return url
    except Exception as e:
        _log(f"  [鍥惧簥] 涓婁紶澶辫触: {e}")
        raise



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
        base_url = str(plugin_params.get("base_url", "https://api.openai.com")).strip().rstrip("/")
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
        if ref_paths:
            if progress_callback:
                progress_callback("自动处理角色图眼睛遮挡...")
            ref_paths = _cover_reference_image_faces_batch(ref_paths, project_path, plugin_params)
            _log(f"  [face-cover] final reference paths after face cover: {ref_paths}")
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
                progress_callback("涓婁紶鍙傝€冨浘鍒板浘搴?..")

            image_urls = []
            for p in ref_paths:
                url = _upload_image_to_host(p, image_host_url, image_host_token, image_host_timeout)
                if url:
                    image_urls.append(url)

            if not image_urls:
                raise Exception("PLUGIN_ERROR:::鎵€鏈夊弬鑰冨浘涓婁紶澶辫触")

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
        
        headers = {"Authorization": f"Bearer {api_key}"}
        if not is_schat_fast_9ref:
            headers["Content-Type"] = "application/json"
        
        if progress_callback:
            progress_callback("鎻愪氦浠诲姟涓?..")
        
        # 璁＄畻璇锋眰浣撳ぇ灏忕敤浜庤瘖鏂?
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
            req_size_kb = len(payload_json.encode('utf-8')) / 1024
            _log(f"  [NewAPI] 璇锋眰浣撳ぇ灏? {req_size_kb:.2f} KB")
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
            raise Exception(f"PLUGIN_ERROR:::API 閿欒 {resp.status_code}: {err}")
        
        result = resp.json()
        _log(f"  鎻愪氦鍝嶅簲: {json.dumps(result, ensure_ascii=False)[:500]}")
        
        task_id = result.get("task_id") or result.get("id")
        if not task_id:
            raise Exception(f"PLUGIN_ERROR:::API 鍝嶅簲涓己灏戜换鍔?ID: {result}")
        
        _log(f"  浠诲姟 ID: {task_id}")
        
        # ===== 2. 杞浠诲姟鐘舵€?=====
        if progress_callback:
            progress_callback("鐢熸垚涓?..", 0)
        
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
                        progress_callback(f"鐢熸垚涓?({progress_pct}%)", int(progress_pct))
                    except Exception:
                        progress_callback(f"鐢熸垚涓?({progress_pct})")
                elif status_key in ("pending", "queued", "submitted"):
                    progress_callback("鎺掗槦涓?..")
                elif status_key in ("processing", "in_progress"):
                    progress_callback("鐢熸垚涓?..")
            
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
                    fail_msg = error_info.get("message", "鏈煡閿欒")
                else:
                    fail_msg = str(error_info) if error_info else "浠诲姟澶辫触"
                raise Exception(f"PLUGIN_ERROR:::瑙嗛鐢熸垚澶辫触: {fail_msg}")
            elif is_seedream and status_key in ("failure", "cancelled", "canceled"):
                raise Exception(f"PLUGIN_ERROR:::瑙嗛鐢熸垚澶辫触: {_seedream_failure_reason(status_data)}")
        else:
            raise Exception(f"PLUGIN_ERROR:::瓒呰繃鏈€澶ц疆璇㈡鏁?({max_poll})锛岃棰戞湭鐢熸垚")
        
        if not video_url:
            raise Exception("PLUGIN_ERROR:::浠诲姟瀹屾垚浣嗘湭鑾峰彇鍒拌棰?URL")
        
        # ===== 3. 涓嬭浇瑙嗛 =====
        if progress_callback:
            progress_callback("涓嬭浇涓?..", 99)
        
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
            raise Exception("PLUGIN_ERROR:::瑙嗛涓嬭浇澶辫触锛岃妫€鏌ョ綉缁滄垨绋嶅悗閲嶈瘯")

        if str(plugin_params.get("watermark_remove_enabled", False)).strip().lower() in ("1", "true", "yes", "on"):
            if progress_callback:
                progress_callback("鍘绘按鍗板鐞嗕腑...", 99)
            video_path = _remove_video_watermark(video_path, plugin_params)
        
        if progress_callback:
            progress_callback("瀹屾垚", 100)
        
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
            "Supports reference image upload through the configured image host."
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
    
    elif action == "select_local_file":
        target = str(data.get("target") or "").strip()
        title = str(data.get("title") or "Select file")
        initial_path = str(data.get("initial_path") or "").strip().strip('"')
        filetypes = data.get("filetypes")
        if not isinstance(filetypes, list) or not filetypes:
            filetypes = [("Python scripts", "*.py"), ("Executables", "*.exe"), ("All files", "*.*")]

        try:
            import tkinter as tk
            from tkinter import filedialog

            initial_dir = ""
            initial_file = ""
            if initial_path:
                candidate = Path(initial_path)
                if candidate.is_dir():
                    initial_dir = str(candidate)
                else:
                    initial_dir = str(candidate.parent) if str(candidate.parent) != "." else ""
                    initial_file = candidate.name

            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            selected = filedialog.askopenfilename(
                title=title,
                initialdir=initial_dir or None,
                initialfile=initial_file or None,
                filetypes=[tuple(item) for item in filetypes],
            )
            root.destroy()
        except Exception as exc:
            _log(f"[select-file] failed: {exc}")
            return {"ok": False, "target": target, "error": str(exc)}

        if not selected:
            return {"ok": False, "target": target, "cancelled": True}

        if target in {"face_tool_path", "watermark_tool_path"}:
            try:
                update_plugin_params(_PLUGIN_FILE, {target: selected})
            except Exception as save_err:
                _log(f"[select-file] save failed: {save_err}")

        return {"ok": True, "target": target, "path": selected}

    elif action == "check_update":
        repo = str(data.get("repo") or get_params().get("update_repo") or "").strip()
        result = _get_latest_release(repo, int(data.get("timeout", 20) or 20))
        if result.get("ok") and not result.get("has_update"):
            result["message"] = "已经是最新版本"
        return result

    elif action == "apply_update":
        repo = str(data.get("repo") or get_params().get("update_repo") or "").strip()
        asset_name = str(data.get("asset_name") or get_params().get("update_asset_name") or "").strip()
        return _apply_github_update(repo, asset_name)

    elif action == "get_logs":
        lines = int(data.get("lines", 100))
        logs = _get_recent_logs(lines)
        return {"ok": True, "logs": logs}
    
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



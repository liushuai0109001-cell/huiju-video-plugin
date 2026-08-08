"""Batch face processing desktop tool.

Modes:
* Eye cover: masks the eyes on the main portrait and optionally copies that crop
  to the right panel of a multi-view image.
* Head grid: detects every front/profile face and draws a square grid over it.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps, ImageTk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class EyeParams:
    black_y_offset: int = 8
    black_height: int = 36
    black_pad_x: int = 20
    right_y_offset: int = 28
    right_x_offset: int = 0
    paste_to_right: bool = True


@dataclass
class GridParams:
    grid_n: int = 4
    line_width: int = 2
    expand_pct: int = 15
    detect_profile: bool = True
    color: tuple[int, int, int] = (0, 0, 0)


@dataclass
class ProcessResult:
    image: Image.Image
    message: str
    detected: int = 0
    eye_box: Optional[tuple[int, int, int, int]] = None
    right_place: Optional[tuple[int, int, int, int]] = None


def clamp_box(box: tuple[int, int, int, int], width: int, height: int, min_w: int = 1, min_h: int = 1):
    x0, y0, x1, y1 = map(int, box)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    bw, bh = max(min_w, x1 - x0), max(min_h, y1 - y0)
    x0 = max(0, min(x0, max(0, width - min_w)))
    y0 = max(0, min(y0, max(0, height - min_h)))
    x1, y1 = x0 + bw, y0 + bh
    if x1 > width:
        x1, x0 = width, max(0, width - bw)
    if y1 > height:
        y1, y0 = height, max(0, height - bh)
    return int(x0), int(y0), int(x1), int(y1)


class FaceDetector:
    def __init__(self) -> None:
        packaged_data = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "cv2" / "data"
        base = str(packaged_data) + os.sep if packaged_data.is_dir() else cv2.data.haarcascades
        self.face = cv2.CascadeClassifier(base + "haarcascade_frontalface_alt2.xml")
        self.eye = cv2.CascadeClassifier(base + "haarcascade_eye_tree_eyeglasses.xml")
        self.eye2 = cv2.CascadeClassifier(base + "haarcascade_eye.xml")
        self.profile = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")
        if self.face.empty() or self.eye.empty():
            raise RuntimeError("OpenCV Haar cascades could not be loaded")

    @staticmethod
    def gray(image: Image.Image) -> np.ndarray:
        rgb = np.asarray(image.convert("RGB"))
        return cv2.equalizeHist(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))

    def faces(self, gray: np.ndarray, min_size: int = 40) -> list[tuple[int, int, int, int]]:
        found = self.face.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=4, minSize=(min_size, min_size), flags=cv2.CASCADE_SCALE_IMAGE
        )
        if found is None or len(found) == 0:
            found = self.face.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=3, minSize=(min_size, min_size))
        return [tuple(map(int, item)) for item in found] if found is not None else []

    def all_faces(self, image: Image.Image, detect_profile: bool) -> list[tuple[int, int, int, int]]:
        gray = self.gray(image)
        h, w = gray.shape[:2]
        minimum = max(24, min(w, h) // 35)
        boxes = self.faces(gray, minimum)
        if detect_profile and not self.profile.empty():
            for source, flipped in ((gray, False), (cv2.flip(gray, 1), True)):
                found = self.profile.detectMultiScale(source, scaleFactor=1.05, minNeighbors=3, minSize=(minimum, minimum))
                if found is not None:
                    for x, y, fw, fh in found:
                        if flipped:
                            boxes.append((w - int(x) - int(fw), int(y), int(fw), int(fh)))
                        else:
                            boxes.append((int(x), int(y), int(fw), int(fh)))
        return nms(boxes)

    def eyes(self, gray: np.ndarray, face: tuple[int, int, int, int]) -> Optional[tuple[int, int, int, int]]:
        fx, fy, fw, fh = face
        y0, y1 = fy + int(fh * 0.18), fy + int(fh * 0.58)
        x0, x1 = fx + int(fw * 0.08), fx + int(fw * 0.92)
        roi = gray[y0:y1, x0:x1]
        if roi.size == 0:
            return None
        found = self.eye.detectMultiScale(roi, scaleFactor=1.08, minNeighbors=3, minSize=(12, 12))
        if found is None or len(found) < 1:
            found = self.eye2.detectMultiScale(roi, scaleFactor=1.08, minNeighbors=3, minSize=(12, 12))
        if found is None or len(found) < 1:
            return None
        boxes = [(x0 + int(x), y0 + int(y), x0 + int(x + w), y0 + int(y + h)) for x, y, w, h in found]
        if len(boxes) == 1:
            bx0, by0, bx1, by1 = boxes[0]
            center = fx + fw // 2
            mirrored_center = 2 * center - (bx0 + bx1) // 2
            half = (bx1 - bx0) // 2
            boxes.append((mirrored_center - half, by0, mirrored_center + half, by1))
        return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def nms(boxes: Iterable[tuple[int, int, int, int]], threshold: float = 0.35) -> list[tuple[int, int, int, int]]:
    kept: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
        x, y, w, h = box
        reject = False
        for ox, oy, ow, oh in kept:
            ix0, iy0 = max(x, ox), max(y, oy)
            ix1, iy1 = min(x + w, ox + ow), min(y + h, oy + oh)
            inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
            union = w * h + ow * oh - inter
            if union and inter / union >= threshold:
                reject = True
                break
        if not reject:
            kept.append(box)
    return kept


def select_primary_face(faces: list[tuple[int, int, int, int]], width: int) -> Optional[tuple[int, int, int, int]]:
    if not faces:
        return None
    portrait_end = max(width // 2, int(width * 0.35))
    return max(faces, key=lambda b: b[2] * b[3] * (1.5 if b[0] + b[2] // 2 < portrait_end else 0.5))


def panel_splits(arr: np.ndarray) -> list[int]:
    white = np.all(arr >= 248, axis=2).mean(axis=0)
    smooth = np.convolve(white, np.ones(7) / 7, mode="same")
    peaks: list[int] = []
    for x in range(40, arr.shape[1] - 40):
        if smooth[x] > 0.72 and smooth[x] >= smooth[x - 1] and smooth[x] >= smooth[x + 1]:
            if not peaks or x - peaks[-1] > 40:
                peaks.append(x)
            elif smooth[x] > smooth[peaks[-1]]:
                peaks[-1] = x
    return peaks


def content_bbox(arr: np.ndarray, x0: int, x1: int, threshold: int = 248) -> Optional[tuple[int, int, int, int]]:
    region = arr[:, x0:x1]
    mask = np.any(region < threshold, axis=2)
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if not len(rows):
        return None
    return x0 + int(cols[0]), int(rows[0]), x0 + int(cols[-1]) + 1, int(rows[-1]) + 1


def _window_sum(integral: np.ndarray, x: int, y: int, width: int, height: int) -> float:
    x1, y1 = x + width, y + height
    return float(integral[y1, x1] - integral[y, x1] - integral[y1, x] + integral[y, x])


def find_empty_place(
    image: Image.Image,
    size: tuple[int, int],
    faces: list[tuple[int, int, int, int]],
    source_box: tuple[int, int, int, int],
) -> Optional[tuple[int, int, int, int]]:
    """Find a low-texture background window that does not overlap a person zone."""
    arr = np.asarray(image.convert("RGB"))
    height, width = arr.shape[:2]
    box_w, box_h = size
    if box_w >= width or box_h >= height:
        return None

    edge_h = max(2, height // 30)
    edge_w = max(2, width // 30)
    border_pixels = np.concatenate(
        (
            arr[:edge_h].reshape(-1, 3),
            arr[-edge_h:].reshape(-1, 3),
            arr[:, :edge_w].reshape(-1, 3),
            arr[:, -edge_w:].reshape(-1, 3),
        ),
        axis=0,
    ).astype(np.float32)
    background = np.median(border_pixels, axis=0)
    distance = np.linalg.norm(arr.astype(np.float32) - background, axis=2)
    foreground = (distance > 38).astype(np.uint8)
    foreground = cv2.dilate(foreground, np.ones((5, 5), np.uint8), iterations=1)

    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = (cv2.Canny(gray, 60, 140) > 0).astype(np.uint8)
    avoid = np.zeros((height, width), dtype=np.uint8)
    for x, y, fw, fh in faces:
        cx = x + fw / 2
        # Keep the exclusion close to the detected head; the background mask
        # handles the wider body silhouette while preserving gaps between poses.
        x0 = max(0, round(cx - fw * 1.45))
        x1 = min(width, round(cx + fw * 1.45))
        y0 = max(0, round(y - fh * 1.2))
        y1 = min(height, round(y + fh * 8.0))
        avoid[y0:y1, x0:x1] = 1
    sx0, sy0, sx1, sy1 = source_box
    margin = max(8, box_h // 2)
    avoid[max(0, sy0 - margin):min(height, sy1 + margin), max(0, sx0 - margin):min(width, sx1 + margin)] = 1

    fg_integral = cv2.integral(foreground)
    edge_integral = cv2.integral(edges)
    avoid_integral = cv2.integral(avoid)
    area = box_w * box_h
    step = max(4, min(box_w, box_h) // 4)
    xs = list(range(0, width - box_w + 1, step))
    ys = list(range(0, height - box_h + 1, step))
    if xs[-1] != width - box_w:
        xs.append(width - box_w)
    if ys[-1] != height - box_h:
        ys.append(height - box_h)

    best = None
    best_score = float("inf")
    for y in ys:
        for x in xs:
            if _window_sum(avoid_integral, x, y, box_w, box_h) > 0:
                continue
            foreground_ratio = _window_sum(fg_integral, x, y, box_w, box_h) / area
            edge_ratio = _window_sum(edge_integral, x, y, box_w, box_h) / area
            score = foreground_ratio * 7.0 + edge_ratio * 2.5 + (1.0 - x / max(1, width - box_w)) * 0.08
            if score < best_score:
                best_score = score
                best = (x, y, x + box_w, y + box_h, foreground_ratio, edge_ratio)

    if best is None or best[4] > 0.08 or best[5] > 0.04:
        return None
    return best[0], best[1], best[2], best[3]


def eye_mask(
    image: Image.Image,
    detector: FaceDetector,
    params: EyeParams,
    right_place: Optional[tuple[int, int, int, int]] = None,
) -> ProcessResult:
    source = ImageOps.exif_transpose(image).convert("RGB")
    w, h = source.size
    gray = detector.gray(source)
    face = select_primary_face(detector.faces(gray), w)
    if face is None:
        return ProcessResult(source, "No face detected", 0)
    detected = detector.eyes(gray, face)
    if detected is None:
        fx, fy, fw, fh = face
        detected = (fx + int(fw * .18), fy + int(fh * .32), fx + int(fw * .82), fy + int(fh * .48))
        eye_note = "eye fallback"
    else:
        eye_note = "eyes detected"
    el, et, er, eb = detected
    pad = max(0, int(params.black_pad_x))
    el, er = max(0, el - pad), min(w, er + pad)
    center_y = (et + eb) // 2 + int(params.black_y_offset)
    target_h = max(12, min(int(params.black_height), max(12, h)))
    et, eb = center_y - target_h // 2, center_y - target_h // 2 + target_h
    eye_box = clamp_box((el, et, er, eb), w, h)
    el, et, er, eb = eye_box
    crop = source.crop(eye_box)
    result = source.copy()
    draw = ImageDraw.Draw(result)
    radius = max(4, min((eb - et) // 2, (er - el) // 4, 40))
    draw.rounded_rectangle((el, et, er - 1, eb - 1), radius=radius, fill=(0, 0, 0))
    pasted = False
    pasted_box = None
    if params.paste_to_right:
        if right_place is not None:
            x, y, x1, y1 = clamp_box(right_place, w, h)
            pasted_box = (x, y, x1, y1)
        else:
            all_faces = detector.all_faces(source, detect_profile=True)
            pasted_box = find_empty_place(source, crop.size, all_faces, eye_box)
        if pasted_box is not None:
            x, y, x1, y1 = pasted_box
            pasted = True
            result.paste(crop.resize((x1 - x, y1 - y), Image.Resampling.LANCZOS), (x, y))
    return ProcessResult(
        result,
        f"{eye_note}; black bar {er - el}x{eb - et}"
        + ("; pasted in empty area" if pasted else "; no safe empty area"),
        1,
        eye_box,
        pasted_box,
    )


def _eye_box_for_face(
    detector: FaceDetector,
    gray: np.ndarray,
    face: tuple[int, int, int, int],
    image_size: tuple[int, int],
    params: EyeParams,
) -> tuple[tuple[int, int, int, int], str]:
    w, h = image_size
    detected = detector.eyes(gray, face)
    fx, fy, fw, fh = face
    if detected is None:
        if fw < fh * 0.9:
            detected = (fx + int(fw * .12), fy + int(fh * .18), fx + int(fw * .92), fy + int(fh * .40))
        else:
            detected = (fx + int(fw * .18), fy + int(fh * .22), fx + int(fw * .82), fy + int(fh * .42))
        eye_note = "eye fallback"
    else:
        eye_note = "eyes detected"

    el, et, er, eb = detected
    pad = max(0, min(int(params.black_pad_x), int(fw * 0.22)))
    el, er = max(0, el - pad), min(w, er + pad)
    center_y = (et + eb) // 2 + int(params.black_y_offset)
    adaptive_h = max(12, int(fh * 0.17))
    target_h = max(12, min(int(params.black_height), adaptive_h, h))
    et, eb = center_y - target_h // 2, center_y - target_h // 2 + target_h
    return clamp_box((el, et, er, eb), w, h), eye_note


def eye_mask_all_visible(
    image: Image.Image,
    detector: FaceDetector,
    params: EyeParams,
    right_place: Optional[tuple[int, int, int, int]] = None,
) -> ProcessResult:
    source = ImageOps.exif_transpose(image).convert("RGB")
    w, h = source.size
    gray = detector.gray(source)
    faces = detector.all_faces(source, detect_profile=True)
    if not faces:
        return ProcessResult(source, "No face detected", 0)

    faces = sorted(faces, key=lambda b: (b[0], b[1]))
    primary = select_primary_face(faces, w) or faces[0]
    primary_eye_box, primary_note = _eye_box_for_face(detector, gray, primary, source.size, params)
    crop = source.crop(primary_eye_box)

    result = source.copy()
    draw = ImageDraw.Draw(result)
    masked_boxes: list[tuple[int, int, int, int]] = []
    notes: list[str] = []
    for face in faces:
        if face[1] > h * 0.36 and detector.eyes(gray, face) is None:
            continue
        eye_box, note = _eye_box_for_face(detector, gray, face, source.size, params)
        el, et, er, eb = eye_box
        if er - el < 4 or eb - et < 4:
            continue
        draw.rectangle((el, et, er - 1, eb - 1), fill=(0, 0, 0))
        masked_boxes.append(eye_box)
        notes.append(note)

    pasted = False
    pasted_box = None
    if params.paste_to_right and crop.size[0] < w and crop.size[1] < h:
        if right_place is not None:
            x, y, x1, y1 = clamp_box(right_place, w, h)
            pasted_box = (x, y, x1, y1)
        else:
            pasted_box = find_empty_place(source, crop.size, faces, primary_eye_box)
        if pasted_box is not None:
            x, y, x1, y1 = pasted_box
            result.paste(crop.resize((x1 - x, y1 - y), Image.Resampling.LANCZOS), (x, y))
            pasted = True

    summary_note = primary_note if primary_note in notes else (notes[0] if notes else primary_note)
    return ProcessResult(
        result,
        f"{summary_note}; black bars {len(masked_boxes)}"
        + ("; pasted in empty area" if pasted else "; no safe empty area"),
        len(masked_boxes),
        primary_eye_box,
        pasted_box,
    )


def head_box(face: tuple[int, int, int, int], width: int, height: int, expand_pct: int) -> tuple[int, int, int, int]:
    x, y, fw, fh = face
    side = max(fw, fh) * (1 + max(0, expand_pct) / 100)
    cx, cy = x + fw / 2, y + fh * .48
    return clamp_box((round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2)), width, height)


def draw_grid(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], n: int, line_width: int, color: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    n, line_width = max(1, min(40, int(n))), max(1, min(20, int(line_width)))
    draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=color, width=line_width)
    for i in range(1, n):
        x = x0 + round((x1 - x0) * i / n)
        y = y0 + round((y1 - y0) * i / n)
        draw.line(((x, y0), (x, y1 - 1)), fill=color, width=line_width)
        draw.line(((x0, y), (x1 - 1, y)), fill=color, width=line_width)


def head_grid(image: Image.Image, detector: FaceDetector, params: GridParams) -> ProcessResult:
    source = ImageOps.exif_transpose(image).convert("RGB")
    faces = detector.all_faces(source, params.detect_profile)
    result = source.copy()
    draw = ImageDraw.Draw(result)
    for face in faces:
        draw_grid(draw, head_box(face, *source.size, params.expand_pct), params.grid_n, params.line_width, params.color)
    return ProcessResult(result, f"{len(faces)} head grid(s) drawn" if faces else "No face detected", len(faces))


class FaceTool(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("自动人脸处理工具")
        self.geometry("1100x760")
        self.minsize(900, 620)
        self.detector = FaceDetector()
        self.paths: list[Path] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self._photo: Optional[ImageTk.PhotoImage] = None
        self.preview_source: Optional[Image.Image] = None
        self.preview_result: Optional[ProcessResult] = None
        self.preview_ready = False
        self.manual_right_norm: Optional[tuple[float, float, float, float]] = None
        self._preview_geometry: Optional[tuple[int, int, float]] = None
        self._drag_offset: Optional[tuple[float, float]] = None
        self.mode = tk.StringVar(value="eye")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(Path.cwd() / "face_tool_output"))
        self.eye_y = tk.IntVar(value=8)
        self.eye_h = tk.IntVar(value=36)
        self.eye_pad = tk.IntVar(value=20)
        self.right_y = tk.IntVar(value=28)
        self.right_x = tk.IntVar(value=0)
        self.paste_right = tk.BooleanVar(value=True)
        self.grid_n = tk.IntVar(value=4)
        self.grid_w = tk.IntVar(value=2)
        self.grid_expand = tk.IntVar(value=15)
        self.profile = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="请选择图片文件或文件夹")
        self._build()
        for variable in (self.eye_y, self.eye_h, self.eye_pad, self.right_y, self.right_x, self.paste_right):
            variable.trace_add("write", self._invalidate_eye_preview)
        self.after(80, self._drain_events)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(2, weight=1)

        files = ttk.LabelFrame(root, text="输入与输出", padding=8)
        files.grid(row=0, column=0, columnspan=2, sticky="ew")
        files.columnconfigure(1, weight=1)
        ttk.Button(files, text="选择图片", command=self.select_files).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(files, text="选择文件夹", command=self.select_folder).grid(row=0, column=1, sticky="w")
        ttk.Label(files, textvariable=self.input_var).grid(row=0, column=2, padx=8, sticky="w")
        ttk.Label(files, text="输出目录").grid(row=1, column=0, pady=(8, 0), sticky="w")
        ttk.Entry(files, textvariable=self.output_var).grid(row=1, column=1, columnspan=1, pady=(8, 0), sticky="ew")
        ttk.Button(files, text="浏览", command=self.select_output).grid(row=1, column=2, padx=(6, 0), pady=(8, 0))

        controls = ttk.LabelFrame(root, text="自动处理", padding=8)
        controls.grid(row=1, column=0, rowspan=2, sticky="nsew", pady=(10, 0))
        ttk.Radiobutton(controls, text="眼睛遮挡并贴图", variable=self.mode, value="eye", command=self._toggle_controls).pack(anchor="w")
        ttk.Radiobutton(controls, text="头部网格遮挡", variable=self.mode, value="grid", command=self._toggle_controls).pack(anchor="w", pady=(2, 8))
        self.eye_frame = ttk.LabelFrame(controls, text="眼睛遮挡参数", padding=6)
        self.eye_frame.pack(fill=tk.X)
        self._scale(self.eye_frame, "黑条上下偏移", self.eye_y, -100, 100)
        self._scale(self.eye_frame, "黑条高度", self.eye_h, 12, 200)
        self._scale(self.eye_frame, "黑条左右边距", self.eye_pad, 0, 200)
        ttk.Checkbutton(self.eye_frame, text="把眼睛贴到右侧分栏", variable=self.paste_right).pack(anchor="w", pady=(4, 0))
        self.grid_frame = ttk.LabelFrame(controls, text="头部网格参数", padding=6)
        self.grid_frame.pack(fill=tk.X, pady=(10, 0))
        self._scale(self.grid_frame, "网格 N x N", self.grid_n, 1, 20)
        self._scale(self.grid_frame, "线条粗细", self.grid_w, 1, 12)
        self._scale(self.grid_frame, "头部扩大 %", self.grid_expand, 0, 100)
        ttk.Checkbutton(self.grid_frame, text="检测侧脸", variable=self.profile).pack(anchor="w", pady=(4, 0))
        self.auto_batch_button = ttk.Button(controls, text="全自动批量处理（每张独立找空位）", command=self.run_auto_batch)
        self.auto_batch_button.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(controls, text="预览首张并设置位置", command=self.preview_first).pack(fill=tk.X, pady=(12, 0))
        ttk.Button(controls, text="恢复自动位置", command=self.reset_right_position).pack(fill=tk.X, pady=(6, 0))
        self.run_button = ttk.Button(controls, text="按预览位置批量处理", command=self.run_batch)
        self.run_button.pack(fill=tk.X, pady=(6, 0))

        preview = ttk.LabelFrame(root, text="预览：拖动红框，或单击建立粘贴位置", padding=8)
        preview.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=(10, 0), pady=(10, 0))
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)
        self.preview = tk.Canvas(preview, background="#202020", highlightthickness=0)
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.preview.create_text(350, 250, text="请选择图片后预览", fill="#dddddd", font=("Microsoft YaHei UI", 12))
        self.preview.bind("<ButtonPress-1>", self._on_preview_press)
        self.preview.bind("<B1-Motion>", self._on_preview_drag)
        self.preview.bind("<ButtonRelease-1>", self._on_preview_release)
        self.preview.bind("<Configure>", lambda _event: self._draw_preview())
        self.log = tk.Text(preview, height=8, state=tk.DISABLED, wrap=tk.WORD)
        self.log.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(root, textvariable=self.status_var).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._toggle_controls()

    @staticmethod
    def _scale(parent: ttk.Widget, label: str, variable: tk.IntVar, low: int, high: int) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label, width=19).pack(side=tk.LEFT)
        ttk.Scale(row, from_=low, to=high, variable=variable).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(row, textvariable=variable, width=5).pack(side=tk.LEFT, padx=(6, 0))

    def _toggle_controls(self) -> None:
        eye_state = "normal" if self.mode.get() == "eye" else "disabled"
        grid_state = "normal" if self.mode.get() == "grid" else "disabled"
        for child in self.eye_frame.winfo_children():
            child.configure(state=eye_state) if child.winfo_class() in {"TCheckbutton", "TScale"} else None
        for child in self.grid_frame.winfo_children():
            child.configure(state=grid_state) if child.winfo_class() in {"TCheckbutton", "TScale"} else None
        self.preview_ready = False

    def _invalidate_eye_preview(self, *_args) -> None:
        if self.mode.get() == "eye" and self.preview_result is not None:
            self.preview_ready = False
            self.status_var.set("参数已改变，请重新预览确认后再批量处理")

    def _clear_preview_state(self) -> None:
        self.preview_source = None
        self.preview_result = None
        self.preview_ready = False
        self.manual_right_norm = None
        self._preview_geometry = None
        self._drag_offset = None

    def _scaled_right_box(self, size: tuple[int, int]) -> Optional[tuple[int, int, int, int]]:
        if self.manual_right_norm is None:
            return None
        w, h = size
        x0, y0, x1, y1 = self.manual_right_norm
        return clamp_box((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)), w, h)

    def preview_first(self) -> None:
        if not self.paths:
            messagebox.showwarning("自动人脸处理工具", "请先选择图片文件。")
            return
        try:
            with Image.open(self.paths[0]) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            if self.mode.get() == "eye":
                result = eye_mask(image, self.detector, self.current_params(), self._scaled_right_box(image.size))
                if self.paste_right.get() and result.right_place is None:
                    messagebox.showwarning("未找到安全空位", "已保留黑条但未自动贴图。可在预览中单击空白位置建立红框，再拖动微调。")
                    self.preview_ready = False
                else:
                    self.preview_ready = True
                if result.right_place is not None:
                    x0, y0, x1, y1 = result.right_place
                    w, h = image.size
                    self.manual_right_norm = (x0 / w, y0 / h, x1 / w, y1 / h)
            else:
                result = head_grid(image, self.detector, self.current_params())
                self.preview_ready = True
            self.preview_source = image
            self.preview_result = result
            self._draw_preview()
            self.status_var.set(f"首张预览完成：{result.message}。拖动红框可调整位置。")
        except Exception as exc:
            self.preview_ready = False
            messagebox.showerror("预览失败", str(exc))

    def reset_right_position(self) -> None:
        self.manual_right_norm = None
        self.preview_ready = False
        if self.paths:
            self.preview_first()

    def select_files(self) -> None:
        names = filedialog.askopenfilenames(filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")])
        if names:
            self.paths = [Path(name) for name in names]
            self._clear_preview_state()
            self.input_var.set(f"已选择 {len(self.paths)} 张图片")

    def select_folder(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.paths = sorted(path for path in Path(folder).rglob("*") if path.suffix.lower() in IMAGE_EXTENSIONS)
            self._clear_preview_state()
            self.input_var.set(f"文件夹内共 {len(self.paths)} 张图片")

    def select_output(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.output_var.set(folder)

    def current_params(self):
        if self.mode.get() == "eye":
            return EyeParams(self.eye_y.get(), self.eye_h.get(), self.eye_pad.get(), self.right_y.get(), self.right_x.get(), self.paste_right.get())
        return GridParams(self.grid_n.get(), self.grid_w.get(), self.grid_expand.get(), self.profile.get())

    def run_batch(self) -> None:
        if self.busy:
            return
        if not self.paths:
            messagebox.showwarning("自动人脸处理工具", "请先选择图片文件。")
            return
        if self.mode.get() == "eye" and not self.preview_ready:
            messagebox.showwarning("请先预览", "请先点击“预览首张并设置位置”，确认眼睛粘贴位置后再处理。")
            return
        self._start_batch(self.manual_right_norm)

    def run_auto_batch(self) -> None:
        if self.busy:
            return
        if not self.paths:
            messagebox.showwarning("自动人脸处理工具", "请先选择图片文件或文件夹。")
            return
        self._start_batch(None)

    def _start_batch(self, right_norm: Optional[tuple[float, float, float, float]]) -> None:
        output = Path(self.output_var.get()).expanduser()
        if not str(output):
            messagebox.showwarning("自动人脸处理工具", "请选择输出目录。")
            return
        self.busy = True
        self.run_button.configure(state=tk.DISABLED)
        self.auto_batch_button.configure(state=tk.DISABLED)
        self.status_var.set("正在处理...")
        threading.Thread(
            target=self._worker,
            args=(list(self.paths), output, self.mode.get(), self.current_params(), right_norm),
            daemon=True,
        ).start()

    def _worker(
        self,
        paths: list[Path],
        output: Path,
        mode: str,
        params: EyeParams | GridParams,
        right_norm: Optional[tuple[float, float, float, float]],
    ) -> None:
        output.mkdir(parents=True, exist_ok=True)
        report: list[str] = []
        suffix = "_eyes_covered" if mode == "eye" else "_head_grid"
        for index, path in enumerate(paths, start=1):
            try:
                with Image.open(path) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                if mode == "eye":
                    right_box = None
                    if right_norm is not None:
                        w, h = image.size
                        x0, y0, x1, y1 = right_norm
                        right_box = clamp_box((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)), w, h)
                    result = eye_mask(image, self.detector, params, right_box)
                else:
                    result = head_grid(image, self.detector, params)
                destination = output / f"{path.stem}{suffix}{path.suffix.lower()}"
                if destination.suffix.lower() in {".jpg", ".jpeg"}:
                    result.image.save(destination, quality=95, subsampling=0)
                else:
                    result.image.save(destination)
                report.append(f"OK\t{path.name}\t{result.message}")
                self.events.put(("result", (index, len(paths), path.name, result)))
            except Exception as exc:
                report.append(f"ERROR\t{path.name}\t{exc}")
                self.events.put(("log", f"ERROR {path.name}: {exc}"))
        (output / "processing-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
        self.events.put(("done", (len(paths), output)))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "result":
                    index, total, name, result = data
                    self.status_var.set(f"{index}/{total}: {name} - {result.message}")
                    self.preview_result = result
                    self._draw_preview()
                    self._append(f"OK {name}: {result.message}")
                elif kind == "log":
                    self._append(str(data))
                elif kind == "done":
                    total, output = data
                    self.busy = False
                    self.run_button.configure(state=tk.NORMAL)
                    self.auto_batch_button.configure(state=tk.NORMAL)
                    self.status_var.set(f"完成：已输出 {total} 张图片到 {output}")
                    self._append(f"完成。处理报告：{Path(output) / 'processing-report.txt'}")
        except queue.Empty:
            pass
        self.after(80, self._drain_events)

    def _draw_preview(self) -> None:
        self.preview.delete("all")
        if self.preview_result is None:
            self.preview.create_text(
                max(1, self.preview.winfo_width() // 2),
                max(1, self.preview.winfo_height() // 2),
                text="请选择图片后预览",
                fill="#dddddd",
                font=("Microsoft YaHei UI", 12),
            )
            return
        image = self.preview_result.image
        canvas_w, canvas_h = max(1, self.preview.winfo_width()), max(1, self.preview.winfo_height())
        copy = image.copy()
        copy.thumbnail((max(1, canvas_w - 12), max(1, canvas_h - 12)), Image.Resampling.LANCZOS)
        scale = copy.width / image.width
        ox, oy = (canvas_w - copy.width) // 2, (canvas_h - copy.height) // 2
        self._photo = ImageTk.PhotoImage(copy)
        self.preview.create_image(ox, oy, image=self._photo, anchor=tk.NW)
        self._preview_geometry = (ox, oy, scale)
        box = self._scaled_right_box(image.size) if self.manual_right_norm is not None else self.preview_result.right_place
        if self.mode.get() == "eye" and box is not None:
            x0, y0, x1, y1 = box
            self.preview.create_rectangle(
                ox + x0 * scale,
                oy + y0 * scale,
                ox + x1 * scale,
                oy + y1 * scale,
                outline="#ff3b30",
                width=2,
            )
            self.preview.create_text(
                ox + x0 * scale + 4,
                oy + y0 * scale - 4,
                text="粘贴位置",
                anchor=tk.SW,
                fill="#ff3b30",
                font=("Microsoft YaHei UI", 10, "bold"),
            )

    def _preview_point(self, event: tk.Event) -> Optional[tuple[float, float]]:
        if self._preview_geometry is None or self.preview_result is None:
            return None
        ox, oy, scale = self._preview_geometry
        x, y = (event.x - ox) / scale, (event.y - oy) / scale
        w, h = self.preview_result.image.size
        if 0 <= x <= w and 0 <= y <= h:
            return x, y
        return None

    def _on_preview_press(self, event: tk.Event) -> None:
        if self.mode.get() != "eye" or self.preview_result is None:
            return
        point = self._preview_point(event)
        box = self._scaled_right_box(self.preview_result.image.size) if self.manual_right_norm is not None else self.preview_result.right_place
        if point is None:
            return
        x, y = point
        if box is None and self.preview_result.eye_box is not None:
            ex0, ey0, ex1, ey1 = self.preview_result.eye_box
            bw, bh = ex1 - ex0, ey1 - ey0
            w, h = self.preview_result.image.size
            x0, y0, x1, y1 = clamp_box((round(x - bw / 2), round(y - bh / 2), round(x + bw / 2), round(y + bh / 2)), w, h)
            self.manual_right_norm = (x0 / w, y0 / h, x1 / w, y1 / h)
            self._drag_offset = (x - x0, y - y0)
            self._draw_preview()
            return
        if box is None:
            return
        x0, y0, x1, y1 = box
        if x0 <= x <= x1 and y0 <= y <= y1:
            self._drag_offset = (x - x0, y - y0)

    def _on_preview_drag(self, event: tk.Event) -> None:
        if self._drag_offset is None or self.preview_result is None:
            return
        point = self._preview_point(event)
        if point is None:
            return
        current = self._scaled_right_box(self.preview_result.image.size) if self.manual_right_norm is not None else self.preview_result.right_place
        if current is None:
            return
        x, y = point
        x0, y0, x1, y1 = current
        bw, bh = x1 - x0, y1 - y0
        dx, dy = self._drag_offset
        w, h = self.preview_result.image.size
        x0, y0, x1, y1 = clamp_box((round(x - dx), round(y - dy), round(x - dx + bw), round(y - dy + bh)), w, h)
        self.manual_right_norm = (x0 / w, y0 / h, x1 / w, y1 / h)
        self._draw_preview()

    def _on_preview_release(self, _event: tk.Event) -> None:
        if self._drag_offset is None:
            return
        self._drag_offset = None
        self.preview_ready = False
        self.preview_first()

    def _append(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch face processing desktop tool")
    parser.add_argument("--cli", action="store_true", help="Run without the desktop UI")
    parser.add_argument("--input", help="Input image or folder")
    parser.add_argument("--output", help="Output image or folder")
    parser.add_argument("--mode", choices=("eye", "eye-all", "grid"), default="eye-all")
    parser.add_argument("--black-y-offset", type=int, default=8)
    parser.add_argument("--black-height", type=int, default=36)
    parser.add_argument("--black-pad-x", type=int, default=20)
    parser.add_argument("--no-paste", action="store_true")
    parser.add_argument("--grid-n", type=int, default=4)
    parser.add_argument("--grid-line-width", type=int, default=2)
    parser.add_argument("--grid-expand-pct", type=int, default=15)
    args = parser.parse_args()

    if not args.cli:
        FaceTool().mainloop()
    else:
        if not args.input or not args.output:
            parser.error("--cli requires --input and --output")
        input_path = Path(args.input).expanduser()
        output_path = Path(args.output).expanduser()
        if input_path.is_dir():
            paths = sorted(path for path in input_path.rglob("*") if path.suffix.lower() in IMAGE_EXTENSIONS)
            output_path.mkdir(parents=True, exist_ok=True)
        else:
            paths = [input_path]
            output_path.parent.mkdir(parents=True, exist_ok=True)

        detector = FaceDetector()
        eye_params = EyeParams(args.black_y_offset, args.black_height, args.black_pad_x, paste_to_right=not args.no_paste)
        grid_params = GridParams(args.grid_n, args.grid_line_width, args.grid_expand_pct, True)
        report: list[str] = []
        for path in paths:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            if args.mode == "grid":
                result = head_grid(image, detector, grid_params)
                suffix = "_head_grid"
            elif args.mode == "eye":
                result = eye_mask(image, detector, eye_params)
                suffix = "_eyes_covered"
            else:
                result = eye_mask_all_visible(image, detector, eye_params)
                suffix = "_eyes_covered"

            destination = output_path / f"{path.stem}{suffix}{path.suffix.lower()}" if input_path.is_dir() else output_path
            if destination.suffix.lower() in {".jpg", ".jpeg"}:
                result.image.save(destination, quality=95, subsampling=0)
            else:
                result.image.save(destination)
            report.append(f"OK\t{path}\t{destination}\t{result.message}")

        if input_path.is_dir():
            (output_path / "processing-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n".join(report))

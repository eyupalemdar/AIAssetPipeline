from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .image_ops import clear_outer_alpha, dilate_transparent_rgb, erode_bool, neutralize_hidden_artifacts


CANONICAL_BRONZE = {
    "dark": np.array([72, 40, 22], dtype=np.float32),
    "base": np.array([160, 92, 48], dtype=np.float32),
    "light": np.array([222, 155, 88], dtype=np.float32),
    "gold": np.array([244, 196, 122], dtype=np.float32),
}


def render_vector_sdf_icon(size: tuple[int, int], config: dict[str, Any]) -> Image.Image:
    """Render a clean icon from an analytical mask and controlled material.

    The implementation is intentionally dependency-light. It follows the same
    production idea as SDF/MSDF icon workflows: shape first, material second.
    Supersampling gives stable coverage alpha, while optional signed-distance
    data from OpenCV, when available, drives the small bevel band.
    """

    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid vector icon size: {size}")

    glyph = str(config.get("glyph", "")).strip().lower()
    supersample = max(3, int(config.get("supersample", 6)))
    if glyph in {"mic_muted", "microphone_muted", "muted_microphone"}:
        arr = _render_muted_microphone(size, supersample, config)
        clear_px = int(config.get("clear_outer_alpha_px", 1))
        arr = clear_outer_alpha(arr, clear_px)
        arr = dilate_transparent_rgb(arr, iterations=int(config.get("transparent_rgb_dilation", 16)))
        arr = neutralize_hidden_artifacts(arr)
        return Image.fromarray(arr, "RGBA")

    alpha = _draw_glyph_alpha(glyph, size, supersample)
    arr = _apply_bronze_material(alpha, config)
    clear_px = int(config.get("clear_outer_alpha_px", 1))
    arr = clear_outer_alpha(arr, clear_px)
    arr = dilate_transparent_rgb(arr, iterations=int(config.get("transparent_rgb_dilation", 16)))
    arr = neutralize_hidden_artifacts(arr)
    return Image.fromarray(arr, "RGBA")


def _render_muted_microphone(
    size: tuple[int, int],
    supersample: int,
    config: dict[str, Any],
) -> np.ndarray:
    mic_alpha = _draw_glyph_alpha("mic_unmuted", size, supersample)
    base = Image.fromarray(_apply_bronze_material(mic_alpha, config), "RGBA")

    outline_alpha = _draw_mute_slash_alpha(size, supersample, width_pct=8.4)
    outline = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    outline[outline_alpha > 0, :3] = np.array([54, 31, 18], dtype=np.uint8)
    outline[:, :, 3] = outline_alpha
    base.alpha_composite(Image.fromarray(outline, "RGBA"))

    inner_alpha = _draw_mute_slash_alpha(size, supersample, width_pct=5.2)
    inner = Image.fromarray(_apply_bronze_material(inner_alpha, config), "RGBA")
    base.alpha_composite(inner)
    return np.asarray(base.convert("RGBA"), dtype=np.uint8).copy()


def _draw_mute_slash_alpha(
    size: tuple[int, int],
    supersample: int,
    *,
    width_pct: float,
) -> np.ndarray:
    width, height = size
    mask = Image.new("L", (width * supersample, height * supersample), 0)
    draw = ImageDraw.Draw(mask)

    def x(value: float) -> int:
        return int(round(value / 100.0 * width * supersample))

    def y(value: float) -> int:
        return int(round(value / 100.0 * height * supersample))

    stroke = max(1, int(round(width_pct / 100.0 * min(width, height) * supersample)))
    p1 = np.array([x(31), y(24)], dtype=np.float32)
    p2 = np.array([x(72), y(76)], dtype=np.float32)
    direction = p2 - p1
    length = float(np.linalg.norm(direction))
    if length <= 0.001:
        return np.zeros((height, width), dtype=np.uint8)
    normal = np.array([-direction[1], direction[0]], dtype=np.float32) / length * (stroke / 2.0)
    points = [
        tuple(np.round(p1 + normal).astype(int)),
        tuple(np.round(p2 + normal).astype(int)),
        tuple(np.round(p2 - normal).astype(int)),
        tuple(np.round(p1 - normal).astype(int)),
    ]
    draw.polygon(points, fill=255)
    radius = stroke / 2.0
    for point in (p1, p2):
        draw.ellipse(
            [
                int(round(point[0] - radius)),
                int(round(point[1] - radius)),
                int(round(point[0] + radius)),
                int(round(point[1] + radius)),
            ],
            fill=255,
        )
    arr = np.asarray(mask.resize(size, Image.Resampling.LANCZOS), dtype=np.uint8).copy()
    arr[arr < 3] = 0
    return arr


def _draw_glyph_alpha(glyph: str, size: tuple[int, int], supersample: int) -> np.ndarray:
    width, height = size
    mask = Image.new("L", (width * supersample, height * supersample), 0)
    draw = ImageDraw.Draw(mask)

    def x(value: float) -> int:
        return int(round(value / 100.0 * width * supersample))

    def y(value: float) -> int:
        return int(round(value / 100.0 * height * supersample))

    def sw(value: float) -> int:
        return max(1, int(round(value / 100.0 * min(width, height) * supersample)))

    def rect(x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return x(x0), y(y0), x(x1), y(y1)

    if glyph in {"mic", "microphone", "mic_unmuted", "microphone_unmuted"}:
        _draw_microphone(draw, rect, x, y, sw, muted=False)
    elif glyph in {"mic_muted", "microphone_muted", "muted_microphone"}:
        _draw_microphone(draw, rect, x, y, sw, muted=True)
    elif glyph in {"add_friend", "person_plus", "user_plus"}:
        _draw_add_friend(draw, rect, x, y, sw)
    elif glyph in {"self_balance", "balance", "seat_balance"}:
        _draw_self_balance(draw, rect, x, y, sw)
    else:
        raise ValueError(f"Unsupported vector_sdf_icon glyph: {glyph}")

    alpha = mask.resize(size, Image.Resampling.LANCZOS)
    arr = np.asarray(alpha, dtype=np.uint8).copy()
    arr[arr < 3] = 0
    return arr


def _draw_microphone(
    draw: ImageDraw.ImageDraw,
    rect: Any,
    x: Any,
    y: Any,
    sw: Any,
    *,
    muted: bool,
) -> None:
    draw.rounded_rectangle(rect(38, 14, 62, 58), radius=sw(12), fill=255)
    draw.line([(x(30), y(47)), (x(30), y(65))], fill=255, width=sw(5.3))
    draw.line([(x(70), y(47)), (x(70), y(65))], fill=255, width=sw(5.3))
    draw.arc(rect(30, 41, 70, 79), 0, 180, fill=255, width=sw(5.3))
    draw.line([(x(50), y(76)), (x(50), y(87))], fill=255, width=sw(5.0))
    draw.line([(x(36), y(87)), (x(64), y(87))], fill=255, width=sw(5.8))
    if muted:
        draw.line([(x(28), y(23)), (x(74), y(78))], fill=255, width=sw(6.4))


def _draw_add_friend(
    draw: ImageDraw.ImageDraw,
    rect: Any,
    x: Any,
    y: Any,
    sw: Any,
) -> None:
    draw.ellipse(rect(31, 18, 54, 43), fill=255)
    draw.rounded_rectangle(rect(18, 54, 65, 82), radius=sw(13), fill=255)
    draw.line([(x(75), y(38)), (x(75), y(66))], fill=255, width=sw(8))
    draw.line([(x(61), y(52)), (x(89), y(52))], fill=255, width=sw(8))


def _draw_self_balance(
    draw: ImageDraw.ImageDraw,
    rect: Any,
    x: Any,
    y: Any,
    sw: Any,
) -> None:
    draw.ellipse(rect(18, 18, 82, 82), outline=255, width=sw(5))
    for px, py in ((50, 12), (88, 50), (50, 88), (12, 50)):
        r = sw(4.8)
        draw.polygon(
            [
                (x(px), y(py) - r),
                (x(px) + r, y(py)),
                (x(px), y(py) + r),
                (x(px) - r, y(py)),
            ],
            fill=255,
        )
    for i in range(4):
        angle = math.tau * i / 4.0 + 0.24
        points = [
            (x(50 + math.cos(angle) * 10), y(50 + math.sin(angle) * 10)),
            (x(50 + math.cos(angle + 0.46) * 30), y(50 + math.sin(angle + 0.46) * 30)),
            (x(50 + math.cos(angle + 1.00) * 13), y(50 + math.sin(angle + 1.00) * 13)),
        ]
        draw.polygon(points, fill=255)
    draw.ellipse(rect(43, 43, 57, 57), fill=255)


def _apply_bronze_material(alpha: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    height, width = alpha.shape
    yy = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]

    palette = CANONICAL_BRONZE
    base = np.broadcast_to(palette["base"], (height, width, 3)).copy()
    vertical = 1.12 - yy * 0.34
    top_left = np.clip(1.0 - (xx * 0.75 + yy * 0.95), 0.0, 1.0)
    center = np.exp(-(((xx - 0.42) ** 2) / 0.055 + ((yy - 0.30) ** 2) / 0.075))
    shade = np.clip(vertical + top_left * 0.18 + center * 0.20, 0.58, 1.30)
    rgb = base * shade[:, :, None]

    signed_distance = _signed_distance(alpha)
    if signed_distance is not None:
        edge_width = max(1.0, float(config.get("bevel_edge_px", 2.0)))
        edge_band = np.clip(1.0 - np.abs(signed_distance) / edge_width, 0.0, 1.0)
        lit_edge = edge_band * np.clip(1.2 - (xx * 0.55 + yy * 0.85), 0.0, 1.0)
        dark_edge = edge_band * np.clip((xx * 0.40 + yy * 0.85) - 0.15, 0.0, 1.0)
        rgb = rgb * (1.0 - dark_edge[:, :, None] * 0.42)
        rgb = rgb * (1.0 - lit_edge[:, :, None] * 0.50) + palette["gold"] * lit_edge[:, :, None] * 0.50
        inner_outline = (signed_distance > 0.0) & (signed_distance < max(1.0, edge_width * 0.42))
        if inner_outline.any():
            light_side = (xx + yy) < 0.92
            rgb[inner_outline & light_side] = np.clip(
                rgb[inner_outline & light_side] * 0.35 + palette["gold"] * 0.65,
                0,
                255,
            )
            rgb[inner_outline & ~light_side] = np.clip(
                rgb[inner_outline & ~light_side] * 0.42 + palette["dark"] * 0.58,
                0,
                255,
            )
    else:
        edge = _edge_band_from_alpha(alpha)
        rgb[edge] = np.clip(rgb[edge] * 0.82 + palette["gold"] * 0.18, 0, 255)

    highlight = np.clip((top_left + center * 0.7) * (alpha.astype(np.float32) / 255.0), 0.0, 1.0)
    rgb = rgb * (1.0 - highlight[:, :, None] * 0.12) + palette["light"] * highlight[:, :, None] * 0.12

    alpha_out = alpha.copy()
    alpha_out[alpha_out < int(config.get("alpha_floor", 4))] = 0
    out = np.zeros((height, width, 4), dtype=np.uint8)
    visible = alpha_out > 0
    out[visible, :3] = np.clip(rgb[visible], 0, 255).astype(np.uint8)
    out[:, :, 3] = alpha_out
    return out


def _signed_distance(alpha: np.ndarray) -> np.ndarray | None:
    try:
        import cv2  # type: ignore

        inside = alpha > 127
        outside = ~inside
        if not inside.any() or not outside.any():
            return None
        dist_in = cv2.distanceTransform(inside.astype(np.uint8), cv2.DIST_L2, 3)
        dist_out = cv2.distanceTransform(outside.astype(np.uint8), cv2.DIST_L2, 3)
        return dist_in.astype(np.float32) - dist_out.astype(np.float32)
    except Exception:
        return None


def _edge_band_from_alpha(alpha: np.ndarray) -> np.ndarray:
    mask = alpha > 24
    if not mask.any():
        return mask
    eroded = erode_bool(mask, 1)
    return mask & ~eroded

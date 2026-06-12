from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def load_rgba(path: Path) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(path)
    return Image.open(path).convert("RGBA")


def chroma_key_mask(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    return (
        (red > 140)
        & (blue > 95)
        & (green < 155)
        & ((red - green) > 35)
        & ((blue - green) > 12)
        & ((red + blue) > ((green * 2) + 80))
    )


def chroma_fringe_candidate_mask(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    key_dist = np.sqrt(
        ((red - 255) * 0.85) ** 2
        + ((green - 0) * 1.15) ** 2
        + ((blue - 216) * 0.90) ** 2
    )
    return (
        (red > 90)
        & (blue > 65)
        & (green < 170)
        & ((red - green) > 20)
        & ((blue - green) > 8)
        & ((red + blue) > ((green * 2) + 45))
        & (key_dist < 205)
    )


def visible_magenta_fringe_mask(arr: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    key_dist = np.sqrt(
        ((red - 255) * 0.85) ** 2
        + ((green - 0) * 1.15) ** 2
        + ((blue - 216) * 0.90) ** 2
    )
    return (
        (alpha > 8)
        & (red > 95)
        & (blue > 65)
        & (green < 145)
        & ((red - green) > 25)
        & ((blue - green) > 12)
        & ((red + blue) > ((green * 2) + 55))
        & (key_dist < 210)
    )


def low_alpha_saturated_chroma_fringe_mask(arr: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    return (
        (alpha > 8)
        & (alpha <= 160)
        & (green < 70)
        & (np.maximum(red, blue) > 70)
        & ((red - green) > 25)
        & ((blue - green) > 45)
        & ((red + blue) > ((green * 2) + 95))
    )


def hidden_saturated_chroma_mask(arr: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    return (
        (alpha == 0)
        & (green < 70)
        & (np.maximum(red, blue) > 70)
        & ((red - green) > 25)
        & ((blue - green) > 45)
        & ((red + blue) > ((green * 2) + 95))
    )


def low_alpha_saturated_rgb_artifact_mask(arr: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation = maximum - minimum
    return (
        (alpha > 8)
        & (alpha <= 160)
        & (maximum > 48)
        & ((saturation > 40) | (minimum > 170))
    )


def hidden_saturated_rgb_artifact_mask(arr: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation = maximum - minimum
    return (
        (alpha == 0)
        & (maximum > 48)
        & ((saturation > 40) | (minimum > 170))
    )


def dilate_bool(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    out = mask.astype(bool).copy()
    for _ in range(iterations):
        expanded = out.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                sy0 = max(0, -dy)
                sy1 = min(out.shape[0], out.shape[0] - dy)
                dy0 = max(0, dy)
                dy1 = min(out.shape[0], out.shape[0] + dy)
                sx0 = max(0, -dx)
                sx1 = min(out.shape[1], out.shape[1] - dx)
                dx0 = max(0, dx)
                dx1 = min(out.shape[1], out.shape[1] + dx)
                expanded[dy0:dy1, dx0:dx1] |= out[sy0:sy1, sx0:sx1]
        out = expanded
    return out


def fill_mask_rgb_from_neighbors(arr: np.ndarray, mask: np.ndarray, iterations: int = 18) -> np.ndarray:
    out = arr.copy()
    pending = mask.astype(bool).copy()
    known = (out[:, :, 3] > 8) & ~pending
    height, width = pending.shape

    for _ in range(iterations):
        if not pending.any():
            break
        accum = np.zeros((height, width, 3), dtype=np.float32)
        count = np.zeros((height, width, 1), dtype=np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                sy0 = max(0, -dy)
                sy1 = min(height, height - dy)
                dy0 = max(0, dy)
                dy1 = min(height, height + dy)
                sx0 = max(0, -dx)
                sx1 = min(width, width - dx)
                dx0 = max(0, dx)
                dx1 = min(width, width + dx)
                neighbor_known = known[sy0:sy1, sx0:sx1]
                neighbor_rgb = out[sy0:sy1, sx0:sx1, :3].astype(np.float32)
                weight = neighbor_known[:, :, None].astype(np.float32)
                accum[dy0:dy1, dx0:dx1] += neighbor_rgb * weight
                count[dy0:dy1, dx0:dx1] += weight
        fill = pending & (count[:, :, 0] > 0)
        if not fill.any():
            break
        out[fill, :3] = np.clip(accum[fill] / count[fill], 0, 255).astype(np.uint8)
        pending[fill] = False
        known[fill] = True
    return out


def despill_visible_magenta(arr: np.ndarray) -> np.ndarray:
    out = arr.copy()
    near_transparent = dilate_bool(out[:, :, 3] <= 8, iterations=3)
    low_alpha_rgb_artifact = low_alpha_saturated_rgb_artifact_mask(out) & near_transparent
    if low_alpha_rgb_artifact.any():
        out[low_alpha_rgb_artifact, 3] = 0
        out[low_alpha_rgb_artifact, :3] = 0

    low_alpha_chroma = low_alpha_saturated_chroma_fringe_mask(out) & near_transparent
    if low_alpha_chroma.any():
        out[low_alpha_chroma, 3] = 0
        out[low_alpha_chroma, :3] = 0

    fringe = visible_magenta_fringe_mask(out)
    if not fringe.any():
        return out

    strong_key = fringe & near_transparent & chroma_fringe_candidate_mask(out[:, :, :3])
    out[strong_key, 3] = 0
    out[strong_key, :3] = 0

    remaining = visible_magenta_fringe_mask(out)
    if remaining.any():
        out = fill_mask_rgb_from_neighbors(out, remaining)
    return out


def chroma_to_alpha(image: Image.Image) -> Image.Image:
    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    hard_chroma = chroma_key_mask(arr[:, :, :3])
    background = hard_chroma.copy()
    fringe_candidate = chroma_fringe_candidate_mask(arr[:, :, :3])
    for _ in range(4):
        grown = dilate_bool(background, iterations=1) & fringe_candidate
        if np.array_equal(grown | background, background):
            break
        background |= grown
    arr[background, 3] = 0
    arr[background, :3] = 0
    arr = despill_visible_magenta(arr)
    return Image.fromarray(arr, "RGBA")


def alpha_bbox(image: Image.Image, threshold: int = 8) -> tuple[int, int, int, int]:
    alpha = np.asarray(image.convert("RGBA"))[:, :, 3]
    ys, xs = np.where(alpha > threshold)
    if len(xs) == 0:
        raise ValueError("No non-transparent pixels found")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def component_boxes(image: Image.Image, min_area: int = 1200) -> list[tuple[int, int, int, int]]:
    alpha = np.asarray(image.convert("RGBA"))[:, :, 3]
    mask = (alpha > 8).astype(np.uint8)
    try:
        import cv2  # type: ignore

        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        boxes: list[tuple[int, int, int, int]] = []
        for label in range(1, count):
            x, y, w, h, area = stats[label]
            if int(area) >= min_area:
                boxes.append((int(x), int(y), int(x + w), int(y + h)))
        return boxes
    except Exception:
        return [alpha_bbox(image)]


def crop_box(image: Image.Image, box: tuple[int, int, int, int], pad: int = 24) -> Image.Image:
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(image.width, x1 + pad)
    y1 = min(image.height, y1 + pad)
    return image.crop((x0, y0, x1, y1))


def dilate_transparent_rgb(arr: np.ndarray, iterations: int = 18) -> np.ndarray:
    out = arr.copy()
    known = out[:, :, 3] > 0
    height, width = known.shape

    for _ in range(iterations):
        unknown = ~known
        if not unknown.any():
            break
        accum = np.zeros((height, width, 3), dtype=np.float32)
        count = np.zeros((height, width, 1), dtype=np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                sy0 = max(0, -dy)
                sy1 = min(height, height - dy)
                dy0 = max(0, dy)
                dy1 = min(height, height + dy)
                sx0 = max(0, -dx)
                sx1 = min(width, width - dx)
                dx0 = max(0, dx)
                dx1 = min(width, width + dx)
                neighbor_known = known[sy0:sy1, sx0:sx1]
                neighbor_rgb = out[sy0:sy1, sx0:sx1, :3].astype(np.float32)
                mask = neighbor_known[:, :, None].astype(np.float32)
                accum[dy0:dy1, dx0:dx1] += neighbor_rgb * mask
                count[dy0:dy1, dx0:dx1] += mask
        fill = unknown & (count[:, :, 0] > 0)
        if not fill.any():
            break
        out[fill, :3] = np.clip(accum[fill] / count[fill], 0, 255).astype(np.uint8)
        known[fill] = True
    return out


def clear_outer_alpha(arr: np.ndarray, pixels: int = 1) -> np.ndarray:
    out = arr.copy()
    if pixels <= 0:
        return out
    out[:pixels, :, 3] = 0
    out[-pixels:, :, 3] = 0
    out[:, :pixels, 3] = 0
    out[:, -pixels:, 3] = 0
    return out


def resize_premultiplied(image: Image.Image, size: tuple[int, int], clear_outer_pixels: int = 1) -> Image.Image:
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = arr[:, :, 3:4] / 255.0
    premul = arr.copy()
    premul[:, :, :3] *= alpha
    resized = Image.fromarray(np.clip(premul, 0, 255).astype(np.uint8), "RGBA").resize(
        size, Image.Resampling.LANCZOS
    )
    rarr = np.asarray(resized, dtype=np.float32)
    a = rarr[:, :, 3:4] / 255.0
    nonzero = a[:, :, 0] > 1.0 / 255.0
    rarr[nonzero, :3] = np.clip(rarr[nonzero, :3] / a[nonzero], 0, 255)
    out = np.asarray(chroma_to_alpha(Image.fromarray(np.clip(rarr, 0, 255).astype(np.uint8), "RGBA")))
    out = dilate_transparent_rgb(out)
    out = despill_visible_magenta(out)
    out = dilate_transparent_rgb(out)
    out = clear_outer_alpha(out, clear_outer_pixels)
    out = dilate_transparent_rgb(out, iterations=64)
    hidden = hidden_saturated_chroma_mask(out)
    if hidden.any():
        out[hidden, :3] = 0
    hidden_rgb_artifact = hidden_saturated_rgb_artifact_mask(out)
    if hidden_rgb_artifact.any():
        out[hidden_rgb_artifact, :3] = 0
    return Image.fromarray(out, "RGBA")


def diagnostics(image: Image.Image) -> dict[str, object]:
    arr = np.asarray(image.convert("RGBA"))
    alpha = arr[:, :, 3]
    visible_chroma = (alpha > 8) & chroma_key_mask(arr[:, :, :3])
    visible_magenta = visible_magenta_fringe_mask(arr)
    low_alpha_chroma = low_alpha_saturated_chroma_fringe_mask(arr)
    hidden_saturated_chroma = hidden_saturated_chroma_mask(arr)
    low_alpha_rgb_artifact = low_alpha_saturated_rgb_artifact_mask(arr)
    hidden_rgb_artifact = hidden_saturated_rgb_artifact_mask(arr)
    corners = [alpha[0, 0], alpha[0, -1], alpha[-1, 0], alpha[-1, -1]]
    edge = np.concatenate([alpha[0, :], alpha[-1, :], alpha[:, 0], alpha[:, -1]])
    return {
        "size": [image.width, image.height],
        "alpha_bbox": list(alpha_bbox(image)) if int((alpha > 8).sum()) else None,
        "opaque_or_translucent_pixels": int((alpha > 0).sum()),
        "transparent_corners": all(int(v) == 0 for v in corners),
        "edge_alpha_gt0": int((edge > 0).sum()),
        "edge_alpha_gt8": int((edge > 8).sum()),
        "max_edge_alpha": int(edge.max()) if edge.size else 0,
        "visible_chroma_key_pixels_alpha_gt_8": int(visible_chroma.sum()),
        "visible_magenta_fringe_pixels_alpha_gt_8": int(visible_magenta.sum()),
        "low_alpha_saturated_chroma_fringe_pixels": int(low_alpha_chroma.sum()),
        "hidden_saturated_chroma_pixels_alpha_eq_0": int(hidden_saturated_chroma.sum()),
        "low_alpha_saturated_rgb_artifact_pixels": int(low_alpha_rgb_artifact.sum()),
        "hidden_saturated_rgb_artifact_pixels_alpha_eq_0": int(hidden_rgb_artifact.sum()),
    }

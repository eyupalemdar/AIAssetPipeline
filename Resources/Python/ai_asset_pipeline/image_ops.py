from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


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
        (alpha > 0)
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
        (alpha > 0)
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


def erode_bool(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    out = mask.astype(bool).copy()
    for _ in range(iterations):
        eroded = out.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                sy0 = max(0, -dy)
                sy1 = min(out.shape[0], out.shape[0] - dy)
                dy0 = max(0, dy)
                dy1 = min(out.shape[0], out.shape[0] + dy)
                sx0 = max(0, -dx)
                sx1 = min(out.shape[1], out.shape[1] - dx)
                dx0 = max(0, dx)
                dx1 = min(out.shape[1], out.shape[1] + dx)
                view = np.zeros_like(out)
                view[dy0:dy1, dx0:dx1] = out[sy0:sy1, sx0:sx1]
                eroded &= view
        out = eroded
    return out


def open_bool(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    return dilate_bool(erode_bool(mask, iterations), iterations)


def close_bool(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    return erode_bool(dilate_bool(mask, iterations), iterations)


def connected_component_stats(mask: np.ndarray) -> list[dict[str, int]]:
    mask = mask.astype(bool)
    if not mask.any():
        return []
    try:
        import cv2  # type: ignore

        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        items: list[dict[str, int]] = []
        for label in range(1, count):
            x, y, w, h, area = stats[label]
            items.append(
                {
                    "x0": int(x),
                    "y0": int(y),
                    "x1": int(x + w),
                    "y1": int(y + h),
                    "area": int(area),
                }
            )
        return items
    except Exception:
        height, width = mask.shape
        visited = np.zeros_like(mask, dtype=bool)
        items: list[dict[str, int]] = []
        for start_y in range(height):
            for start_x in range(width):
                if visited[start_y, start_x] or not mask[start_y, start_x]:
                    continue
                stack = [(start_x, start_y)]
                visited[start_y, start_x] = True
                area = 0
                min_x = max_x = start_x
                min_y = max_y = start_y
                while stack:
                    x, y = stack.pop()
                    area += 1
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)
                    for ny in range(max(0, y - 1), min(height, y + 2)):
                        for nx in range(max(0, x - 1), min(width, x + 2)):
                            if visited[ny, nx] or not mask[ny, nx]:
                                continue
                            visited[ny, nx] = True
                            stack.append((nx, ny))
                items.append({"x0": min_x, "y0": min_y, "x1": max_x + 1, "y1": max_y + 1, "area": area})
        return items


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


def neutralize_hidden_artifacts(arr: np.ndarray) -> np.ndarray:
    out = arr.copy()
    hidden_chroma = hidden_saturated_chroma_mask(out)
    if hidden_chroma.any():
        out[hidden_chroma, :3] = 0
    hidden_rgb_artifact = hidden_saturated_rgb_artifact_mask(out)
    if hidden_rgb_artifact.any():
        out[hidden_rgb_artifact, :3] = 0
    return out


def keep_largest_alpha_component(arr: np.ndarray, alpha_threshold: int = 8) -> np.ndarray:
    out = arr.copy()
    alpha = out[:, :, 3]
    components = connected_component_stats(alpha > alpha_threshold)
    if not components:
        return out
    largest = max(components, key=lambda item: int(item["area"]))
    keep = np.zeros(alpha.shape, dtype=bool)
    keep[
        largest["y0"] : largest["y1"],
        largest["x0"] : largest["x1"],
    ] = alpha[
        largest["y0"] : largest["y1"],
        largest["x0"] : largest["x1"],
    ] > alpha_threshold
    out[~keep, 3] = 0
    out[~keep, :3] = 0
    return out


def clean_button_icon_overlay(
    image: Image.Image,
    *,
    alpha_threshold: int = 8,
    small_component_min_area: int = 96,
    neutral_haze_max_rgb: int = 118,
    neutral_haze_max_saturation: int = 48,
    weak_dark_max_alpha: int = 170,
    weak_dark_max_rgb: int = 105,
    very_weak_alpha: int = 34,
    dilation_iterations: int = 24,
) -> Image.Image:
    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    arr = keep_largest_alpha_component(arr, alpha_threshold)

    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation = maximum - minimum
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]

    # The derived icon overlays are bronze/gold. Neutral low-alpha gray pixels
    # are difference-mask residue from the old button face, not icon material.
    bronze = (red > 70) & (red > green + 7) & (green >= blue - 10) & (saturation > 20)
    gold = (red > 120) & (green > 70) & (red > green + 4) & (green > blue + 8)
    neutral_haze = (
        (alpha > 0)
        & (alpha < 250)
        & (maximum < neutral_haze_max_rgb)
        & (saturation < neutral_haze_max_saturation)
        & ~bronze
        & ~gold
    )
    weak_dark = (alpha > 0) & (alpha <= weak_dark_max_alpha) & (maximum < weak_dark_max_rgb) & ~bronze & ~gold
    very_weak = (alpha > 0) & (alpha < very_weak_alpha)
    remove = neutral_haze | weak_dark | very_weak
    if remove.any():
        arr[remove, 3] = 0
        arr[remove, :3] = 0

    arr, _ = remove_alpha_speckles(arr, small_component_min_area, alpha_threshold)
    arr = dilate_transparent_rgb(arr, iterations=dilation_iterations)
    arr = neutralize_hidden_artifacts(arr)
    return Image.fromarray(arr, "RGBA")


def remove_alpha_speckles(
    arr: np.ndarray,
    min_area: int = 12,
    alpha_threshold: int = 8,
) -> tuple[np.ndarray, dict[str, int]]:
    out = arr.copy()
    if min_area <= 1:
        return out, {"removed_components": 0, "removed_pixels": 0}
    mask = out[:, :, 3] > alpha_threshold
    components = connected_component_stats(mask)
    remove_mask = np.zeros(mask.shape, dtype=bool)
    removed_components = 0
    removed_pixels = 0
    for component in components:
        area = int(component["area"])
        if area >= min_area:
            continue
        remove_mask[component["y0"] : component["y1"], component["x0"] : component["x1"]] |= mask[
            component["y0"] : component["y1"], component["x0"] : component["x1"]
        ]
        removed_components += 1
        removed_pixels += area
    if remove_mask.any():
        out[remove_mask, 3] = 0
        out[remove_mask, :3] = 0
    return out, {"removed_components": removed_components, "removed_pixels": removed_pixels}


def alpha_component_metrics(arr: np.ndarray, alpha_threshold: int = 8, small_area_threshold: int = 12) -> dict[str, int]:
    components = connected_component_stats(arr[:, :, 3] > alpha_threshold)
    small = [item for item in components if int(item["area"]) < small_area_threshold]
    largest = max((int(item["area"]) for item in components), default=0)
    return {
        "alpha_component_count": len(components),
        "alpha_largest_component_area": largest,
        "small_alpha_component_area_threshold": int(small_area_threshold),
        "small_alpha_component_count": len(small),
        "small_alpha_component_pixels": int(sum(int(item["area"]) for item in small)),
    }


def chroma_to_alpha(image: Image.Image) -> Image.Image:
    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    rgb_float = arr[:, :, :3].astype(np.float32)
    border = np.concatenate(
        (rgb_float[0, :, :], rgb_float[-1, :, :], rgb_float[:, 0, :], rgb_float[:, -1, :]),
        axis=0,
    )
    border_key = np.median(border, axis=0)
    key_max = float(border_key.max())
    key_min = float(border_key.min())
    if key_max - key_min >= 80.0 and key_max >= 140.0:
        dominant = int(np.argmax(border_key))
        distance = np.linalg.norm(rgb_float - border_key[None, None, :], axis=2)
        other = [channel for channel in range(3) if channel != dominant]
        dominant_margin = rgb_float[:, :, dominant] - np.maximum(
            rgb_float[:, :, other[0]], rgb_float[:, :, other[1]]
        )
        candidate = (distance <= 118.0) & (dominant_margin >= 34.0) & (rgb_float[:, :, dominant] >= 125.0)
        try:
            import cv2  # type: ignore

            _count, labels = cv2.connectedComponents(candidate.astype(np.uint8), 8)
            border_labels = np.unique(
                np.concatenate((labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]))
            )
            border_labels = border_labels[border_labels != 0]
            connected = np.isin(labels, border_labels)
        except Exception:
            from collections import deque

            connected = np.zeros(candidate.shape, dtype=bool)
            queue: deque[tuple[int, int]] = deque()
            for x in range(candidate.shape[1]):
                for y in (0, candidate.shape[0] - 1):
                    if candidate[y, x] and not connected[y, x]:
                        connected[y, x] = True
                        queue.append((y, x))
            for y in range(candidate.shape[0]):
                for x in (0, candidate.shape[1] - 1):
                    if candidate[y, x] and not connected[y, x]:
                        connected[y, x] = True
                        queue.append((y, x))
            while queue:
                y, x = queue.popleft()
                for ny in range(max(0, y - 1), min(candidate.shape[0], y + 2)):
                    for nx in range(max(0, x - 1), min(candidate.shape[1], x + 2)):
                        if candidate[ny, nx] and not connected[ny, nx]:
                            connected[ny, nx] = True
                            queue.append((ny, nx))
        if connected.any():
            arr[connected, 3] = 0
            arr[connected, :3] = 0
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


def clean_existing_source_target_size(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    alpha_open_iterations: int = 0,
    alpha_close_iterations: int = 0,
    pre_speckle_min_area: int = 0,
    post_speckle_min_area: int = 12,
) -> Image.Image:
    arr = np.asarray(chroma_to_alpha(image), dtype=np.uint8)
    if pre_speckle_min_area > 1:
        arr, _ = remove_alpha_speckles(arr, pre_speckle_min_area)
    if alpha_open_iterations > 0 or alpha_close_iterations > 0:
        alpha = arr[:, :, 3]
        mask = alpha > 8
        if alpha_open_iterations > 0:
            mask = open_bool(mask, alpha_open_iterations)
        if alpha_close_iterations > 0:
            mask = close_bool(mask, alpha_close_iterations)
        arr[~mask, 3] = 0
        arr[~mask, :3] = 0
    arr = despill_visible_magenta(arr)
    arr = neutralize_hidden_artifacts(arr)
    resized = resize_premultiplied(Image.fromarray(arr, "RGBA"), size, 0)
    out = np.asarray(resized.convert("RGBA"), dtype=np.uint8)
    out, _ = remove_alpha_speckles(out, post_speckle_min_area)
    out = despill_visible_magenta(out)
    out = dilate_transparent_rgb(out, iterations=64)
    out = clear_outer_alpha(out, clear_outer_pixels)
    out = dilate_transparent_rgb(out, iterations=64)
    out = neutralize_hidden_artifacts(out)
    return Image.fromarray(out, "RGBA")


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


def smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    if edge1 <= edge0:
        raise ValueError("smoothstep edge1 must be greater than edge0")
    t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - (2.0 * t))


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


def _srgb_to_linear(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0.0, 1.0)
    return np.where(value <= 0.04045, value / 12.92, np.power((value + 0.055) / 1.055, 2.4))


def _linear_to_srgb(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0.0, 1.0)
    return np.where(value <= 0.0031308, value * 12.92, 1.055 * np.power(value, 1.0 / 2.4) - 0.055)


def _resize_float_plane(value: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(
        Image.fromarray(value.astype(np.float32), "F").resize(size, Image.Resampling.LANCZOS),
        dtype=np.float32,
    )


def resize_linear_light_premultiplied(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
) -> Image.Image:
    """Resize RGBA in linear light with premultiplied RGB.

    This is intentionally separate from the legacy resize path so existing
    specs keep byte-compatible behavior. Canonical-shape components opt into
    this path to avoid sRGB dark fringes while preserving aspect ratio.
    """

    width, height = (int(size[0]), int(size[1]))
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid target size: {size}")

    cleaned = chroma_to_alpha(image.convert("RGBA"))
    arr = np.asarray(cleaned, dtype=np.float32) / 255.0
    alpha = arr[:, :, 3]
    linear = _srgb_to_linear(arr[:, :, :3])
    premultiplied = linear * alpha[:, :, None]

    resized_alpha = np.clip(_resize_float_plane(alpha, (width, height)), 0.0, 1.0)
    resized_premultiplied = np.stack(
        [_resize_float_plane(premultiplied[:, :, channel], (width, height)) for channel in range(3)],
        axis=2,
    )
    resized_premultiplied = np.clip(resized_premultiplied, 0.0, 1.0)
    nonzero = resized_alpha > (1.0 / 65535.0)
    resized_linear = np.zeros_like(resized_premultiplied)
    resized_linear[nonzero] = np.clip(
        resized_premultiplied[nonzero] / resized_alpha[nonzero, None],
        0.0,
        1.0,
    )

    out = np.zeros((height, width, 4), dtype=np.uint8)
    out[:, :, :3] = np.clip(np.rint(_linear_to_srgb(resized_linear) * 255.0), 0, 255).astype(np.uint8)
    out[:, :, 3] = np.clip(np.rint(resized_alpha * 255.0), 0, 255).astype(np.uint8)
    out = dilate_transparent_rgb(out, iterations=64)
    out = despill_visible_magenta(out)
    out = clear_outer_alpha(out, clear_outer_pixels)
    out = dilate_transparent_rgb(out, iterations=64)
    out = neutralize_hidden_artifacts(out)
    return Image.fromarray(out, "RGBA")


def _rgb_triplet(value: object, default: tuple[int, int, int]) -> np.ndarray:
    if not isinstance(value, list) or len(value) != 3:
        return np.array(default, dtype=np.float32) / 255.0
    return np.array([int(v) for v in value], dtype=np.float32) / 255.0


def resize_soft_glow(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 2,
    *,
    alpha_median_size: int = 3,
    alpha_blur_radius: float = 1.15,
    alpha_low_cutoff: float = 0.035,
    alpha_high_cutoff: float = 0.92,
    alpha_gamma: float = 1.18,
    alpha_scale: float = 0.86,
    alpha_cap: float = 0.86,
    border_fade_px: float = 7.0,
    brightness_blur_radius: float = 1.25,
    palette: dict[str, object] | None = None,
) -> Image.Image:
    """Resize a soft glow while preventing noisy low-alpha perimeter rims.

    This mode is intentionally opt-in. It is for AI/model-generated bloom,
    halo, shadow, and reflection assets where a normal Lanczos downsample can
    compress source perimeter noise into a visible outer ring.
    """
    resized = resize_premultiplied(image, size, 0)
    arr = np.asarray(resized.convert("RGBA"), dtype=np.float32)

    alpha = arr[:, :, 3] / 255.0
    alpha_image = Image.fromarray(np.clip(alpha * 255.0, 0, 255).astype(np.uint8), "L")
    if alpha_median_size >= 3:
        if alpha_median_size % 2 == 0:
            alpha_median_size += 1
        alpha_image = alpha_image.filter(ImageFilter.MedianFilter(size=alpha_median_size))
    if alpha_blur_radius > 0:
        alpha_image = alpha_image.filter(ImageFilter.GaussianBlur(radius=alpha_blur_radius))
    alpha = np.asarray(alpha_image, dtype=np.float32) / 255.0
    alpha = smoothstep(float(alpha_low_cutoff), float(alpha_high_cutoff), alpha)
    alpha = np.power(alpha, max(float(alpha_gamma), 0.01))

    height, width = alpha.shape
    if border_fade_px > 0:
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
        border_distance = np.minimum.reduce([xx, yy, width - 1.0 - xx, height - 1.0 - yy])
        alpha *= smoothstep(0.0, float(border_fade_px), border_distance)
    alpha = np.clip(alpha * float(alpha_scale), 0.0, float(alpha_cap))

    rgb = arr[:, :, :3] / 255.0
    if palette:
        brightness = rgb.max(axis=2)
        if brightness_blur_radius > 0:
            brightness_image = Image.fromarray(np.clip(brightness * 255.0, 0, 255).astype(np.uint8), "L")
            brightness_image = brightness_image.filter(ImageFilter.GaussianBlur(radius=brightness_blur_radius))
            brightness = np.asarray(brightness_image, dtype=np.float32) / 255.0

        outer = _rgb_triplet(palette.get("outer"), (47, 142, 79))
        body = _rgb_triplet(palette.get("body"), (101, 201, 65))
        core = _rgb_triplet(palette.get("core"), (183, 245, 29))
        body_edge = palette.get("body_edge", [0.18, 0.62])
        core_edge = palette.get("core_edge", [0.68, 0.94])
        if not isinstance(body_edge, list) or len(body_edge) != 2:
            body_edge = [0.18, 0.62]
        if not isinstance(core_edge, list) or len(core_edge) != 2:
            core_edge = [0.68, 0.94]
        body_mix = smoothstep(float(body_edge[0]), float(body_edge[1]), brightness)[:, :, None]
        core_mix = smoothstep(float(core_edge[0]), float(core_edge[1]), brightness)[:, :, None]
        core_mix *= float(palette.get("core_mix", 0.55))
        rgb = outer * (1.0 - body_mix) + body * body_mix
        rgb = rgb * (1.0 - core_mix) + core * core_mix

    out = np.zeros((height, width, 4), dtype=np.uint8)
    out[:, :, :3] = np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)
    out[:, :, 3] = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
    out[out[:, :, 3] < 3, 3] = 0
    out[out[:, :, 3] == 0, :3] = 0
    out = clear_outer_alpha(out, clear_outer_pixels)
    out = dilate_transparent_rgb(out, iterations=64)
    out = neutralize_hidden_artifacts(out)
    return Image.fromarray(out, "RGBA")


def resize_luma_mask(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    luma_gamma: float = 1.0,
    luma_scale: float = 1.0,
    luma_floor: float = 0.0,
    alpha_scale: float = 1.0,
    alpha_cap: float = 1.0,
    transparent_rgb_dilation: int = 64,
) -> Image.Image:
    """Create a grayscale luma/alpha texture for material-side color locking.

    This mode intentionally avoids final color decisions. Image generation owns
    form, falloff, gloss, and luminance detail; the consuming UE material owns
    BodyColor/PeakColor and opacity/intensity.
    """
    resized = resize_premultiplied(chroma_to_alpha(image), size, 0)
    arr = np.asarray(resized.convert("RGBA"), dtype=np.float32)
    rgb = arr[:, :, :3] / 255.0
    alpha = np.clip((arr[:, :, 3] / 255.0) * float(alpha_scale), 0.0, float(alpha_cap))

    luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    luma = np.power(np.clip(luma * float(luma_scale), 0.0, 1.0), max(float(luma_gamma), 0.01))
    if luma_floor > 0:
        visible = alpha > 0
        luma[visible] = np.maximum(luma[visible], float(luma_floor))

    out = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    luma8 = np.clip(np.rint(luma * 255.0), 0, 255).astype(np.uint8)
    out[:, :, 0] = luma8
    out[:, :, 1] = luma8
    out[:, :, 2] = luma8
    out[:, :, 3] = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
    out[out[:, :, 3] < 3, 3] = 0
    out[out[:, :, 3] == 0, :3] = 0
    out = clear_outer_alpha(out, clear_outer_pixels)
    if transparent_rgb_dilation > 0:
        out = dilate_transparent_rgb(out, iterations=int(transparent_rgb_dilation))
    out = neutralize_hidden_artifacts(out)
    return Image.fromarray(out, "RGBA")


def _rounded_rect_alpha(
    size: tuple[int, int],
    *,
    outer_box: tuple[float, float, float, float] | None = None,
    radius: float = 8.0,
    supersample: int = 16,
    edge_blur_px: float = 0.0,
) -> np.ndarray:
    width, height = size
    scale = max(3, int(supersample))
    if outer_box is None:
        outer_box = (0.0, 0.0, float(width), float(height))

    alpha_hi = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(alpha_hi)
    scaled_outer = tuple(int(round(value * scale)) for value in outer_box)
    draw.rounded_rectangle(scaled_outer, radius=int(round(max(0.0, radius) * scale)), fill=255)
    if edge_blur_px > 0:
        alpha_hi = alpha_hi.filter(ImageFilter.GaussianBlur(radius=edge_blur_px * scale))
    return np.asarray(alpha_hi.resize((width, height), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0


def resize_reference_color_pill(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    outer_box: list[float] | tuple[float, float, float, float] | None = None,
    radius: float = 17.5,
    supersample: int = 16,
    edge_blur_px: float = 0.02,
    alpha_cutoff: float = 0.58,
    min_visible_alpha: float = 0.68,
    transparent_rgb_dilation: int = 0,
) -> Image.Image:
    """Preserve the approved source RGB while replacing only the pill alpha.

    Use this for source-approved color bars where the shape must be clean and
    deterministic, but the source RGB/rim/gloss character should remain the
    visual authority.
    """
    resized = resize_premultiplied(chroma_to_alpha(image), size, 0)
    arr = np.asarray(resized.convert("RGBA"), dtype=np.uint8).copy()
    arr = despill_visible_magenta(arr)

    parsed_outer: tuple[float, float, float, float] | None = None
    if outer_box is not None:
        if len(outer_box) != 4:
            raise ValueError("reference_color_pill_resize outer_box must be [x0,y0,x1,y1]")
        parsed_outer = tuple(float(v) for v in outer_box)

    alpha = _rounded_rect_alpha(
        size,
        outer_box=parsed_outer,
        radius=radius,
        supersample=supersample,
        edge_blur_px=edge_blur_px,
    )
    alpha = np.where(alpha >= float(alpha_cutoff), np.maximum(alpha, float(min_visible_alpha)), 0.0)

    out = arr.copy()
    out[:, :, 3] = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
    out[out[:, :, 3] == 0, :3] = 0
    if clear_outer_pixels:
        out = clear_outer_alpha(out, clear_outer_pixels)
        out[out[:, :, 3] == 0, :3] = 0
    if transparent_rgb_dilation > 0:
        out = dilate_transparent_rgb(out, iterations=int(transparent_rgb_dilation))
        out[out[:, :, 3] == 0, :3] = 0
    out = despill_visible_magenta(out)
    out[out[:, :, 3] == 0, :3] = 0
    return Image.fromarray(out, "RGBA")


def resize_edge_particle_extract(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    source_region_width_px: int = 150,
    source_right_pad_px: int = 8,
    source_vertical_pad_px: int = 6,
    x_fade_start: float = 0.18,
    x_fade_end: float = 0.52,
    alpha_gamma: float = 0.85,
    alpha_scale: float = 1.0,
    min_alpha: float = 0.015,
) -> Image.Image:
    """Extract the real right-edge spark/particle field from a timer bar.

    The mode intentionally preserves small alpha components. It only suppresses
    the solid bar interior with an X fade so the material can remap this texture
    to the active fill boundary.
    """
    cleaned = chroma_to_alpha(image)
    bbox = alpha_bbox(cleaned)
    region_width = max(1, int(source_region_width_px))
    right_pad = max(0, int(source_right_pad_px))
    vertical_pad = max(0, int(source_vertical_pad_px))
    x0 = max(0, bbox[2] - region_width)
    x1 = min(cleaned.width, bbox[2] + right_pad)
    y0 = max(0, bbox[1] - vertical_pad)
    y1 = min(cleaned.height, bbox[3] + vertical_pad)

    region = cleaned.crop((x0, y0, x1, y1))
    resized = resize_premultiplied(region, size, 0)
    arr = np.asarray(resized.convert("RGBA"), dtype=np.uint8).copy()
    arr = despill_visible_magenta(arr)

    height, width = arr.shape[:2]
    xx = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    fade = smoothstep(float(x_fade_start), float(x_fade_end), xx)
    alpha = (arr[:, :, 3].astype(np.float32) / 255.0) * fade
    alpha = np.power(np.clip(alpha * float(alpha_scale), 0.0, 1.0), max(float(alpha_gamma), 0.01))
    alpha[alpha < float(min_alpha)] = 0.0

    out = arr.copy()
    out[:, :, 3] = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
    out[out[:, :, 3] == 0, :3] = 0
    if clear_outer_pixels:
        out = clear_outer_alpha(out, clear_outer_pixels)
        out[out[:, :, 3] == 0, :3] = 0
    out = despill_visible_magenta(out)
    out[out[:, :, 3] == 0, :3] = 0
    return Image.fromarray(out, "RGBA")


def _crop_alpha_bbox_with_pad(image: Image.Image, pad_px: int) -> Image.Image:
    x0, y0, x1, y1 = alpha_bbox(image)
    pad = max(0, int(pad_px))
    return image.crop(
        (
            max(0, x0 - pad),
            max(0, y0 - pad),
            min(image.width, x1 + pad),
            min(image.height, y1 + pad),
        )
    )


def _ratio_delta_pct(source_size: tuple[int, int], target_size: tuple[int, int]) -> float:
    source_w, source_h = source_size
    target_w, target_h = target_size
    if source_w <= 0 or source_h <= 0 or target_w <= 0 or target_h <= 0:
        raise ValueError(f"Invalid ratio source/target sizes: {source_size} -> {target_size}")
    source_ratio = float(source_w) / float(source_h)
    target_ratio = float(target_w) / float(target_h)
    return abs(source_ratio - target_ratio) / target_ratio * 100.0


def _fit_aspect_on_transparent_canvas(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    source_w, source_h = image.size
    if target_w <= 0 or target_h <= 0:
        raise ValueError(f"Invalid target size: {size}")
    if source_w <= 0 or source_h <= 0:
        raise ValueError(f"Invalid source size: {image.size}")

    scale = min(target_w / source_w, target_h / source_h)
    fit_size = (
        max(1, int(round(source_w * scale))),
        max(1, int(round(source_h * scale))),
    )
    fitted = resize_premultiplied(image, fit_size, 0)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(fitted, ((target_w - fit_size[0]) // 2, (target_h - fit_size[1]) // 2))
    return canvas


def _sanitize_timer_output(
    image: Image.Image,
    clear_outer_pixels: int,
    *,
    min_visible_alpha: float,
    alpha_floor_cutoff: float,
    transparent_rgb_dilation: int,
) -> Image.Image:
    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    arr = despill_visible_magenta(arr)

    alpha = arr[:, :, 3].astype(np.float32) / 255.0
    remove = (alpha > 0.0) & (alpha < float(alpha_floor_cutoff))
    if remove.any():
        arr[remove, 3] = 0
        arr[remove, :3] = 0

    min_alpha_u8 = int(round(np.clip(float(min_visible_alpha), 0.0, 1.0) * 255.0))
    floor = (arr[:, :, 3] > 0) & (arr[:, :, 3] < min_alpha_u8)
    if floor.any():
        arr[floor, 3] = min_alpha_u8

    if clear_outer_pixels:
        arr = clear_outer_alpha(arr, clear_outer_pixels)
    if transparent_rgb_dilation > 0:
        arr = dilate_transparent_rgb(arr, iterations=int(transparent_rgb_dilation))
    arr = neutralize_hidden_artifacts(arr)
    arr = despill_visible_magenta(arr)
    arr[arr[:, :, 3] == 0, :3] = 0
    return Image.fromarray(arr, "RGBA")


def resize_nameplate_timer_aaa_fill(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    alpha_bbox_pad_px: int = 12,
    aspect_ratio_tolerance_pct: float = 3.0,
    min_visible_alpha: float = 0.66,
    alpha_floor_cutoff: float = 0.025,
    transparent_rgb_dilation: int = 64,
) -> Image.Image:
    """Create a no-stretch AAA timer body texture from one approved fill source.

    Unlike ``reference_color_pill_resize``, this path does not replace the
    source alpha with a synthetic rounded rectangle. It cleans the chroma matte,
    crops the real generated fill silhouette, validates the ratio, and fits it
    onto the target canvas with uniform scaling.
    """
    cleaned = chroma_to_alpha(image)
    cropped = _crop_alpha_bbox_with_pad(cleaned, alpha_bbox_pad_px)
    ratio_delta = _ratio_delta_pct(cropped.size, size)
    if ratio_delta > float(aspect_ratio_tolerance_pct):
        raise ValueError(
            "nameplate_timer_aaa_fill_resize aspect ratio delta "
            f"{ratio_delta:.3f}% exceeds tolerance {float(aspect_ratio_tolerance_pct):.3f}% "
            f"for {cropped.size} -> {size}"
        )
    fitted = _fit_aspect_on_transparent_canvas(cropped, size)
    return _sanitize_timer_output(
        fitted,
        clear_outer_pixels=max(1, clear_outer_pixels),
        min_visible_alpha=min_visible_alpha,
        alpha_floor_cutoff=alpha_floor_cutoff,
        transparent_rgb_dilation=transparent_rgb_dilation,
    )


def resize_nameplate_timer_aaa_edge(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    alpha_bbox_pad_px: int = 18,
    aspect_ratio_tolerance_pct: float = 3.0,
    min_visible_alpha: float = 0.0,
    alpha_floor_cutoff: float = 0.015,
    transparent_rgb_dilation: int = 64,
) -> Image.Image:
    """Create a boundary-local AAA timer fracture texture without particle-only cropping."""
    cleaned = chroma_to_alpha(image)
    cropped = _crop_alpha_bbox_with_pad(cleaned, alpha_bbox_pad_px)
    ratio_delta = _ratio_delta_pct(cropped.size, size)
    if ratio_delta > float(aspect_ratio_tolerance_pct):
        raise ValueError(
            "nameplate_timer_aaa_edge_resize aspect ratio delta "
            f"{ratio_delta:.3f}% exceeds tolerance {float(aspect_ratio_tolerance_pct):.3f}% "
            f"for {cropped.size} -> {size}"
        )
    fitted = _fit_aspect_on_transparent_canvas(cropped, size)
    return _sanitize_timer_output(
        fitted,
        clear_outer_pixels=max(1, clear_outer_pixels),
        min_visible_alpha=min_visible_alpha,
        alpha_floor_cutoff=alpha_floor_cutoff,
        transparent_rgb_dilation=transparent_rgb_dilation,
    )


def resize_nameplate_timer_aaa_edge_flipbook_atlas(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    frame_size: tuple[int, int] = (256, 128),
    columns: int = 4,
    rows: int = 3,
    frame_count: int = 12,
    alpha_bbox_pad_px: int = 18,
    aspect_ratio_tolerance_pct: float = 8.0,
    min_visible_alpha: float = 0.0,
    alpha_floor_cutoff: float = 0.012,
    transparent_rgb_dilation: int = 64,
    background_max_rgb_cutoff: int = 6,
) -> Image.Image:
    """Create a packed RGBA edge flipbook atlas from an Image 2.0 source sheet."""
    frame_w, frame_h = int(frame_size[0]), int(frame_size[1])
    cols, atlas_rows, frames = int(columns), int(rows), int(frame_count)
    if frame_w <= 0 or frame_h <= 0 or cols <= 0 or atlas_rows <= 0 or frames <= 0:
        raise ValueError("Invalid nameplate timer edge atlas dimensions")
    expected_size = (frame_w * cols, frame_h * atlas_rows)
    if tuple(size) != expected_size:
        raise ValueError(f"target_size {size} must equal frame_size*grid {expected_size}")
    if frames > cols * atlas_rows:
        raise ValueError(f"frame_count {frames} exceeds grid capacity {cols * atlas_rows}")

    cleaned = _packed_mask_source_to_alpha(image, background_max_rgb_cutoff)
    source_w, source_h = cleaned.size
    if source_w <= 0 or source_h <= 0:
        raise ValueError(f"Invalid source sheet size: {cleaned.size}")

    source_cols = cols
    source_rows = atlas_rows
    source_frame_w = source_w / float(source_cols)
    source_frame_h = source_h / float(source_rows)
    # Image generation tools often return a fixed canvas aspect even when the
    # prompt requests a wide atlas. Split the raw grid first, then normalize
    # each visible cell into the contractual 2:1 frame.

    atlas = Image.new("RGBA", expected_size, (0, 0, 0, 0))
    pad = max(0, int(alpha_bbox_pad_px))
    for frame_index in range(frames):
        col = frame_index % source_cols
        row = frame_index // source_cols
        box = (
            int(round(col * source_frame_w)),
            int(round(row * source_frame_h)),
            int(round((col + 1) * source_frame_w)),
            int(round((row + 1) * source_frame_h)),
        )
        frame = cleaned.crop(box)
        try:
            frame = _crop_alpha_bbox_with_pad(frame, pad)
        except ValueError:
            frame = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        fitted = _fit_aspect_on_transparent_canvas(frame, (frame_w, frame_h))
        sanitized = _sanitize_timer_output(
            fitted,
            clear_outer_pixels=max(1, clear_outer_pixels),
            min_visible_alpha=min_visible_alpha,
            alpha_floor_cutoff=alpha_floor_cutoff,
            transparent_rgb_dilation=transparent_rgb_dilation,
        )
        atlas.alpha_composite(sanitized, ((frame_index % cols) * frame_w, (frame_index // cols) * frame_h))

    if clear_outer_pixels:
        arr = clear_outer_alpha(np.asarray(atlas.convert("RGBA"), dtype=np.uint8), int(clear_outer_pixels))
        atlas = Image.fromarray(arr, "RGBA")
    return atlas


def resize_nameplate_timer_image1_body_luma(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    row_index: int = 0,
    component_min_area: int = 1800,
    horizontal_inset_pct: float = 0.012,
    vertical_inset_pct: float = 0.075,
    corner_radius_pct: float = 0.45,
    luma_gamma: float = 0.92,
    luma_scale: float = 1.0,
    transparent_rgb_dilation: int = 64,
) -> Image.Image:
    """Derive timer-only body luma/alpha from Image #1 without blue backing/frame pixels."""
    target_w, target_h = int(size[0]), int(size[1])
    if target_w <= 0 or target_h <= 0:
        raise ValueError(f"Invalid target size: {size}")

    rgba = image.convert("RGBA")
    arr = np.asarray(rgba, dtype=np.uint8)
    colored = _image1_timer_color_mask(arr)
    component = _select_image1_timer_component(colored, row_index, component_min_area)
    x0, y0, x1, y1 = _pad_box(component, arr.shape[1], arr.shape[0], 10, 8)
    crop = arr[y0:y1, x0:x1, :].astype(np.float32) / 255.0
    crop_mask = colored[y0:y1, x0:x1]

    core = crop_mask.copy()
    if core.any():
        stats = connected_component_stats(core)
        if stats:
            largest = max(stats, key=lambda item: item["area"])
            core = np.zeros_like(core, dtype=bool)
            core[largest["y0"] : largest["y1"], largest["x0"] : largest["x1"]] = crop_mask[
                largest["y0"] : largest["y1"], largest["x0"] : largest["x1"]
            ]

    luma = 0.2126 * crop[:, :, 0] + 0.7152 * crop[:, :, 1] + 0.0722 * crop[:, :, 2]
    row_profile = np.zeros(crop.shape[0], dtype=np.float32)
    for y in range(crop.shape[0]):
        row_values = luma[y, core[y]]
        row_profile[y] = float(np.percentile(row_values, 72)) if row_values.size else 0.0
    if float(row_profile.max()) <= 0.001:
        row_profile[:] = 0.55
    else:
        nonzero = row_profile[row_profile > 0.001]
        floor = float(np.percentile(nonzero, 8)) if nonzero.size else 0.0
        ceil = float(np.percentile(nonzero, 96)) if nonzero.size else 1.0
        row_profile = np.clip((row_profile - floor) / max(ceil - floor, 1e-5), 0.0, 1.0)

    profile_img = Image.fromarray(np.uint8(np.clip(row_profile[:, None] * 255.0, 0, 255)), "L")
    profile = np.asarray(profile_img.resize((1, target_h), Image.Resampling.BICUBIC), dtype=np.float32)[:, 0] / 255.0

    yy = np.linspace(0.0, 1.0, target_h, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, target_w, dtype=np.float32)[None, :]
    top_rim = smoothstep_np(0.030, 0.075, yy) * (1.0 - smoothstep_np(0.085, 0.150, yy))
    upper_gloss = smoothstep_np(0.060, 0.180, yy) * (1.0 - smoothstep_np(0.240, 0.430, yy))
    core_shadow = smoothstep_np(0.300, 0.470, yy) * (1.0 - smoothstep_np(0.560, 0.760, yy))
    lower_shadow = smoothstep_np(0.540, 0.850, yy)
    lower_rim = smoothstep_np(0.760, 0.890, yy) * (1.0 - smoothstep_np(0.900, 0.985, yy))
    horizontal = 0.90 + 0.10 * (1.0 - xx)
    source_variation = (profile[:, None] - 0.5) * 0.06
    luma_out = (
        0.43
        + source_variation
        + top_rim * 0.42
        + upper_gloss * 0.26
        - core_shadow * 0.27
        - lower_shadow * 0.12
        + lower_rim * 0.10
    ) * horizontal
    luma_out = np.clip(luma_out * float(luma_scale), 0.0, 1.0) ** max(float(luma_gamma), 0.001)

    alpha = _rounded_pill_alpha(
        (target_w, target_h),
        horizontal_inset_pct=horizontal_inset_pct,
        vertical_inset_pct=vertical_inset_pct,
        corner_radius_pct=corner_radius_pct,
    )
    out = np.zeros((target_h, target_w, 4), dtype=np.uint8)
    gray = np.uint8(np.clip(luma_out * 255.0, 0, 255))
    out[:, :, 3] = np.uint8(np.clip(alpha * 255.0, 0, 255))
    safe_gray = np.minimum(gray, out[:, :, 3])
    out[:, :, 0] = safe_gray
    out[:, :, 1] = safe_gray
    out[:, :, 2] = safe_gray
    if clear_outer_pixels:
        out = clear_outer_alpha(out, int(clear_outer_pixels))
    out = dilate_transparent_rgb(out, int(transparent_rgb_dilation))
    out[out[:, :, 3] == 0, :3] = 0
    return Image.fromarray(out, "RGBA")


def resize_nameplate_timer_image1_edge_flipbook_atlas(
    image: Image.Image,
    size: tuple[int, int],
    clear_outer_pixels: int = 1,
    *,
    frame_size: tuple[int, int] = (256, 128),
    columns: int = 4,
    rows: int = 3,
    frame_count: int = 12,
    row_indices: tuple[int, ...] = (1, 2, 3, 4),
    component_min_area: int = 900,
    boundary_left_px: int = 92,
    boundary_right_px: int = 108,
    vertical_pad_px: int = 12,
    alpha_floor_cutoff: float = 0.010,
    transparent_rgb_dilation: int = 8,
    aspect_ratio_tolerance_pct: float = 8.0,
) -> Image.Image:
    """Derive a V03-style packed edge atlas from Image #1 pitted timer boundaries."""
    _ = aspect_ratio_tolerance_pct
    frame_w, frame_h = int(frame_size[0]), int(frame_size[1])
    cols, atlas_rows, frames = int(columns), int(rows), int(frame_count)
    expected_size = (frame_w * cols, frame_h * atlas_rows)
    if tuple(size) != expected_size:
        raise ValueError(f"target_size {size} must equal frame_size*grid {expected_size}")
    if frames > cols * atlas_rows:
        raise ValueError(f"frame_count {frames} exceeds grid capacity {cols * atlas_rows}")

    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    colored = _image1_timer_color_mask(arr)
    components = _image1_timer_components(colored, component_min_area)
    if not components:
        raise ValueError("Image1 timer edge extraction found no colored timer components")

    selected_rows = []
    for row_index in row_indices:
        if 0 <= int(row_index) < len(components):
            selected_rows.append(components[int(row_index)])
    if not selected_rows:
        selected_rows = components[min(1, len(components) - 1) :]
    if not selected_rows:
        selected_rows = components

    frame_masks = []
    for frame_index in range(frames):
        component = selected_rows[frame_index % len(selected_rows)]
        coverage = _extract_image1_boundary_coverage(
            arr,
            colored,
            component,
            frame_size=(frame_w, frame_h),
            boundary_left_px=int(boundary_left_px),
            boundary_right_px=int(boundary_right_px),
            vertical_pad_px=int(vertical_pad_px),
            frame_index=frame_index,
        )
        frame_masks.append(_image1_coverage_to_packed_edge(coverage, frame_index))

    atlas = np.zeros((expected_size[1], expected_size[0], 4), dtype=np.uint8)
    for frame_index, frame in enumerate(frame_masks):
        x = (frame_index % cols) * frame_w
        y = (frame_index // cols) * frame_h
        atlas[y : y + frame_h, x : x + frame_w, :] = frame

    if alpha_floor_cutoff > 0.0:
        alpha = atlas[:, :, 3].astype(np.float32) / 255.0
        atlas[alpha < float(alpha_floor_cutoff), :] = 0
    if clear_outer_pixels:
        atlas = clear_outer_alpha(atlas, int(clear_outer_pixels))
    atlas = dilate_transparent_rgb(atlas, int(transparent_rgb_dilation))
    atlas[atlas[:, :, 3] == 0, :3] = 0
    return Image.fromarray(atlas, "RGBA")


def _packed_mask_source_to_alpha(image: Image.Image, background_max_rgb_cutoff: int = 6) -> Image.Image:
    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    rgb_max = arr[:, :, :3].max(axis=2)
    background = rgb_max <= int(background_max_rgb_cutoff)
    arr[background, :3] = 0
    arr[:, :, 3] = np.where(background, 0, rgb_max).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def smoothstep_np(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _image1_timer_color_mask(arr: np.ndarray) -> np.ndarray:
    rgb = arr[:, :, :3].astype(np.float32)
    alpha = arr[:, :, 3].astype(np.float32)
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation = maximum - minimum
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    blue_backing = (blue > 52) & (blue > green * 1.08) & (blue > red * 1.22)
    dark_frame = maximum < 42
    colored_state = (
        (maximum > 82)
        & (saturation > 34)
        & (alpha > 8)
        & ~blue_backing
        & ~dark_frame
        & (
            ((green > red * 1.08) & (green > blue * 1.15))
            | ((red > 118) & (green > 52) & (blue < 120))
            | ((red > 118) & (green < 86) & (blue < 96))
        )
    )
    return colored_state


def _image1_timer_components(mask: np.ndarray, component_min_area: int) -> list[dict[str, int]]:
    components = [
        item
        for item in connected_component_stats(mask)
        if int(item["area"]) >= int(component_min_area)
        and (item["x1"] - item["x0"]) >= 24
        and (item["y1"] - item["y0"]) >= 16
    ]
    components.sort(key=lambda item: (item["y0"] + item["y1"], -item["area"]))
    filtered: list[dict[str, int]] = []
    for item in components:
        cy = (item["y0"] + item["y1"]) * 0.5
        if filtered and abs(cy - (filtered[-1]["y0"] + filtered[-1]["y1"]) * 0.5) < 24:
            if item["area"] > filtered[-1]["area"]:
                filtered[-1] = item
            continue
        filtered.append(item)
    return filtered


def _select_image1_timer_component(mask: np.ndarray, row_index: int, component_min_area: int) -> dict[str, int]:
    components = _image1_timer_components(mask, component_min_area)
    if not components:
        raise ValueError("Image1 timer body extraction found no colored timer component")
    index = max(0, min(int(row_index), len(components) - 1))
    return components[index]


def _pad_box(
    component: dict[str, int],
    width: int,
    height: int,
    pad_x: int,
    pad_y: int,
) -> tuple[int, int, int, int]:
    return (
        max(0, int(component["x0"]) - int(pad_x)),
        max(0, int(component["y0"]) - int(pad_y)),
        min(int(width), int(component["x1"]) + int(pad_x)),
        min(int(height), int(component["y1"]) + int(pad_y)),
    )


def _rounded_pill_alpha(
    size: tuple[int, int],
    *,
    horizontal_inset_pct: float,
    vertical_inset_pct: float,
    corner_radius_pct: float,
) -> np.ndarray:
    width, height = int(size[0]), int(size[1])
    scale = 3
    canvas = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(canvas)
    inset_x = max(1.0, width * float(horizontal_inset_pct)) * scale
    inset_y = max(1.0, height * float(vertical_inset_pct)) * scale
    radius = max(2.0, (height - inset_y * 2.0) * float(corner_radius_pct)) * scale
    draw.rounded_rectangle(
        (inset_x, inset_y, width * scale - inset_x, height * scale - inset_y),
        radius=radius,
        fill=255,
    )
    canvas = canvas.filter(ImageFilter.GaussianBlur(0.55 * scale))
    canvas = canvas.resize((width, height), Image.Resampling.LANCZOS)
    return np.asarray(canvas, dtype=np.float32) / 255.0


def _extract_image1_boundary_coverage(
    arr: np.ndarray,
    colored: np.ndarray,
    component: dict[str, int],
    *,
    frame_size: tuple[int, int],
    boundary_left_px: int,
    boundary_right_px: int,
    vertical_pad_px: int,
    frame_index: int,
) -> np.ndarray:
    height, width = colored.shape
    y0 = max(0, int(component["y0"]) - int(vertical_pad_px))
    y1 = min(height, int(component["y1"]) + int(vertical_pad_px))
    row_mask = colored[y0:y1, :]
    density = row_mask.mean(axis=0)
    dense = density > max(0.08, float(np.percentile(density[density > 0], 64)) if (density > 0).any() else 0.08)
    xs = np.where(dense)[0]
    boundary = int(xs.max()) if xs.size else int(component["x1"])
    left = max(0, boundary - max(8, int(boundary_left_px)))
    right = min(width, boundary + max(8, int(boundary_right_px)))
    if right <= left + 8:
        left, right = max(0, int(component["x0"]) - 16), min(width, int(component["x1"]) + 16)

    crop = arr[y0:y1, left:right, :]
    crop_mask = colored[y0:y1, left:right]
    rgb = crop[:, :, :3].astype(np.float32)
    luma = (0.2126 * rgb[:, :, 0]) + (0.7152 * rgb[:, :, 1]) + (0.0722 * rgb[:, :, 2])
    saturation = rgb.max(axis=2) - rgb.min(axis=2)
    coverage = np.where(crop_mask, np.clip((luma - 28.0) / 210.0, 0.0, 1.0), 0.0)
    coverage = np.maximum(coverage, np.where(crop_mask, np.clip(saturation / 190.0, 0.0, 1.0), 0.0))

    cov_img = Image.fromarray(np.uint8(np.clip(coverage, 0.0, 1.0) * 255.0), "L")
    cov_img = cov_img.resize(frame_size, Image.Resampling.LANCZOS)
    cov = np.asarray(cov_img, dtype=np.float32) / 255.0
    if frame_index:
        shift_x = int((frame_index % 5) - 2)
        shift_y = int(((frame_index * 2) % 5) - 2)
        cov = _shift_float_mask(cov, shift_x, shift_y)
    return np.clip(cov, 0.0, 1.0)


def _image1_coverage_to_packed_edge(coverage: np.ndarray, frame_index: int) -> np.ndarray:
    height, width = coverage.shape
    yy = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    vertical_gate = smoothstep_np(0.06, 0.16, yy) * (1.0 - smoothstep_np(0.84, 0.96, yy))

    blur_cov = np.asarray(
        Image.fromarray(np.uint8(np.clip(coverage, 0.0, 1.0) * 255.0), "L").filter(ImageFilter.GaussianBlur(1.0)),
        dtype=np.float32,
    ) / 255.0
    rng = np.random.default_rng(31013 + int(frame_index) * 97)
    noise = rng.random((height, width), dtype=np.float32)
    coarse = Image.fromarray(np.uint8(noise * 255.0), "L").resize(
        (max(1, width // 5), max(1, height // 5)),
        Image.Resampling.BILINEAR,
    ).resize((width, height), Image.Resampling.BILINEAR)
    coarse_noise = np.asarray(coarse, dtype=np.float32) / 255.0

    threshold = max(0.10, float(np.percentile(blur_cov[blur_cov > 0.01], 38)) if (blur_cov > 0.01).any() else 0.10)
    strong = blur_cov > threshold
    gap_limit = max(2, int(round(width * 0.020)))
    min_break_x = int(round(width * 0.22))
    boundary = np.full(height, width * 0.46, dtype=np.float32)
    for y in range(height):
        xs = np.where(strong[y])[0]
        if xs.size == 0:
            continue
        start = int(xs[0])
        last = start
        gap = 0
        for x in range(start, width):
            if strong[y, x]:
                last = x
                gap = 0
            else:
                gap += 1
                if gap >= gap_limit and x > min_break_x:
                    break
        boundary[y] = float(last)

    boundary_img = Image.fromarray(np.uint8(np.clip(boundary / max(1, width - 1), 0.0, 1.0) * 255.0)[:, None], "L")
    boundary_img = boundary_img.filter(ImageFilter.GaussianBlur(max(0.35, height / 96.0)))
    boundary = np.asarray(boundary_img, dtype=np.float32)[:, 0] / 255.0 * float(max(1, width - 1))
    if frame_index:
        boundary += float((frame_index % 5) - 2) * max(0.6, width / 256.0)
    boundary += width * 0.075
    boundary = np.clip(boundary, width * 0.38, width * 0.60)

    x_grid = np.arange(width, dtype=np.float32)[None, :]
    dist = x_grid - boundary[:, None]
    scale = max(0.25, width / 256.0)
    face_band = smoothstep_np(-34.0 * scale, -14.0 * scale, dist) * (
        1.0 - smoothstep_np(14.0 * scale, 36.0 * scale, dist)
    )
    cut_band = smoothstep_np(-42.0 * scale, -20.0 * scale, dist) * (
        1.0 - smoothstep_np(0.0, 20.0 * scale, dist)
    )
    particle_band = smoothstep_np(3.0 * scale, 12.0 * scale, dist) * (
        1.0 - smoothstep_np(52.0 * scale, 78.0 * scale, dist)
    )

    support = smoothstep_np(0.035, 0.145, blur_cov)
    fracture = np.clip((coverage * 1.08 + blur_cov * 0.52) * face_band * vertical_gate * (0.92 + coarse_noise * 0.35), 0.0, 1.0)
    face_boost_support = 0.35 + support * 0.65
    face_boost = face_boost_support * smoothstep_np(-20.0 * scale, -5.0 * scale, dist) * (
        1.0 - smoothstep_np(6.0 * scale, 24.0 * scale, dist)
    ) * vertical_gate * (0.38 + coarse_noise * 0.22)
    fracture = np.maximum(fracture, np.clip(face_boost, 0.0, 1.0))
    cut = np.clip((1.0 - blur_cov * 0.46) * support * cut_band * vertical_gate * (0.30 + coarse_noise * 0.82), 0.0, 1.0)
    particle = np.clip(coverage * particle_band * vertical_gate * 1.60, 0.0, 1.0)

    dot_count = 34
    for _ in range(dot_count):
        cy = int(rng.integers(int(height * 0.14), max(int(height * 0.86), int(height * 0.14) + 1)))
        cx = int(round(float(boundary[cy]) + float(rng.uniform(4.0 * scale, 56.0 * scale))))
        if cx < 0 or cx >= width:
            continue
        radius = int(rng.integers(1, max(3, int(round(4 * scale)) + 1)))
        y0 = max(0, cy - radius)
        y1 = min(height, cy + radius + 1)
        x0 = max(0, cx - radius)
        x1 = min(width, cx + radius + 1)
        for y in range(y0, y1):
            for x in range(x0, x1):
                if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius:
                    particle[y, x] = max(particle[y, x], float(rng.uniform(0.44, 0.95)))

    alpha = np.maximum.reduce([cut * 0.52, fracture * 0.86, particle])
    frame = np.zeros((height, width, 4), dtype=np.uint8)
    frame[:, :, 0] = np.uint8(np.clip(cut * 255.0, 0, 255))
    frame[:, :, 1] = np.uint8(np.clip(particle * 255.0, 0, 255))
    frame[:, :, 2] = np.uint8(np.clip(fracture * 255.0, 0, 255))
    frame[:, :, 3] = np.uint8(np.clip(alpha * 255.0, 0, 255))
    frame[frame[:, :, 3] == 0, :3] = 0
    return frame


def _shift_float_mask(mask: np.ndarray, shift_x: int, shift_y: int) -> np.ndarray:
    out = np.zeros_like(mask)
    height, width = mask.shape
    src_x0 = max(0, -shift_x)
    src_x1 = min(width, width - shift_x)
    dst_x0 = max(0, shift_x)
    dst_x1 = min(width, width + shift_x)
    src_y0 = max(0, -shift_y)
    src_y1 = min(height, height - shift_y)
    dst_y0 = max(0, shift_y)
    dst_y1 = min(height, height + shift_y)
    if src_x1 > src_x0 and src_y1 > src_y0:
        out[dst_y0:dst_y1, dst_x0:dst_x1] = mask[src_y0:src_y1, src_x0:src_x1]
    return out


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
    component_metrics = alpha_component_metrics(arr)
    return {
        "size": [image.width, image.height],
        "alpha_bbox": list(alpha_bbox(image)) if int((alpha > 8).sum()) else None,
        "opaque_or_translucent_pixels": int((alpha > 0).sum()),
        "fully_opaque_pixels": int((alpha == 255).sum()),
        "min_alpha": int(alpha.min()) if alpha.size else 0,
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
        **component_metrics,
    }


def timer_boundary_diagnostics(image: Image.Image, config: dict[str, Any] | None = None) -> dict[str, object]:
    """Measure timer-specific edge failure modes.

    The generic alpha/chroma diagnostics intentionally do not know about HUD
    timer semantics. This optional check catches the dark right-edge band that
    can appear when edge particles are extracted too broadly, and can also
    verify that a particle/breakup texture is centered near the intended active
    fill boundary.
    """
    cfg = config if isinstance(config, dict) else {}
    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    height, width = arr.shape[:2]
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.float32)
    luma = (0.2126 * rgb[:, :, 0]) + (0.7152 * rgb[:, :, 1]) + (0.0722 * rgb[:, :, 2])
    rgb_max = rgb.max(axis=2)

    alpha_threshold = int(cfg.get("alpha_threshold", 8))
    dark_alpha_min = int(cfg.get("dark_alpha_min", alpha_threshold))
    dark_luma_max = float(cfg.get("dark_luma_max", 44.0))
    dark_rgb_max = float(cfg.get("dark_rgb_max", 56.0))
    default_band_width = max(1, int(round(width * 0.12)))
    band_width = max(1, min(width, int(cfg.get("dark_right_band_width_px", default_band_width))))
    band_mask = np.zeros(alpha.shape, dtype=bool)
    band_mask[:, width - band_width : width] = True
    dark_band = (
        band_mask
        & (alpha > dark_alpha_min)
        & (luma <= dark_luma_max)
        & (rgb_max <= dark_rgb_max)
    )

    particle_mask = alpha > alpha_threshold
    bbox: list[int] | None = None
    bbox_center_x: float | None = None
    if particle_mask.any():
        ys, xs = np.where(particle_mask)
        x0 = int(xs.min())
        y0 = int(ys.min())
        x1 = int(xs.max()) + 1
        y1 = int(ys.max()) + 1
        bbox = [x0, y0, x1, y1]
        bbox_center_x = round((x0 + x1 - 1) / 2.0, 3)

    expected_boundary_x = None
    if "expected_boundary_x_px" in cfg:
        expected_boundary_x = float(cfg["expected_boundary_x_px"])
    elif "expected_boundary_x_pct" in cfg:
        expected_boundary_x = float(cfg["expected_boundary_x_pct"]) * float(max(0, width - 1))

    boundary_delta = None
    within_tolerance = None
    if expected_boundary_x is not None and bbox_center_x is not None:
        tolerance_px = float(cfg.get("boundary_tolerance_px", 4.0))
        boundary_delta = round(bbox_center_x - expected_boundary_x, 3)
        within_tolerance = abs(boundary_delta) <= tolerance_px

    result: dict[str, object] = {
        "timer_dark_right_edge_band_pixels": int(dark_band.sum()),
        "timer_dark_right_edge_band_width_px": int(band_width),
        "timer_edge_particle_bbox": bbox,
        "timer_edge_particle_bbox_center_x": bbox_center_x,
        "timer_edge_expected_boundary_x": round(expected_boundary_x, 3) if expected_boundary_x is not None else None,
        "timer_edge_boundary_delta_px": boundary_delta,
        "timer_edge_boundary_within_tolerance": within_tolerance,
    }

    if bool(cfg.get("require_boundary_face", False)):
        boundary_x = expected_boundary_x
        if boundary_x is None:
            boundary_x = float(width - 1) * float(cfg.get("boundary_face_x_pct", 0.50))
        face_alpha_min = int(cfg.get("boundary_face_alpha_min", 96))
        left_width = float(cfg.get("boundary_face_left_width_px", max(8.0, width * 0.22)))
        right_width = float(cfg.get("boundary_face_right_width_px", max(3.0, width * 0.06)))
        min_left_pixels = int(cfg.get("boundary_face_min_left_pixels", max(8, round(width * height * 0.018))))
        min_window_pixels = int(cfg.get("boundary_face_min_window_pixels", max(12, round(width * height * 0.024))))

        xx = np.arange(width, dtype=np.float32)[None, :]
        strong = alpha >= face_alpha_min
        left_face = strong & (xx >= boundary_x - left_width) & (xx < boundary_x)
        boundary_face = strong & (xx >= boundary_x - left_width) & (xx <= boundary_x + right_width)
        left_pixels = int(left_face.sum())
        window_pixels = int(boundary_face.sum())
        has_boundary_face = left_pixels >= min_left_pixels and window_pixels >= min_window_pixels
        result.update(
            {
                "timer_edge_boundary_face_required": True,
                "timer_edge_boundary_face_pixels": window_pixels,
                "timer_edge_left_face_pixels": left_pixels,
                "timer_edge_boundary_face_min_pixels": min_window_pixels,
                "timer_edge_left_face_min_pixels": min_left_pixels,
                "timer_edge_has_boundary_face": bool(has_boundary_face),
            }
        )

    return result

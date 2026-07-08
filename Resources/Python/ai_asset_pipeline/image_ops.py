from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


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

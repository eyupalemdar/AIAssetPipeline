from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .image_ops import (
    chroma_key_mask,
    despill_visible_magenta,
    hidden_saturated_chroma_mask,
    hidden_saturated_rgb_artifact_mask,
    low_alpha_saturated_chroma_fringe_mask,
    low_alpha_saturated_rgb_artifact_mask,
    neutralize_hidden_artifacts,
    resize_premultiplied,
    visible_magenta_fringe_mask,
)

import numpy as np


def make_text(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], size: int, fill: tuple[int, int, int, int]) -> None:
    try:
        font = ImageFont.truetype("arial.ttf", size)
    except Exception:
        font = None
    draw.text(xy, text, font=font, fill=fill)


def save_checker(image: Image.Image, dest: Path, step: int = 16) -> None:
    checker = Image.new("RGBA", image.size, (24, 26, 30, 255))
    draw = ImageDraw.Draw(checker)
    for y in range(0, image.height, step):
        for x in range(0, image.width, step):
            if ((x // step) + (y // step)) % 2 == 0:
                draw.rectangle([x, y, x + step - 1, y + step - 1], fill=(48, 52, 60, 255))
    Image.alpha_composite(checker, image).convert("RGB").save(dest)


def save_alpha_mask(image: Image.Image, dest: Path) -> None:
    alpha = image.convert("RGBA").getchannel("A")
    alpha.convert("RGB").save(dest)


def save_matte_issue_overlay(image: Image.Image, dest: Path) -> None:
    rgba = image.convert("RGBA")
    arr = np.asarray(rgba, dtype=np.uint8)
    alpha = arr[:, :, 3]
    visible_chroma = (alpha > 8) & chroma_key_mask(arr[:, :, :3])
    visible_magenta = visible_magenta_fringe_mask(arr)
    low_alpha_chroma = low_alpha_saturated_chroma_fringe_mask(arr)
    hidden_chroma = hidden_saturated_chroma_mask(arr)
    low_alpha_rgb = low_alpha_saturated_rgb_artifact_mask(arr)
    hidden_rgb = hidden_saturated_rgb_artifact_mask(arr)

    base = Image.new("RGBA", rgba.size, (20, 22, 26, 255))
    draw = ImageDraw.Draw(base)
    step = 16
    for y in range(0, rgba.height, step):
        for x in range(0, rgba.width, step):
            if ((x // step) + (y // step)) % 2 == 0:
                draw.rectangle([x, y, x + step - 1, y + step - 1], fill=(44, 48, 56, 255))
    base = Image.alpha_composite(base, rgba)
    overlay = np.asarray(base.convert("RGBA"), dtype=np.uint8).copy()
    problem = visible_chroma | visible_magenta | low_alpha_chroma | hidden_chroma | low_alpha_rgb | hidden_rgb
    overlay[problem] = [255, 40, 220, 255]
    Image.fromarray(overlay, "RGBA").convert("RGB").save(dest)


def clean_review_matte(image: Image.Image) -> Image.Image:
    arr = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    arr = despill_visible_magenta(arr)
    arr = neutralize_hidden_artifacts(arr)
    return Image.fromarray(arr, "RGBA")


def load_runtime(root: Path, item: dict[str, Any], draw_size: tuple[int, int]) -> Image.Image:
    image = Image.open(root / str(item["runtime_file"])).convert("RGBA")
    if image.size == draw_size:
        return image
    return resize_premultiplied(image, draw_size, 0)


def output_by_id(outputs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["component_id"]): item for item in outputs}


def compose_review(root: Path, outputs: list[dict[str, Any]], review_spec: dict[str, Any]) -> Image.Image:
    size = tuple(int(v) for v in review_spec["size"])
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    by_id = output_by_id(outputs)
    for component_id in review_spec.get("component_ids", []):
        item = by_id[str(component_id)]
        x, y, w, h = [int(v) for v in item["draw_rect"]]
        canvas.alpha_composite(load_runtime(root, item, (w, h)), (x, y))

    for text in review_spec.get("text_overlays", []):
        fill = tuple(int(v) for v in text.get("fill", [238, 220, 180, 255]))
        make_text(
            ImageDraw.Draw(canvas),
            str(text["text"]),
            (int(text["x"]), int(text["y"])),
            int(text.get("size", 16)),
            fill,  # type: ignore[arg-type]
        )
    return canvas


def make_contact_sheet(root: Path, outputs: list[dict[str, Any]], dest: Path) -> None:
    tiles: list[Image.Image] = []
    for item in outputs:
        image = Image.open(root / str(item["runtime_file"])).convert("RGBA")
        scale = min(150 / image.width, 120 / image.height, 1.0)
        if scale < 1.0:
            image = resize_premultiplied(
                image,
                (
                    max(1, int(round(image.width * scale))),
                    max(1, int(round(image.height * scale))),
                ),
                0,
            )
        tile = Image.new("RGBA", (210, 174), (18, 20, 24, 255))
        tile.alpha_composite(image, ((210 - image.width) // 2, 12))
        draw = ImageDraw.Draw(tile)
        draw.text((8, 132), str(item["component_id"])[:28], fill=(235, 236, 240, 255))
        draw.text((8, 150), f"{item['target_size'][0]}x{item['target_size'][1]}", fill=(160, 168, 178, 255))
        tiles.append(tile)
    columns = 4
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * 210, rows * 174), (12, 14, 18, 255))
    for index, tile in enumerate(tiles):
        sheet.alpha_composite(tile, ((index % columns) * 210, (index // columns) * 174))
    sheet.save(dest)


def gaussian_glow_from_alpha(base: Image.Image, tint: list[int], blur_radius: int, alpha_scale: float, alpha_cap: int) -> Image.Image:
    alpha = base.getchannel("A")
    glow_alpha = alpha.filter(ImageFilter.GaussianBlur(blur_radius)).point(lambda p: min(alpha_cap, int(p * alpha_scale)))
    glow = Image.new("RGBA", base.size, tuple(tint))
    glow.putalpha(glow_alpha)
    return glow

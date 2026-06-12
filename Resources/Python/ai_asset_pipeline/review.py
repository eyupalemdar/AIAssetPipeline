from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont


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


def load_runtime(root: Path, item: dict[str, Any], draw_size: tuple[int, int]) -> Image.Image:
    image = Image.open(root / str(item["runtime_file"])).convert("RGBA")
    return image.resize(draw_size, Image.Resampling.LANCZOS)


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
        image.thumbnail((150, 120), Image.Resampling.LANCZOS)
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


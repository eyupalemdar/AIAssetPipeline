from __future__ import annotations

from typing import Any

from PIL import Image

from .image_ops import alpha_bbox, chroma_to_alpha, chroma_to_alpha_strict_hsv, component_boxes, crop_box


Box = tuple[int, int, int, int]


def _box_key(sort: str):
    def key(box: Box):
        x0, y0, x1, y1 = box
        cx = (x0 + x1) * 0.5
        cy = (y0 + y1) * 0.5
        area = (x1 - x0) * (y1 - y0)
        if sort == "x":
            return (cx, cy, -area)
        if sort == "y":
            return (cy, cx, -area)
        if sort == "xy":
            return (x0, y0, -area)
        if sort == "area_desc":
            return (-area, y0, x0)
        return (y0, x0, -area)

    return key


def select_crop(source: Image.Image, selector: dict[str, Any]) -> tuple[Image.Image, dict[str, object]]:
    selector_type = selector.get("type", "alpha_largest")
    chroma_key_mode = str(selector.get("chroma_key_mode", "standard"))

    if selector_type == "full_image_raw":
        rgba = source.convert("RGBA")
        return rgba, {"selector": selector_type, "chroma_key_mode": "raw", "selected_box": [0, 0, rgba.width, rgba.height]}

    if chroma_key_mode == "standard":
        cleaned = chroma_to_alpha(source)
    elif chroma_key_mode == "strict_hsv":
        cleaned = chroma_to_alpha_strict_hsv(source)
    else:
        raise ValueError(f"Unsupported chroma_key_mode: {chroma_key_mode}")

    if selector_type == "full_image":
        return cleaned, {"selector": selector_type, "chroma_key_mode": chroma_key_mode, "selected_box": [0, 0, cleaned.width, cleaned.height]}

    if selector_type == "bbox":
        raw_box = selector.get("box")
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            raise ValueError("bbox selector requires box=[x,y,w,h]")
        x, y, w, h = [int(v) for v in raw_box]
        box = (x, y, x + w, y + h)
        return cleaned.crop(box), {"selector": selector_type, "chroma_key_mode": chroma_key_mode, "selected_box": list(box)}

    boxes = component_boxes(cleaned, int(selector.get("min_area", 1200)))
    if not boxes:
        raise RuntimeError("No alpha components detected")

    if selector_type == "alpha_largest":
        box = max(boxes, key=lambda item: (item[2] - item[0]) * (item[3] - item[1]))
        return crop_box(cleaned, box, int(selector.get("pad", 24))), {
            "selector": selector_type,
            "chroma_key_mode": chroma_key_mode,
            "detected_count": len(boxes),
            "selected_box": list(box),
        }

    if selector_type == "alpha_bbox":
        box = alpha_bbox(cleaned)
        return crop_box(cleaned, box, int(selector.get("pad", 0))), {
            "selector": selector_type,
            "chroma_key_mode": chroma_key_mode,
            "detected_count": len(boxes),
            "selected_box": list(box),
        }

    if selector_type == "alpha_components_sorted":
        ordered = sorted(boxes, key=_box_key(str(selector.get("sort", "yx"))))
        slice_range = selector.get("slice")
        if slice_range is not None:
            if not isinstance(slice_range, list) or len(slice_range) != 2:
                raise ValueError("alpha_components_sorted slice must be [start,end]")
            ordered = ordered[int(slice_range[0]) : int(slice_range[1])]
        if "slice_sort" in selector:
            ordered = sorted(ordered, key=_box_key(str(selector["slice_sort"])))
        min_count = int(selector.get("min_count", 1))
        if len(ordered) < min_count:
            raise RuntimeError(f"Expected at least {min_count} selectable components, found {len(ordered)}")
        index = int(selector.get("index", 0))
        try:
            box = ordered[index]
        except IndexError as exc:
            raise RuntimeError(f"Component index {index} is out of range for {len(ordered)} boxes") from exc
        return crop_box(cleaned, box, int(selector.get("pad", 24))), {
            "selector": selector_type,
            "chroma_key_mode": chroma_key_mode,
            "detected_count": len(boxes),
            "selected_count": len(ordered),
            "selected_index": index,
            "selected_box": list(box),
        }

    raise ValueError(f"Unsupported selector type: {selector_type}")

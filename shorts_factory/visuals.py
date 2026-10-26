"""Provider-generated photos, Claude-generated SVG drawings, and offline demo cards."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .providers import anthropic_text, openai_image


_ALLOWED_TAGS = {"svg", "g", "rect", "circle", "ellipse", "path", "line", "polyline", "polygon"}
_ALLOWED_ATTRS = {
    "xmlns", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2",
    "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "stroke", "stroke-width",
    "stroke-linejoin", "stroke-linecap", "stroke-dasharray", "opacity", "fill-opacity", "stroke-opacity",
    "transform", "viewbox", "vector-effect", "fill-rule", "clip-rule",
}


def validate_svg(raw: str) -> str:
    """Whitelist vector primitives, reject scripts, CSS, remote resources and attributes."""
    svg = raw.strip()
    if svg.startswith("```"):
        svg = re.sub(r"^```(?:svg|xml)?\s*|\s*```$", "", svg, flags=re.I).strip()
    if len(svg) > 60000 or "<!" in svg:
        raise ValueError("SVG too large or includes forbidden declarations")
    root = ET.fromstring(svg)
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("Root must be SVG")
    count = 0
    for element in root.iter():
        count += 1
        if count > 250:
            raise ValueError("SVG has too many elements")
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in _ALLOWED_TAGS:
            raise ValueError(f"Forbidden SVG element: {tag}")
        for key, value in element.attrib.items():
            local_key = key.rsplit("}", 1)[-1]
            if local_key not in _ALLOWED_ATTRS or "url(" in value.lower() or "http" in value.lower():
                raise ValueError(f"Forbidden SVG attribute: {local_key}")
    return svg


def claude_svg(prompt: str, output: Path, *, style: str) -> None:
    import cairosvg

    answer = anthropic_text(
        "Make ONE attractive, original, precisely structured flat-vector illustration for a vertical short. "
        "Output ONLY safe SVG markup, without fences or commentary. "
        "Use <svg xmlns='http://www.w3.org/2000/svg' width='1024' height='1536' viewBox='0 0 1024 1536'>. "
        "Only shapes: svg, g, rect, circle, ellipse, path, line, polyline, polygon. "
        "Only inline shape color attributes (fill, stroke, etc.). No text or fonts, no CSS, "
        "scripts, external references, filters, masks, images, gradients, declarations or entities. "
        "Make a bold visual readable on a phone, with simple appealing geometric style. "
        f"ART STYLE: {style}. VISUAL SUBJECT: {prompt}", max_tokens=6000,
    )
    svg = validate_svg(answer)
    svg_path = output.with_suffix(".svg")
    svg_path.write_text(svg, encoding="utf-8")
    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(output), output_width=1024, output_height=1536)


def card_image(prompt: str, output: Path) -> None:
    """No-network deterministic vector-style card for testing the local renderer."""
    import hashlib
    seed = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
    colors = [(40, 63, 116), (32, 102, 102), (105, 49, 128), (112, 65, 57)]
    color = colors[seed % len(colors)]
    canvas = Image.new("RGB", (768, 1152), color)
    draw = ImageDraw.Draw(canvas)
    for i in range(12):
        a = (seed >> (i % 12)) % 400
        x = (a * (i + 1) + i * 89) % 760
        y = (a * (i + 4) + i * 120) % 1130
        radius = 20 + ((seed >> (i % 16)) % 90)
        shade = tuple(min(255, int(c + 28 + (i % 4) * 16)) for c in color)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=shade, width=8)
    draw.rounded_rectangle((90, 470, 675, 700), radius=60, fill=(255, 255, 255))
    draw.ellipse((250, 500, 510, 665), fill=(245, 197, 80))
    draw.polygon([(350, 538), (350, 627), (430, 580)], fill=color)
    canvas.save(output)


def create_visual(provider: str, prompt: str, output: Path, *, style: str) -> None:
    if provider == "openai":
        openai_image(prompt, output, style=style)
    elif provider == "claude-svg":
        claude_svg(prompt, output, style=style)
    elif provider == "cards":
        card_image(prompt, output)
    else:
        raise ValueError(f"Unknown image provider: {provider}")
    with Image.open(output) as image:
        image.verify()

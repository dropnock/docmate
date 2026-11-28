"""Make every logo file the app, the manuals and the sign-in page use from
the source artwork.

    python manuals/tools/make_logo_assets.py

Source: manuals/brand/source/docmate-logo-source.svg. Its wordmark is live
<text> in Inter, which an <img> can't load (it falls back to Arial), so this
outlines the text with the bundled Inter ExtraBold (the weight a browser
picks for the source's 750), keeping its letter-spacing and Inter's kerning,
and sets "Mate" directly after "Doc" so they don't collide.
It also drops the white background box and crops to the artwork. Writes:

    manuals/brand/logo.svg, logo.png                 manuals (PNG for Word)
    frontend/src/shared/assets/docmate-logo.svg      app header
    frontend/src/shared/assets/docmate-icon.svg/.png favicon (the document mark)
    keycloak/themes/docmate/login/resources/img/     sign-in page logo + favicon.ico

Needs: fonttools, brotli, uharfbuzz, cairosvg, Pillow (dev only; the outputs
are committed).
"""
import copy
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import cairosvg
import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "manuals/brand/source/docmate-logo-source.svg"
FONT = ROOT / "manuals/brand/fonts/inter-latin-800-normal.woff2"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
q = lambda tag: f"{{{SVG_NS}}}{tag}"

# The document mark's extent in the source's coordinates: the outer path
# (x 38..238, y 8..244, stroke 18) inside <g transform="translate(54 40)">.
MARK_BOX = (54 + 38 - 9, 40 + 8 - 9, 54 + 238 + 9, 40 + 244 + 9)
PAD = 6


def load_font():
    tt = TTFont(str(FONT))
    tt.flavor = None
    buf = io.BytesIO()
    tt.save(buf)
    data = buf.getvalue()
    return TTFont(io.BytesIO(data)), hb.Font(hb.Face(data))


def outline_text(el, tt, hbfont, start_x=None):
    """A <text> element → (<path> element, bounds, end x). start_x overrides
    the element's x (to continue the previous run)."""
    size = float(el.get("font-size"))
    spacing = float(el.get("letter-spacing", "0"))
    x, y = (start_x if start_x is not None else float(el.get("x"))), float(el.get("y"))
    upem = tt["head"].unitsPerEm
    scale = size / upem
    buf = hb.Buffer()
    buf.add_str(el.text)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf, {"kern": True, "liga": True})
    glyphset = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    pen = SVGPathPen(glyphset)
    bounds = BoundsPen(glyphset)
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = order[info.codepoint]
        t = (scale, 0, 0, -scale, x + pos.x_offset * scale, y - pos.y_offset * scale)
        glyphset[name].draw(TransformPen(pen, t))
        glyphset[name].draw(TransformPen(bounds, t))
        x += pos.x_advance * scale + spacing
    path = ET.Element(q("path"), {"d": pen.getCommands(), "fill": el.get("fill")})
    return path, bounds.bounds, x


def build_svgs():
    tree = ET.parse(SRC)
    root = tree.getroot()
    tt, hbfont = load_font()
    # Drop the full-size white background box.
    for rect in root.findall(q("rect")):
        if rect.get("fill", "").lower() in ("#fff", "#ffffff", "white"):
            root.remove(rect)
    x0, y0, x1, y1 = MARK_BOX
    # "Doc" and "Mate" are separate <text>s at fixed x positions chosen for a
    # narrower rendering; with Inter ExtraBold "Doc" would run into the "M".
    # Set each later word straight after the previous one on the same
    # baseline, as one run of text would be.
    run_end = None
    for i, el in enumerate(list(root)):
        if el.tag == q("text"):
            path, (bx0, by0, bx1, by1), run_end = outline_text(el, tt, hbfont, run_end)
            root.remove(el)
            root.insert(i, path)
            x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
    for k in ("width", "height"):
        root.attrib.pop(k, None)

    full = copy.deepcopy(root)
    full.set("viewBox", f"{x0 - PAD:.0f} {y0 - PAD:.0f} {x1 - x0 + 2 * PAD:.0f} {y1 - y0 + 2 * PAD:.0f}")

    icon = copy.deepcopy(root)
    for el in list(icon):
        if el.tag == q("path"):  # the outlined wordmark
            icon.remove(el)
    mx0, my0, mx1, my1 = MARK_BOX
    side = max(mx1 - mx0, my1 - my0) + 2 * 4
    cx, cy = (mx0 + mx1) / 2, (my0 + my1) / 2
    icon.set("viewBox", f"{cx - side / 2:.0f} {cy - side / 2:.0f} {side:.0f} {side:.0f}")
    desc = icon.find(q("desc"))
    if desc is not None:
        icon.remove(desc)

    def text(el):
        s = ET.tostring(el, encoding="unicode")
        s = re.sub(r">\s+<", "><", s)  # compact; paths are long anyway
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + s + "\n"

    return text(full), text(icon)


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.write_bytes if isinstance(data, bytes) else path.write_text)(data)
    print(path.relative_to(ROOT), path.stat().st_size)


def png(svg: str, width: int) -> bytes:
    return cairosvg.svg2png(bytestring=svg.encode(), output_width=width)


def main():
    full, icon = build_svgs()
    write(ROOT / "manuals/brand/logo.svg", full)
    write(ROOT / "manuals/brand/logo.png", png(full, 1200))
    write(ROOT / "frontend/src/shared/assets/docmate-logo.svg", full)
    write(ROOT / "frontend/src/shared/assets/docmate-icon.svg", icon)
    write(ROOT / "frontend/src/shared/assets/docmate-icon.png", png(icon, 192))
    kc = ROOT / "keycloak/themes/docmate/login/resources/img"
    write(kc / "docmate-logo.svg", full)
    ico = io.BytesIO()
    Image.open(io.BytesIO(png(icon, 256))).save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    write(kc / "favicon.ico", ico.getvalue())


if __name__ == "__main__":
    main()

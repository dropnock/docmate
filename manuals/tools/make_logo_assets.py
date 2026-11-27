"""Make the logo files the app and manuals use from the original artwork.

    python manuals/tools/make_logo_assets.py [original.png]

The original (brand/source/docmate-logo-original.png) is a raster on white.
This trims it and makes the white background transparent, including inside
the wordmark's letters; the white inside the document mark stays. Writes:

    manuals/brand/logo.png                       full lockup for the manuals
    frontend/src/shared/assets/docmate-logo.png  full lockup for the app header
    frontend/src/shared/assets/docmate-icon.png  the document mark (favicon)
"""
import sys
from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "manuals/brand/source/docmate-logo-original.png"
WHITE = 238  # a pixel with every channel at or above this counts as background


def transparent_outside(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    is_bg = lambda x, y: min(px[x, y][:3]) >= WHITE
    outside = bytearray(w * h)
    q = deque((x, y) for x in range(w) for y in (0, h - 1))
    q.extend((x, y) for y in range(h) for x in (0, w - 1))
    while q:
        x, y = q.popleft()
        i = y * w + x
        if outside[i] or not is_bg(x, y):
            continue
        outside[i] = 1
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not outside[ny * w + nx]:
                q.append((nx, ny))
    # The wordmark starts at the first all-background column after the mark;
    # its letters' white counters (D, o, a, e) aren't reached by the flood, so
    # the whole wordmark is un-matted from white instead.
    split = next(
        x for x in range(w // 8, w)
        if any(not outside[y * w + x] for y in range(h)) is False
        or all(outside[y * w + x] for y in range(h))
    )
    unmat = lambda c, a: max(0, min(255, round((c - 255 * (1 - a / 255)) * 255 / a)))
    for y in range(h):
        for x in range(w):
            i = y * w + x
            r, g, b, _ = px[x, y]
            if outside[i]:
                px[x, y] = (255, 255, 255, 0)
                continue
            if x > split:
                a = 255 - min(r, g, b)
                px[x, y] = (unmat(r, a), unmat(g, a), unmat(b, a), a) if a else (255, 255, 255, 0)
                continue
            # Soften the anti-aliased rim next to the background: alpha from
            # how far the pixel is from white, colour un-blended from white.
            near = any(
                0 <= x + dx < w and 0 <= y + dy < h and outside[(y + dy) * w + x + dx]
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            )
            if near:
                a = max(1, 255 - min(r, g, b))
                px[x, y] = (unmat(r, a), unmat(g, a), unmat(b, a), a)
    return im


def trimmed(im: Image.Image, pad: int) -> Image.Image:
    box = im.getchannel("A").getbbox()
    im = im.crop(box)
    out = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (255, 255, 255, 0))
    out.paste(im, (pad, pad))
    return out


def save(im: Image.Image, path: Path, width: int | None = None, size: int | None = None):
    if width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    if size:
        side = max(im.size)
        sq = Image.new("RGBA", (side, side), (255, 255, 255, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        im = sq.resize((size, size), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, optimize=True)
    print(path.relative_to(ROOT), im.size, path.stat().st_size)


def main():
    full = trimmed(transparent_outside(Image.open(SRC)), pad=4)
    save(full, ROOT / "manuals/brand/logo.png", width=1200)
    save(full, ROOT / "frontend/src/shared/assets/docmate-logo.png", width=480)
    # The document mark is the artwork left of the wordmark's gap.
    alpha = full.getchannel("A")
    cols = [any(alpha.getpixel((x, y)) for y in range(0, full.height, 2)) for x in range(full.width)]
    x = next(i for i in range(full.width // 8, full.width) if not cols[i])  # first empty column
    icon = trimmed(full.crop((0, 0, x, full.height)), pad=2)
    save(icon, ROOT / "frontend/src/shared/assets/docmate-icon.png", size=192)


if __name__ == "__main__":
    main()

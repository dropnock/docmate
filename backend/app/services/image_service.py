from io import BytesIO

from PIL import Image


def tiff_to_png(data: bytes, page: int = 0) -> tuple[bytes, int]:
    """No browser natively decodes TIFF in an <img>/canvas context (OpenSeadragon's
    "simple image" tile source is just `new Image(); image.src = url` under the
    hood), so scanned-document TIFFs must be converted before they can be viewed.
    Returns the requested page (clamped to the last page if out of range)
    alongside the total page count, so callers can drive pagination."""
    with Image.open(BytesIO(data)) as img:
        page_count = getattr(img, "n_frames", 1)
        img.seek(min(max(page, 0), page_count - 1))
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue(), page_count


def tiff_to_pdf(data: bytes) -> bytes:
    """Converts every frame of a (possibly multi-page) TIFF into a single
    multi-page PDF, so the whole scan can be served once through the existing
    PDF <iframe> viewing path instead of re-decoding a page at a time on every
    view. Browsers paginate/zoom a PDF natively, so no per-page bookkeeping is
    needed downstream."""
    with Image.open(BytesIO(data)) as img:
        page_count = getattr(img, "n_frames", 1)
        pages = []
        for i in range(page_count):
            img.seek(i)
            pages.append(img.convert("RGB"))
        buf = BytesIO()
        pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
        return buf.getvalue()

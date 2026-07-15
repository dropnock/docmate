from io import BytesIO

from PIL import Image


def tiff_to_png(data: bytes) -> bytes:
    """No browser natively decodes TIFF in an <img>/canvas context (OpenSeadragon's
    "simple image" tile source is just `new Image(); image.src = url` under the
    hood), so scanned-document TIFFs must be converted before they can be viewed.
    Renders only the first page/frame — the viewer has no multi-page navigation."""
    with Image.open(BytesIO(data)) as img:
        img.seek(0)
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()

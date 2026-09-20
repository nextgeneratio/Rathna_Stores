"""AI preprocessing for staff-uploaded product images."""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageEnhance, ImageOps

_AI_SESSION = None


class ImageProcessingError(Exception):
    """A user-safe image processing failure."""


def process_product_image(file_bytes: bytes) -> tuple[bytes, str]:
    """Remove the background and apply light, non-destructive enhancement."""
    try:
        from rembg import new_session, remove
    except ImportError as exc:
        raise ImageProcessingError(
            "AI image processing is not installed. Run pip install -r requirements.txt."
        ) from exc

    try:
        global _AI_SESSION
        if _AI_SESSION is None:
            _AI_SESSION = new_session("u2net")
        source = Image.open(BytesIO(file_bytes)).convert("RGBA")
        foreground = remove(source, session=_AI_SESSION)
        if not isinstance(foreground, Image.Image):
            foreground = Image.open(BytesIO(foreground)).convert("RGBA")
        foreground = ImageOps.exif_transpose(foreground).convert("RGBA")

        alpha = foreground.getchannel("A")
        rgb = Image.new("RGB", foreground.size, "#ffffff")
        rgb.paste(foreground, mask=alpha)
        enhanced = ImageEnhance.Color(rgb).enhance(1.08)
        enhanced = ImageEnhance.Contrast(enhanced).enhance(1.06)
        enhanced = ImageEnhance.Sharpness(enhanced).enhance(1.12)
        enhanced.putalpha(alpha)

        output = BytesIO()
        enhanced.save(output, format="WEBP", quality=92, method=6)
        return output.getvalue(), "image/webp"
    except Exception as exc:
        raise ImageProcessingError(
            "The image could not be processed. Please try another clear product photo."
        ) from exc

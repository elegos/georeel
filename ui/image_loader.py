from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtGui import QImage


def load_qimage(path: str, max_height: int | None = None) -> QImage:
    """Load an image from disk as a QImage, correcting EXIF orientation.

    If max_height is given the image is scaled down inside Pillow before
    conversion, keeping memory usage proportional to the output size rather
    than the original file size.
    """
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            if max_height is not None and img.height > max_height:
                ratio = max_height / img.height
                img = img.resize(
                    (max(1, round(img.width * ratio)), max_height),
                    Image.LANCZOS,
                )
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            return QImage(
                data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888
            ).copy()
    except (UnidentifiedImageError, OSError, Exception):
        return QImage()

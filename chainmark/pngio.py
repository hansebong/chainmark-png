"""PNG-only I/O and a code-generated drawing; no PDF dependencies."""
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, PngImagePlugin


def decode_png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Expected PNG bytes, not a renamed JPEG/PDF")
    with Image.open(BytesIO(data)) as source:
        if source.format != "PNG" or getattr(source, "n_frames", 1) != 1:
            raise ValueError("Only single-frame PNG is supported")
        if source.width * source.height > 30_000_000:
            raise ValueError("PNG exceeds the PoC limit of 30 megapixels")
        image = ImageOps.exif_transpose(source).convert("RGBA")
        white = Image.new("RGBA", image.size, (255, 255, 255, 255))
        return np.array(Image.alpha_composite(white, image).convert("RGB"))


def encode_png(image, event_id=None):
    info = PngImagePlugin.PngInfo()
    if event_id:
        # A receipt token distinguishes revisits without amplifying an existing
        # watermark. It is NOT used as evidence by the pattern detector.
        info.add_text("ChainMark-receipt", event_id)
    stream = BytesIO()
    Image.fromarray(image).save(stream, format="PNG", pnginfo=info)
    return stream.getvalue()


def load_png(path):
    return decode_png(Path(path).read_bytes())


def save_png(path, image):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(image))


def sample_drawing():
    """Synthetic engineering drawing, generated directly as pixels."""
    image = np.full((1024, 800, 3), 248, np.uint8)
    for x in range(32, 780, 32):
        cv2.line(image, (x, 128), (x, 928), (224, 230, 234), 1)
    for y in range(128, 929, 32):
        cv2.line(image, (32, y), (768, y), (224, 230, 234), 1)
    cv2.rectangle(image, (0, 0), (799, 105), (22, 45, 66), -1)
    cv2.putText(image, "CHAINMARK / DRAWING 001", (30, 47), cv2.FONT_HERSHEY_SIMPLEX, .9, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(image, "Synthetic PNG - folder transfer experiment", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, .53, (220, 235, 241), 1, cv2.LINE_AA)
    dark, accent = (38, 58, 73), (20, 123, 134)
    cv2.rectangle(image, (96, 224), (704, 800), dark, 5)
    cv2.line(image, (384, 224), (384, 800), dark, 4)
    cv2.line(image, (96, 512), (704, 512), dark, 4)
    cv2.rectangle(image, (372, 390), (396, 454), (248, 248, 248), -1)
    cv2.rectangle(image, (530, 500), (594, 524), (248, 248, 248), -1)
    for i, (x, y) in enumerate(((160, 300), (450, 300), (160, 585), (450, 585))):
        cv2.putText(image, f"LAB {i+1:02d}", (x, y), cv2.FONT_HERSHEY_SIMPLEX, .85, dark, 2, cv2.LINE_AA)
        cv2.rectangle(image, (x, y+30), (x+128, y+95), accent, 2)
        for j in range(i+2):
            cv2.circle(image, (x+15+j*24, y+132), 8, dark, 2, cv2.LINE_AA)
        cv2.putText(image, f"SECTION {10+i*7}.25", (x, y+180), cv2.FONT_HERSHEY_SIMPLEX, .43, dark, 1, cv2.LINE_AA)
    cv2.arrowedLine(image, (96, 173), (704, 173), accent, 2, tipLength=.025)
    cv2.putText(image, "24 000 mm", (330, 158), cv2.FONT_HERSHEY_SIMPLEX, .55, dark, 1, cv2.LINE_AA)
    rng = np.random.default_rng(7519)
    for i in range(8):
        label = f"R{i+1:02d} / {int(rng.integers(1000, 9999))} / toler. {i+1}.5"
        cv2.putText(image, label, (48+(i%2)*375, 855+(i//2)*24), cv2.FONT_HERSHEY_SIMPLEX, .44, dark, 1, cv2.LINE_AA)
    cv2.putText(image, "NO REAL BLUEPRINT / NO CAMERA VALIDATION", (42, 985), cv2.FONT_HERSHEY_SIMPLEX, .5, dark, 1, cv2.LINE_AA)
    return image

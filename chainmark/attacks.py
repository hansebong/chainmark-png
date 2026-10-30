"""Deterministic digital distortions. These do not simulate a validated camera."""
import cv2
import numpy as np

ATTACKS = ("identity", "jpeg", "crop", "rotate", "resize", "perspective", "compound", "severe_crop")


def distort(image, name, trial=0):
    h, w = image.shape[:2]
    level = trial % 3
    if name == "identity":
        return image.copy(), {}
    if name == "jpeg":
        q = (70, 40, 20)[level]
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
        if not ok:
            raise IOError("JPEG encoding failed")
        return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB), {"quality": q}
    if name in ("crop", "severe_crop"):
        fraction = (0.08, 0.16, 0.25)[level] if name == "crop" else (0.40, 0.43, 0.46)[level]
        dx, dy = int(w*fraction), int(h*fraction)
        return image[dy:h-dy, dx:w-dx].copy(), {"removed_each_edge": fraction}
    if name == "rotate":
        angle = (2.0, -5.0, 11.0)[level]
        m = cv2.getRotationMatrix2D((w/2, h/2), angle, 1)
        return cv2.warpAffine(image, m, (w, h), borderValue=(255, 255, 255)), {"degrees": angle}
    if name == "resize":
        scale = (.75, .50, .30)[level]
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), {"scale": scale}
    if name == "perspective":
        amount = (.04, .09, .16)[level]
        src = np.float32([[0, 0], [w-1, 0], [w-1, h-1], [0, h-1]])
        dst = np.float32([[amount*w, amount*h], [(1-amount)*w, 0],
                          [w-1, (1-amount)*h], [0, h-1]])
        m = cv2.getPerspectiveTransform(src, dst)
        return cv2.warpPerspective(image, m, (w, h), borderValue=(255, 255, 255)), {"corner_fraction": amount}
    if name == "compound":
        value, _ = distort(image, "perspective", level)
        value, _ = distort(value, "resize", level)
        value, _ = distort(value, "jpeg", level)
        return value, {"components": ["perspective", "resize", "jpeg"], "level": level}
    raise ValueError(f"Unknown distortion: {name}")

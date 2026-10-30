"""Additive spread spectrum in selected 32x32 block DCT coefficients.

The detector REQUIRES the clean reference page and the secret key. It is not
a blind detector. Codes are bound to document ID, page index, and user ID.
"""
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import hmac
import math

import cv2
import numpy as np


@dataclass(frozen=True)
class Config:
    block: int = 32
    strength: float = 18.0
    threshold: float = 0.12
    min_blocks: int = 24

    def __post_init__(self):
        if not isinstance(self.block, int) or self.block < 8 or self.block % 2:
            raise ValueError("DCT block must be an even integer >= 8")
        if not math.isfinite(self.strength) or self.strength <= 0:
            raise ValueError("Strength must be finite and positive")
        if not math.isfinite(self.threshold) or not 0 < self.threshold <= 1:
            raise ValueError("Threshold must be in (0, 1]")
        if not isinstance(self.min_blocks, int) or self.min_blocks < 1:
            raise ValueError("min_blocks must be a positive integer")

    def to_dict(self):
        return asdict(self)


FREQUENCIES = ((1, 2), (2, 1), (2, 2), (1, 3), (3, 1), (2, 3), (3, 2), (3, 3))


@lru_cache(maxsize=4)
def basis(block):
    if block < 8:
        raise ValueError("DCT block must be >= 8")
    result = []
    for row, col in FREQUENCIES:
        impulse = np.zeros((block, block), np.float32)
        impulse[row, col] = 1.0
        result.append(cv2.idct(impulse))
    return np.stack(result)


def codes(shape, user, doc_id, page, key, config=Config()):
    if not key or not user or not doc_id or page < 0:
        raise ValueError("Key, user, document ID and nonnegative page are required")
    # Length-prefixed fields avoid ambiguous concatenations. HMAC is used as a
    # PRF; this is a reproducible PoC codebook, not a production watermark cipher.
    fields = ["ChainMark/v1", doc_id, str(page), user]
    message = b"".join(len(s.encode()).to_bytes(4, "big") + s.encode() for s in fields)
    seed = int.from_bytes(hmac.new(key, message, hashlib.sha256).digest()[:16], "big")
    rng = np.random.Generator(np.random.PCG64(seed))
    rows, cols = shape[0] // config.block, shape[1] // config.block
    return (rng.integers(0, 2, (rows, cols, len(FREQUENCIES)), dtype=np.int8) * 2 - 1).astype(np.float32)


def embed(image, user, doc_id, page, key, config=Config()):
    """Add this user's code to the CURRENT pixels; retain previous codes.

    No clean source is used here and no previous code is subtracted. Uint8
    clipping/rounding may weaken earlier patterns; preservation is empirical,
    not mathematically exact after unlimited forwards.
    """
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Expected uint8 RGB image")
    if config.strength <= 0:
        raise ValueError("Strength must be positive")
    code = codes(image.shape, user, doc_id, page, key, config)
    if not code.size:
        raise ValueError("Page smaller than one DCT block")
    b = config.block
    rows, cols = code.shape[:2]
    delta = config.strength * np.einsum("yxc,cij->yixj", code, basis(b), optimize=True)
    delta = delta.reshape(rows * b, cols * b)
    out = image.astype(np.float32)
    out[:rows*b, :cols*b] += delta[..., None]
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def score_aligned(reference, candidate, valid, users, doc_id, page, key, config=Config()):
    """Cosine correlation against reference-subtracted DCT coefficients.

    Score is similarity, not probability. Full valid blocks only, with clipped
    residuals to reduce domination by registration errors at document edges.
    """
    if reference.shape != candidate.shape or valid.shape != reference.shape[:2]:
        raise ValueError("Reference, aligned candidate and validity mask disagree")
    b = config.block
    rows, cols = reference.shape[0] // b, reference.shape[1] // b
    residual = candidate.astype(np.float32).mean(2) - reference.astype(np.float32).mean(2)
    blocks = residual[:rows*b, :cols*b].reshape(rows, b, cols, b).transpose(0, 2, 1, 3)
    mask_blocks = valid[:rows*b, :cols*b].reshape(rows, b, cols, b).mean((1, 3)) > 0.995
    count = int(mask_blocks.sum())
    if count < config.min_blocks:
        return {"status": "insufficient_overlap", "blocks": count, "scores": {u: None for u in users}}
    features = np.einsum("yxij,cij->yxc", blocks, basis(b), optimize=True)
    features = np.clip(features[mask_blocks].ravel(), -3*config.strength, 3*config.strength)
    denominator = float(np.linalg.norm(features) * np.sqrt(features.size))
    scores = {}
    for user in users:
        signs = codes(reference.shape, user, doc_id, page, key, config)[mask_blocks].ravel()
        scores[user] = float(np.dot(features, signs) / denominator) if denominator > 1e-6 else 0.0
    return {"status": "ok", "blocks": count, "scores": scores}

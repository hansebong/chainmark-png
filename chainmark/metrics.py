import math

import cv2
import numpy as np
from skimage.metrics import structural_similarity


def quality(reference, candidate, valid=None):
    """RGB PSNR and grayscale SSIM on valid overlap. Not camera quality."""
    if candidate.shape != reference.shape:
        raise ValueError("Quality requires images on the same pixel grid")
    mask = np.ones(reference.shape[:2], np.uint8) if valid is None else (valid > .995).astype(np.uint8)
    # Use only windows fully inside the overlap for SSIM.
    interior = cv2.erode(mask, np.ones((11, 11), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
    if not interior.any():
        return {"psnr_db": None, "ssim": None, "valid_fraction": float(mask.mean())}
    err = (reference.astype(np.float64)-candidate.astype(np.float64))**2
    mse = float(err[mask.astype(bool)].mean())
    psnr = 10*math.log10(255**2/mse) if mse else None  # JSON null = identical (+infinity), see flag.
    gray_ref = cv2.cvtColor(reference, cv2.COLOR_RGB2GRAY)
    gray_out = cv2.cvtColor(candidate, cv2.COLOR_RGB2GRAY)
    _, full = structural_similarity(gray_ref, gray_out, data_range=255, full=True,
                                     gaussian_weights=True, sigma=1.5, use_sample_covariance=False)
    return {"psnr_db": psnr, "psnr_infinite": mse == 0, "ssim": float(full[interior].mean()),
            "valid_fraction": float(mask.mean())}

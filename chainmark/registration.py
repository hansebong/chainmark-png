"""Feature-only geometry estimation: no benchmark ground truth is supplied."""
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Registration:
    status: str
    image: object = None
    valid: object = None
    homography: object = None
    inliers: int = 0
    coverage: float = 0.0
    method: str = "none"

    def metadata(self):
        return {"status": self.status, "inliers": self.inliers,
                "coverage": self.coverage, "method": self.method,
                "homography": self.homography.tolist() if self.homography is not None else None}


class ReferenceAligner:
    def __init__(self, reference):
        self.reference = reference
        self.gray = cv2.cvtColor(reference, cv2.COLOR_RGB2GRAY)
        self.sift = cv2.SIFT_create(nfeatures=2400, contrastThreshold=0.025)
        self.points, self.descriptors = self.sift.detectAndCompute(self.gray, None)

    def align(self, image):
        ref, gray = self.gray, cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        height, width = ref.shape
        # Identity is selected by observed pixel similarity, never attack labels.
        if gray.shape == ref.shape:
            smooth_error = np.mean(np.abs(cv2.GaussianBlur(gray.astype(np.float32), (9, 9), 2)
                                         - cv2.GaussianBlur(ref.astype(np.float32), (9, 9), 2)))
            if smooth_error < 1.5 and np.mean(np.abs(gray.astype(float)-ref)) < 4.5:
                return Registration("ok", image.copy(), np.ones(ref.shape, np.float32),
                                    np.eye(3), 0, 1.0, "pixel_identity")
        points, desc = self.sift.detectAndCompute(gray, None)
        if desc is None or self.descriptors is None:
            return Registration("no_features")
        pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(desc, self.descriptors, k=2)
        good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < 0.7*b.distance]
        if len(good) < 12:
            return Registration("too_few_matches", inliers=len(good))
        src = np.float32([points[m.queryIdx].pt for m in good])
        dst = np.float32([self.points[m.trainIdx].pt for m in good])
        cv2.setRNGSeed(20261031)
        transform, accepted = cv2.findHomography(src, dst, cv2.RANSAC, 2.0)
        inliers = int(accepted.sum()) if accepted is not None else 0
        if transform is None or not np.isfinite(transform).all() or inliers < 10 or inliers/len(good) < .3:
            return Registration("homography_rejected", inliers=inliers)
        # Reject near-pole homographies instead of trying to decode arbitrary warps.
        ih, iw = gray.shape
        corners = np.float32([[0, 0], [iw, 0], [iw, ih], [0, ih]])
        denom = corners @ transform[2, :2] + transform[2, 2]
        if np.any(np.abs(denom) < 1e-4) or np.min(denom)*np.max(denom) < 0:
            return Registration("unstable_homography", inliers=inliers)
        aligned = cv2.warpPerspective(image, transform, (width, height), borderValue=(255, 255, 255))
        valid = cv2.warpPerspective(np.ones(gray.shape, np.uint8), transform, (width, height),
                                    flags=cv2.INTER_NEAREST, borderValue=0)
        valid = cv2.erode(valid, np.ones((9, 9), np.uint8)).astype(np.float32)
        coverage = float(valid.mean())
        if coverage < .15:
            return Registration("insufficient_overlap", inliers=inliers, coverage=coverage)
        return Registration("ok", aligned, valid, transform, inliers, coverage, "sift_ransac")

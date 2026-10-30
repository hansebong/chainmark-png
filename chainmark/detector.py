from .registration import ReferenceAligner
from .watermark import Config, score_aligned


class Detector:
    def __init__(self, reference, doc_id, page, key, config=Config(), threshold=None):
        self.reference = reference
        self.aligner = ReferenceAligner(reference)
        self.doc_id, self.page, self.key = doc_id, page, key
        self.config = config
        self.threshold = config.threshold if threshold is None else float(threshold)
        if not 0 < self.threshold <= 1:
            raise ValueError("Correlation threshold must be in (0, 1]")

    def detect(self, candidate, users):
        reg = self.aligner.align(candidate)
        if reg.status == "ok":
            result = score_aligned(self.reference, reg.image, reg.valid, users,
                                   self.doc_id, self.page, self.key, self.config)
        else:
            result = {"status": reg.status, "blocks": 0, "scores": {u: None for u in users}}
        result.update({"registration": reg.metadata(), "threshold": self.threshold,
                       "detected": [u for u, s in result["scores"].items()
                                    if s is not None and s >= self.threshold]})
        return result, reg

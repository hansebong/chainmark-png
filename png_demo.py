"""Run in PyCharm: real file COPY then MOVE, serviced by a separate watcher.

No PDF is rendered or created. png_watch.py provides the manual folder test.
"""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import cv2

from chainmark.attacks import ATTACKS, distort
from chainmark.detector import Detector
from chainmark.metrics import quality
from chainmark.pngio import decode_png, encode_png, save_png
from chainmark.pngwatch import PNGWorkspace, initialize, sha, write_json
from chainmark.watermark import embed

ROOT = Path(__file__).resolve().parent


def wait_for(predicate, process, timeout=40):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Watcher exited early ({process.returncode})")
        value = predicate()
        if value:
            return value
        time.sleep(.15)
    raise TimeoutError("Timed out waiting for the folder watcher")


def run_demo(root, trials=1):
    cv2.setNumThreads(1)
    root = initialize(root)
    watcher = subprocess.Popen([sys.executable, "-u", str(ROOT/"png_watch.py"), "watch", "--workspace", str(root)])
    workspace = PNGWorkspace(root)
    try:
        status = root/"watcher_status.json"
        wait_for(lambda: status.exists() and json.loads(status.read_text())["status"] == "watching", watcher)
        snapshots = root/"snapshots"
        snapshots.mkdir()
        steps, current = [], root/"source.png"
        print("PNG experiment: watching actual files; folder IDs simulate users.", flush=True)
        for index, user in enumerate(("A", "B", "C"), 1):
            target = root/user/"drawing.png"
            if user == "C":
                print(f"MOVE: B/drawing.png -> C/drawing.png", flush=True)
                shutil.move(str(current), str(target))
            else:
                print(f"COPY: {current.name} -> {user}/drawing.png", flush=True)
                shutil.copy2(current, target)

            def ready():
                if not target.exists():
                    return None
                row = workspace._lookup(sha(target.read_bytes()))
                return row if row and row["recipient"] == user and row["published"] else None

            wait_for(ready, watcher)
            result = workspace.inspect(target)
            expected = {"A", "B", "C"} & set(("A", "B", "C")[:index])
            found = result["pattern_evidence"]
            if set(found["detected"]) != expected:
                raise RuntimeError(f"Unexpected pattern result at {user}: {found}")
            snapshot = snapshots/f"{index:02d}_{user}.png"
            snapshot.write_bytes(target.read_bytes())
            q = quality(workspace.reference, decode_png(target.read_bytes()))
            steps.append({"folder": user, "file": str(snapshot.relative_to(root)), "quality": q, **result})
            values = " / ".join(f"{u}={found['scores'][u]:.3f}" for u in ("A", "B", "C", "CONTROL"))
            print(f"DETECTED [{user}]: {values}; threshold={found['threshold']:.3f}; {found['detected']}", flush=True)
            current = target
        baseline = embed(workspace.reference, "C", workspace.settings["doc_id"], 0, workspace.key, workspace.config)
        baseline_path = root/"replacement_C_only.png"
        save_png(baseline_path, baseline)
        baseline_result = workspace.inspect(baseline_path)["pattern_evidence"]
        write_json(root/"replacement_scores.json", baseline_result)
        if baseline_result["detected"] != ["C"]:
            raise RuntimeError("Replacement baseline failed")
        final = workspace.inspect(current)
        print("RECORDED LINEAGE: " + " -> ".join(final["transfer_evidence"]["recorded_order"]), flush=True)
        write_json(root/"step_scores.json", steps)
        write_json(root/"final_detection.json", final)
        write_json(root/"png_experiment.json", {"automatic_watcher": True, "real_filesystem_operations": ["copy", "copy", "move"],
                   "threshold_calibrated": False, "folder_names_are_simulated_users": True,
                   "final_detected": final["pattern_evidence"]["detected"], "camera_tested": False})
    finally:
        (root/".chainmark"/"stop.request").touch()
        try:
            watcher.wait(timeout=10)
        except subprocess.TimeoutExpired:
            watcher.terminate()
            watcher.wait(timeout=5)
        workspace.close()
    if watcher.returncode:
        raise RuntimeError(f"Watcher exit code: {watcher.returncode}")
    # Attack tests operate on PNG inputs, with the SAME detector for controls
    # and baseline. Attacked byte streams are not claimed to match the ledger.
    workspace = PNGWorkspace(root)
    try:
        detector = Detector(workspace.reference, workspace.settings["doc_id"], 0, workspace.key, workspace.config)
        final_pixels = decode_png((root/"C"/"drawing.png").read_bytes())
        users = ["A", "B", "C", "CONTROL"]
        rows = []
        for mode, pixels, present in (("incremental", final_pixels, {"A", "B", "C"}),
                                       ("replacement", baseline, {"C"}),
                                       ("unmarked_control", workspace.reference, set())):
            for attack in ATTACKS:
                for trial in range(trials):
                    if attack == "identity" and trial:
                        continue
                    image, params = distort(pixels, attack, trial)
                    found, aligned = detector.detect(image, users)
                    q = quality(workspace.reference, aligned.image, aligned.valid) if aligned.status == "ok" else {}
                    for user, score in found["scores"].items():
                        rows.append({"mode": mode, "attack": attack, "trial": trial, "parameters": params,
                                     "user": user, "score": score, "threshold": found["threshold"],
                                     "expected": user in present, "detected": user in found["detected"],
                                     "status": found["status"], "coverage": aligned.coverage,
                                     "aligned_psnr_db": q.get("psnr_db"), "aligned_ssim": q.get("ssim")})
                    if mode == "incremental":
                        save_png(root/"attacks"/f"{attack}_{trial}.png", image)
        write_json(root/"distortion_scores.json", rows)
        with (root/"distortion_scores.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows({**r, "parameters": json.dumps(r["parameters"])} for r in rows)
        summary = []
        for mode in ("incremental", "replacement", "unmarked_control"):
            for attack in ATTACKS:
                selected = [r for r in rows if r["mode"] == mode and r["attack"] == attack]
                positive = [r for r in selected if r["expected"]]
                negative = [r for r in selected if not r["expected"]]
                tp = sum(r["detected"] for r in positive)
                fp = sum(r["detected"] for r in negative)
                scored_negatives = sum(r["score"] is not None for r in negative)
                summary.append({"mode": mode, "attack": attack, "tp": tp, "fn": len(positive)-tp,
                                "fp": fp, "tn": len(negative)-fp,
                                "detection_rate": tp/len(positive) if positive else None,
                                "false_positive_rate": fp/len(negative) if negative else None,
                                "false_positive_rate_scorable": fp/scored_negatives if scored_negatives else None,
                                "unscorable_queries": sum(r["score"] is None for r in selected)})
        write_json(root/"distortion_summary.json", {"by_attack": summary, "threshold_calibrated": False,
                   "notes": ["Small synthetic PNG experiment; fixed severities, not independent population samples.",
                             "Registration failures count as FN on positive queries and no detection on negative queries.",
                             "Attack PSNR/SSIM use registered valid overlap; stage quality uses the full original image."]})
        write_json(root/"failures.json", [r for r in rows if r["expected"] != r["detected"] or r["score"] is None])
        # Image pixels are identical after metadata removal, so patterns remain;
        # byte hash no longer matches a receipt, thus order stays unverified.
        stripped = root/"metadata_stripped.png"
        stripped.write_bytes(encode_png(final_pixels))
        write_json(root/"metadata_stripped_detection.json", workspace.inspect(stripped))
    finally:
        workspace.close()
    print(f"DONE: {root}", flush=True)
    print("Actual folder arrivals were tested. External messengers/cameras were not.", flush=True)
    return root


def main():
    parser = argparse.ArgumentParser(description="PNG auto folder-move experiment")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--trials", choices=(1, 2, 3), type=int, default=1)
    args = parser.parse_args()
    root = args.out or ROOT/"results"/datetime.now().strftime("png_%Y%m%d_%H%M%S_%f")
    run_demo(root, args.trials)


if __name__ == "__main__":
    main()

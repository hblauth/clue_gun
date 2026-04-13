"""
Validate the star detector against data/labels/stars.csv.

Usage:
    python -m services.image_processor.validate [--limit N] [--out results.json]

For each labeled image that has a matching puzzle JSON, runs
detect_stars_in_clue_list and compares detections to ground truth.
Prints per-image results and aggregate precision / recall / F1.
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image

pillow_heif.register_heif_opener()

# Project root = two levels up from this file
ROOT = Path(__file__).resolve().parents[2]
LABELS_CSV = ROOT / "data" / "labels" / "stars.csv"
PUZZLES_DIR = ROOT / "data" / "puzzles"
PHOTOS_DIR = Path(os.path.expanduser("~/Desktop/crossword_photos/Times"))


def _parse_stars(stars_str: str) -> set[str]:
    """Parse "12a, 18a, 4d" → {"12a", "18a", "4d"}."""
    result = set()
    for tok in stars_str.split(","):
        tok = tok.strip().lower()
        # normalise direction: "ac" / "across" → "a"
        tok = tok.replace("ac", "a").replace("across", "a").replace("down", "d")
        if tok:
            result.add(tok)
    return result


def _detection_key(ann) -> str:
    """ClueAnnotation → "12a" / "4d" style key."""
    direction = ann.direction.lower()
    if direction in ("ac", "across"):
        direction = "a"
    elif direction in ("d", "down"):
        direction = "d"
    return f"{ann.clue_number}{direction}"


def run_validation(limit: int | None = None, out_path: str | None = None):
    from services.image_processor.pipeline import detect_stars_in_clue_list  # noqa: PLC0415

    rows = list(csv.DictReader(open(LABELS_CSV)))
    puzzles_have = {f.replace(".json", "") for f in os.listdir(PUZZLES_DIR) if f.endswith(".json")}

    runnable = [
        r for r in rows
        if r["puzzle_number"].strip() in puzzles_have
        and (PHOTOS_DIR / r["filename"]).exists()
    ]
    if limit:
        runnable = runnable[:limit]

    print(f"Running on {len(runnable)} images …\n")

    tp_total = fp_total = fn_total = 0
    results = []

    for i, row in enumerate(runnable, 1):
        filename = row["filename"]
        puzzle_num = int(row["puzzle_number"].strip())
        gt_stars = _parse_stars(row["stars"])

        path = PHOTOS_DIR / filename
        t0 = time.time()
        try:
            img_pil = Image.open(path)
            img = np.array(img_pil.convert("RGB"))
            detections = detect_stars_in_clue_list(img, puzzle_number=puzzle_num)
        except Exception as exc:
            print(f"[{i:3d}/{len(runnable)}] {filename} ERROR: {exc}")
            continue
        elapsed = time.time() - t0

        detected_keys = {_detection_key(d) for d in detections}

        tp = len(detected_keys & gt_stars)
        fp = len(detected_keys - gt_stars)
        fn = len(gt_stars - detected_keys)

        tp_total += tp
        fp_total += fp
        fn_total += fn

        status = "OK" if fp == 0 and fn == 0 else "MISS" if fp == 0 else "FP" if fn == 0 else "BAD"
        gt_str  = ",".join(sorted(gt_stars))  or "∅"
        det_str = ",".join(sorted(detected_keys)) or "∅"
        print(
            f"[{i:3d}/{len(runnable)}] {filename:40s} "
            f"GT={gt_str:25s} DET={det_str:25s} "
            f"TP={tp} FP={fp} FN={fn} [{status}] {elapsed:.1f}s"
        )
        results.append({
            "filename": filename,
            "puzzle": puzzle_num,
            "gt": sorted(gt_stars),
            "detected": sorted(detected_keys),
            "tp": tp,
            "fp": fp,
            "fn": fn,
        })

    # Aggregate
    precision = tp_total / (tp_total + fp_total) if (tp_total + fp_total) else 0.0
    recall    = tp_total / (tp_total + fn_total) if (tp_total + fn_total) else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    print(f"\n{'='*60}")
    print(f"Images:    {len(results)}")
    print(f"TP={tp_total}  FP={fp_total}  FN={fn_total}")
    print(f"Precision: {precision:.3f}")
    print(f"Recall:    {recall:.3f}")
    print(f"F1:        {f1:.3f}")

    if out_path:
        with open(out_path, "w") as f:
            json.dump({"precision": precision, "recall": recall, "f1": f1, "images": results}, f, indent=2)
        print(f"Results written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max images to process")
    parser.add_argument("--out", default=None, help="Write JSON results to this path")
    args = parser.parse_args()
    run_validation(limit=args.limit, out_path=args.out)

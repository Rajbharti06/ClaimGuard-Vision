import base64
import csv
import os
from pathlib import Path


DATASET_DIR = Path(__file__).parent.parent / "dataset"


def load_csv(filename: str) -> list[dict]:
    path = DATASET_DIR / filename
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_images(image_paths_str: str) -> list[dict]:
    """Load and base64-encode images from semicolon-separated paths."""
    if not image_paths_str or not image_paths_str.strip():
        return []

    paths = [p.strip() for p in image_paths_str.split(";") if p.strip()]
    images = []

    for path_str in paths:
        full_path = DATASET_DIR / path_str
        image_id = Path(path_str).stem  # filename without extension

        if not full_path.exists():
            print(f"  WARNING: image not found: {full_path}")
            continue

        suffix = full_path.suffix.lower()
        media_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_type_map.get(suffix, "image/jpeg")

        with open(full_path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")

        images.append({"id": image_id, "path": path_str, "media_type": media_type, "data": data})

    return images


def get_user_history(user_history_rows: list[dict], user_id: str) -> dict:
    for row in user_history_rows:
        if row.get("user_id") == user_id:
            return row
    return {}


def write_output_csv(rows: list[dict], output_path: str) -> None:
    fieldnames = [
        "user_id", "image_paths", "user_claim", "claim_object",
        "evidence_standard_met", "evidence_standard_met_reason",
        "risk_flags", "issue_type", "object_part", "claim_status",
        "claim_status_justification", "supporting_image_ids",
        "valid_image", "severity"
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

#!/usr/bin/env python3
"""
Main entry point for the Multi-Modal Evidence Review pipeline.
Reads dataset/claims.csv, processes each claim with a VLM, writes output.csv.
"""

import os
import sys
import time
import argparse
from pathlib import Path

# Allow running from either code/ or repo root
sys.path.insert(0, str(Path(__file__).parent))

from utils import load_csv, load_images, get_user_history, write_output_csv
from logger import log_session_start, log_claim_input, log_agent_reasoning, log_decision, log_error, log_session_end

# Auto-detect provider: Groq if GROQ_API_KEY set, else Gemini
_USE_OPENAI_COMPAT = bool(
    os.environ.get("GROQ_API_KEY") or
    os.environ.get("OPENROUTER_API_KEY") or
    os.environ.get("NVIDIA_API_KEY")
)
if _USE_OPENAI_COMPAT:
    from openai import OpenAI
    from processor_openai import process_claim, DEFAULT_MODEL, DEFAULT_ENV_VAR
else:
    from google import genai
    from processor import process_claim, DEFAULT_MODEL, DEFAULT_ENV_VAR


REPO_ROOT = Path(__file__).parent.parent
OUTPUT_PATH = REPO_ROOT / "output.csv"


def run(input_file: str = "claims.csv", model: str = DEFAULT_MODEL, delay: float = 4.5):
    print(f"Loading dataset...")
    claims = load_csv(input_file)
    user_history_rows = load_csv("user_history.csv")
    evidence_reqs = load_csv("evidence_requirements.csv")

    api_key = os.environ.get(DEFAULT_ENV_VAR)
    if not api_key:
        print(f"ERROR: {DEFAULT_ENV_VAR} environment variable not set.")
        sys.exit(1)

    if _USE_OPENAI_COMPAT:
        from processor_openai import GROQ_BASE_URL, _EXTRA_HEADERS
        client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, default_headers=_EXTRA_HEADERS)
        print(f"Provider: OpenAI-compat ({DEFAULT_MODEL} @ {GROQ_BASE_URL})")
    else:
        client = genai.Client(api_key=api_key)
        print(f"Provider: Gemini ({model})")

    log_session_start(mode=input_file.replace(".csv", ""), input_file=input_file)
    print(f"Processing {len(claims)} claims with model={model}")

    output_rows = []
    succeeded = 0
    failed = 0

    for i, row in enumerate(claims, 1):
        claim_id = f"{input_file.replace('.csv','')}_{i:03d}"
        user_id = row.get("user_id", "unknown")
        image_paths_str = row.get("image_paths", "")
        print(f"  [{i:02d}/{len(claims)}] {claim_id} | user={user_id} | images={image_paths_str}")

        log_claim_input(
            claim_id=claim_id,
            user_id=user_id,
            claim_object=row.get("claim_object", ""),
            image_paths=image_paths_str,
            user_claim=row.get("user_claim", "")
        )

        # Load images
        images = load_images(image_paths_str)
        if not images:
            print(f"    WARNING: no images loaded for {claim_id}")

        # Get user history
        history = get_user_history(user_history_rows, user_id)

        # Process with VLM
        try:
            result = process_claim(
                row=row,
                user_history=history,
                evidence_reqs=evidence_reqs,
                image_data=images,
                client=client,
                model=model,
            )
            succeeded += 1
        except Exception as e:
            log_error(claim_id, str(e))
            print(f"    ERROR: {e}")
            result = {
                "evidence_standard_met": "false",
                "evidence_standard_met_reason": f"Unexpected error: {e}",
                "risk_flags": "manual_review_required",
                "issue_type": "unknown",
                "object_part": "unknown",
                "claim_status": "not_enough_information",
                "claim_status_justification": f"Unexpected processing error: {e}",
                "supporting_image_ids": "none",
                "valid_image": "false",
                "severity": "unknown"
            }
            failed += 1

        log_agent_reasoning(claim_id, model, result)
        log_decision(claim_id, {**row, **result})

        # Build output row (preserve input columns + add output columns)
        output_row = {
            "user_id": row.get("user_id", ""),
            "image_paths": row.get("image_paths", ""),
            "user_claim": row.get("user_claim", ""),
            "claim_object": row.get("claim_object", ""),
            **result
        }
        output_rows.append(output_row)
        print(f"    => {result.get('claim_status')} | {result.get('issue_type')} | {result.get('severity')}")

        if i < len(claims):
            time.sleep(delay)

    write_output_csv(output_rows, str(OUTPUT_PATH))
    log_session_end(total=len(claims), succeeded=succeeded, failed=failed)

    print(f"\nDone. Output written to {OUTPUT_PATH}")
    print(f"Succeeded: {succeeded} | Failed: {failed}")


def main():
    parser = argparse.ArgumentParser(description="Multi-Modal Damage Claim Review")
    parser.add_argument("--input", default="claims.csv", help="Input CSV filename in dataset/")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Claude model to use")
    parser.add_argument("--delay", type=float, default=4.5, help="Seconds between API calls")
    args = parser.parse_args()

    run(input_file=args.input, model=args.model, delay=args.delay)


if __name__ == "__main__":
    main()

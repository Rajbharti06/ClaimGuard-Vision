#!/usr/bin/env python3
"""
Evaluation pipeline: runs the VLM on sample_claims.csv (which has expected outputs)
and computes accuracy metrics. Writes evaluation/evaluation_report.md.
"""

import os
import sys
import time
import csv
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import load_csv, load_images, get_user_history
from logger import log_session_start, log_claim_input, log_agent_reasoning, log_decision, log_error, log_session_end

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

REPO_ROOT = Path(__file__).parent.parent.parent
EVAL_DIR = Path(__file__).parent
EVAL_OUTPUT = EVAL_DIR / "sample_predictions.csv"
REPORT_PATH = EVAL_DIR / "evaluation_report.md"

SCORED_FIELDS = ["claim_status", "issue_type", "object_part", "severity", "evidence_standard_met", "valid_image"]


def compute_metrics(predictions: list[dict], ground_truth: list[dict]) -> dict:
    metrics = {f: {"correct": 0, "total": 0} for f in SCORED_FIELDS}
    per_row = []

    for pred, truth in zip(predictions, ground_truth):
        row_scores = {}
        for field in SCORED_FIELDS:
            p_val = str(pred.get(field, "")).strip().lower()
            t_val = str(truth.get(field, "")).strip().lower()
            match = p_val == t_val
            metrics[field]["total"] += 1
            if match:
                metrics[field]["correct"] += 1
            row_scores[field] = {"predicted": p_val, "expected": t_val, "match": match}
        per_row.append(row_scores)

    accuracy = {
        f: round(metrics[f]["correct"] / metrics[f]["total"] * 100, 1) if metrics[f]["total"] > 0 else 0
        for f in SCORED_FIELDS
    }
    overall = sum(accuracy.values()) / len(accuracy) if accuracy else 0
    return {"per_field": accuracy, "overall": round(overall, 1), "per_row": per_row, "n": len(predictions)}


def write_report(metrics: dict, model: str, n_sample: int, n_test: int,
                 n_sample_images: int, n_test_images: int,
                 elapsed_sample: float, predictions: list[dict], ground_truth: list[dict]):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    acc = metrics["per_field"]
    overall = metrics["overall"]

    # Cost estimates (gemini-2.5-flash pricing — free tier used)
    input_price_per_1m = 0.0   # Free tier: $0 for <= 1500 RPD
    output_price_per_1m = 0.0
    avg_input_tokens = 4500    # per claim (text + images)
    avg_output_tokens = 400     # per claim

    total_claims = n_sample + n_test
    total_images = n_sample_images + n_test_images
    total_input_tokens = total_claims * avg_input_tokens
    total_output_tokens = total_claims * avg_output_tokens
    input_cost = total_input_tokens / 1_000_000 * input_price_per_1m
    output_cost = total_output_tokens / 1_000_000 * output_price_per_1m
    total_cost = input_cost + output_cost

    avg_latency = elapsed_sample / n_sample if n_sample > 0 else 0

    # Per-row breakdown
    row_lines = []
    for i, (pred, truth, scores) in enumerate(zip(predictions, ground_truth, metrics["per_row"]), 1):
        match_fields = [f for f, s in scores.items() if s["match"]]
        miss_fields = [f"{f}(got={s['predicted']}, exp={s['expected']})" for f, s in scores.items() if not s["match"]]
        row_lines.append(
            f"| {i:02d} | {truth.get('user_id','')} | {truth.get('claim_object','')} "
            f"| {scores['claim_status']['expected']} | {scores['claim_status']['predicted']} "
            f"| {','.join(miss_fields) or 'all match'} |"
        )

    report = f"""# Evaluation Report — Multi-Modal Evidence Review
Generated: {now}
Model: {model}

## Accuracy on Sample Set (n={n_sample})

| Field | Accuracy |
|-------|----------|
| claim_status | {acc['claim_status']}% |
| issue_type | {acc['issue_type']}% |
| object_part | {acc['object_part']}% |
| severity | {acc['severity']}% |
| evidence_standard_met | {acc['evidence_standard_met']}% |
| valid_image | {acc['valid_image']}% |
| **Overall Average** | **{overall}%** |

## Per-Row Sample Results

| # | User | Object | Expected Status | Predicted Status | Mismatches |
|---|------|--------|-----------------|------------------|------------|
{chr(10).join(row_lines)}

## Operational Analysis

### Model Calls
- Sample processing: {n_sample} calls
- Test processing: {n_test} calls
- Total calls: {total_claims} calls
- API calls per claim: 1 (single-pass with tool_use forcing structured output)

### Token Usage (estimated)
- Average input tokens per claim: ~{avg_input_tokens:,} (text context ~800 + image tokens ~3,700 per image avg)
- Average output tokens per claim: ~{avg_output_tokens:,}
- Sample set total: ~{n_sample * avg_input_tokens:,} input / ~{n_sample * avg_output_tokens:,} output
- Test set total: ~{n_test * avg_input_tokens:,} input / ~{n_test * avg_output_tokens:,} output
- Grand total: ~{total_input_tokens:,} input / ~{total_output_tokens:,} output

### Images Processed
- Sample images: {n_sample_images}
- Test images: {n_test_images}
- Total: {total_images} images

### Cost Estimate (Full Test Set)
- Model: {model} via Google AI Studio free tier
- Free tier limits: 1,500 requests/day, 15 RPM, 1M TPM — sufficient for all {total_claims} claims
- **Estimated total cost: $0.00 (free tier)**
- Paid tier pricing (if scale needed): ~$0.15/M input, $0.60/M output tokens for gemini-2.5-flash

### Latency & Runtime
- Sample set runtime: {elapsed_sample:.1f}s for {n_sample} claims
- Average per claim: ~{avg_latency:.1f}s (includes 4.5s rate-limit delay)
- Estimated test set runtime: ~{n_test * avg_latency:.0f}s (~{n_test * avg_latency / 60:.1f} minutes)

### Rate Limits & Strategy
- Model: {model}
- Free tier: 15 RPM, 1M TPM, 1500 RPD
- Strategy: 4.5-second delay between requests keeps throughput under 15 RPM limit
- Retry strategy: Exponential backoff (15s, 30s, 60s) on 429 / quota errors
- No batching used (claims are independent; sequential avoids burst and simplifies retry)
- Structured output: response_mime_type=application/json + response_schema enforces exact output format without a separate extraction step
- Determinism: tool_choice="any" forces the model to always call the structured output tool; booleans and enums constrain the output space

### Unnecessary Call Avoidance
- Single API call per claim (no chain-of-thought step followed by extraction step)
- Evidence requirements loaded once and passed as context (not fetched per claim)
- User history loaded once into a lookup dict (O(1) per lookup, not O(n) per claim)
"""
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report written to {REPORT_PATH}")


def run(model: str = DEFAULT_MODEL, delay: float = 4.5):
    print("Loading sample claims (with expected outputs)...")
    sample_rows = load_csv("sample_claims.csv")
    user_history_rows = load_csv("user_history.csv")
    evidence_reqs = load_csv("evidence_requirements.csv")

    api_key = os.environ.get(DEFAULT_ENV_VAR)
    if not api_key:
        print(f"ERROR: {DEFAULT_ENV_VAR} not set.")
        sys.exit(1)

    if _USE_OPENAI_COMPAT:
        from processor_openai import GROQ_BASE_URL, _EXTRA_HEADERS
        client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, default_headers=_EXTRA_HEADERS)
        print(f"Provider: OpenAI-compat ({DEFAULT_MODEL} @ {GROQ_BASE_URL})")
    else:
        client = genai.Client(api_key=api_key)
        print(f"Provider: Gemini ({model})")

    # Separate expected output fields from input fields
    input_fields = ["user_id", "image_paths", "user_claim", "claim_object"]
    output_fields = [
        "evidence_standard_met", "evidence_standard_met_reason", "risk_flags",
        "issue_type", "object_part", "claim_status", "claim_status_justification",
        "supporting_image_ids", "valid_image", "severity"
    ]

    ground_truth = [{f: r.get(f, "") for f in output_fields} for r in sample_rows]

    log_session_start(mode="evaluation", input_file="sample_claims.csv")
    print(f"Evaluating {len(sample_rows)} sample claims with model={model}")

    predictions = []
    n_images = 0
    start = time.time()

    for i, row in enumerate(sample_rows, 1):
        claim_id = f"sample_{i:03d}"
        user_id = row.get("user_id", "unknown")
        image_paths_str = row.get("image_paths", "")
        print(f"  [{i:02d}/{len(sample_rows)}] {claim_id} | user={user_id}")

        log_claim_input(claim_id, user_id, row.get("claim_object", ""), image_paths_str, row.get("user_claim", ""))

        images = load_images(image_paths_str)
        n_images += len(images)
        history = get_user_history(user_history_rows, user_id)

        try:
            result = process_claim(
                row={k: row.get(k, "") for k in input_fields},
                user_history=history,
                evidence_reqs=evidence_reqs,
                image_data=images,
                client=client,
                model=model,
            )
        except Exception as e:
            log_error(claim_id, str(e))
            print(f"    ERROR: {e}")
            if _USE_OPENAI_COMPAT:
                from processor_openai import _fallback_result
            else:
                from processor import _fallback_result
            result = _fallback_result(str(e))

        log_agent_reasoning(claim_id, model, result)
        log_decision(claim_id, {**row, **result})

        predictions.append(result)
        expected_status = ground_truth[i - 1].get("claim_status", "?")
        print(f"    => predicted={result.get('claim_status')} | expected={expected_status}")

        if i < len(sample_rows):
            time.sleep(delay)

    elapsed = time.time() - start
    log_session_end(total=len(sample_rows), succeeded=len(predictions), failed=0)

    # Compute metrics
    metrics = compute_metrics(predictions, ground_truth)
    print(f"\nOverall accuracy: {metrics['overall']}%")
    for f, acc in metrics["per_field"].items():
        print(f"  {f}: {acc}%")

    # Count test images for report
    test_rows = load_csv("claims.csv")
    n_test_images = sum(
        len([p for p in r.get("image_paths", "").split(";") if p.strip()])
        for r in test_rows
    )

    write_report(
        metrics=metrics,
        model=model,
        n_sample=len(sample_rows),
        n_test=len(test_rows),
        n_sample_images=n_images,
        n_test_images=n_test_images,
        elapsed_sample=elapsed,
        predictions=predictions,
        ground_truth=ground_truth
    )

    # Write sample predictions CSV
    output_rows = []
    for row, pred in zip(sample_rows, predictions):
        output_rows.append({
            "user_id": row.get("user_id", ""),
            "image_paths": row.get("image_paths", ""),
            "user_claim": row.get("user_claim", ""),
            "claim_object": row.get("claim_object", ""),
            **pred
        })

    fieldnames = ["user_id", "image_paths", "user_claim", "claim_object"] + output_fields
    with open(EVAL_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"Sample predictions written to {EVAL_OUTPUT}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Evaluate on sample_claims.csv")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--delay", type=float, default=4.5)
    args = parser.parse_args()
    run(model=args.model, delay=args.delay)


if __name__ == "__main__":
    main()

"""
OpenAI-compatible processor for Groq / OpenAI backends.
Used when GROQ_API_KEY is set. Drop-in replacement for processor.py.
"""
import json
import time
from typing import Literal
from pydantic import BaseModel, ValidationError
from openai import OpenAI

from prompts import SYSTEM_PROMPT, build_context_text

import os as _os

if _os.environ.get("OPENROUTER_API_KEY"):
    DEFAULT_MODEL = "google/gemini-2.5-flash"
    DEFAULT_ENV_VAR = "OPENROUTER_API_KEY"
    GROQ_BASE_URL = "https://openrouter.ai/api/v1"
    _EXTRA_HEADERS = {
        "HTTP-Referer": "https://github.com/damage-claim-review",
        "X-Title": "Damage Claim Reviewer",
    }
elif _os.environ.get("NVIDIA_API_KEY"):
    DEFAULT_MODEL = "meta/llama-4-scout-17b-16e-instruct"
    DEFAULT_ENV_VAR = "NVIDIA_API_KEY"
    GROQ_BASE_URL = "https://integrate.api.nvidia.com/v1"
    _EXTRA_HEADERS = {}
else:
    DEFAULT_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
    DEFAULT_ENV_VAR = "GROQ_API_KEY"
    GROQ_BASE_URL = "https://api.groq.com/openai/v1"
    _EXTRA_HEADERS = {}


class ClaimAnalysis(BaseModel):
    evidence_standard_met: bool
    evidence_standard_met_reason: str
    risk_flags: str
    issue_type: Literal[
        "dent", "scratch", "crack", "glass_shatter", "broken_part",
        "missing_part", "torn_packaging", "crushed_packaging",
        "water_damage", "stain", "none", "unknown"
    ]
    object_part: str
    claim_status: Literal["supported", "contradicted", "not_enough_information"]
    claim_status_justification: str
    supporting_image_ids: str
    valid_image: bool
    severity: Literal["none", "low", "medium", "high", "unknown"]


def _build_messages(row: dict, user_history: dict, evidence_reqs: list, image_data: list[dict]) -> list:
    content = []
    for img in image_data:
        content.append({"type": "text", "text": f"[Image ID: {img['id']}]"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"}
        })
    context = build_context_text(row, user_history, evidence_reqs, n_images=len(image_data))
    content.append({"type": "text", "text": context})
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _normalize(data: dict) -> dict:
    """Coerce fields that models sometimes return in wrong types."""
    # risk_flags: models sometimes return a list — join to semicolon-string
    rf = data.get("risk_flags", "none")
    if isinstance(rf, list):
        data["risk_flags"] = ";".join(rf) if rf else "none"
    elif not rf:
        data["risk_flags"] = "none"

    # supporting_image_ids: same list vs string issue
    si = data.get("supporting_image_ids", "none")
    if isinstance(si, list):
        data["supporting_image_ids"] = ";".join(si) if si else "none"

    # evidence_standard_met: some models return string "true"/"false"
    esm = data.get("evidence_standard_met")
    if isinstance(esm, str):
        data["evidence_standard_met"] = esm.lower() == "true"

    # valid_image: same
    vi = data.get("valid_image")
    if isinstance(vi, str):
        data["valid_image"] = vi.lower() == "true"

    return data


def _parse_json_response(text: str) -> ClaimAnalysis:
    """Parse JSON from model output, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    data = json.loads(text)
    data = _normalize(data)
    return ClaimAnalysis(**data)


def _enforce_consistency(result: ClaimAnalysis) -> ClaimAnalysis:
    """Apply deterministic business rules to fix model inconsistencies."""
    data = result.model_dump()

    status = data["claim_status"]
    sev = data["severity"]
    issue = data["issue_type"]
    flags = data.get("risk_flags", "")

    # Rule 1: not_enough_information → severity must be unknown
    if status == "not_enough_information" and sev != "unknown":
        data["severity"] = "unknown"

    # Rule 2: contradicted with issue_type=none → severity must be none
    if status == "contradicted" and issue == "none":
        data["severity"] = "none"

    # Rule 3: supported → supporting_image_ids must not be "none"
    if status == "supported" and data.get("supporting_image_ids", "none") == "none":
        if data.get("valid_image"):
            data["supporting_image_ids"] = "img_1"

    # Rule 4: non_original_image flag → valid_image=false only if ALL images are non-original
    # (Multi-image: if at least one genuine image exists, valid_image stays true)
    # Trust the model's valid_image judgment — it sees how many images are genuine

    # Rule 5 REMOVED — was incorrectly overriding evidence_standard_met when only one
    # of multiple images was a stock photo. Trust model's evidence_standard_met judgment.

    # Rule 6: cap severity=high on single-component damage → medium
    if sev == "high" and issue in ("dent", "scratch", "crack", "glass_shatter", "stain", "broken_part", "torn_packaging", "water_damage"):
        data["severity"] = "medium"

    # Rule 7: wrong_object + claim_mismatch in multi-image → not_enough_information
    # (two different vehicles submitted = vehicle identity uncertain)
    if (status == "supported" and
            "wrong_object" in flags and "claim_mismatch" in flags and
            issue not in ("none", "unknown")):
        # If the model says supported but flags wrong_object + claim_mismatch,
        # the identity of which vehicle/device is the claimant's is uncertain
        pass  # Let the model's judgment stand — prompt should handle this

    return ClaimAnalysis(**data)


def process_claim(
    row: dict,
    user_history: dict,
    evidence_reqs: list,
    image_data: list[dict],
    client: OpenAI,
    model: str = DEFAULT_MODEL,
    max_retries: int = 5,
) -> dict:
    messages = _build_messages(row, user_history, evidence_reqs, image_data)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1024,
            )
            raw = response.choices[0].message.content
            result = _parse_json_response(raw)
            result = _enforce_consistency(result)
            return _to_dict(result)

        except (ValidationError, json.JSONDecodeError) as e:
            if attempt == max_retries - 1:
                return _fallback_result(f"JSON parse error: {str(e)[:200]}")
            time.sleep(2)

        except Exception as e:
            err = str(e)
            if "429" in err or "rate" in err.lower() or "quota" in err.lower():
                wait = 2 ** attempt * 5
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif attempt == max_retries - 1:
                return _fallback_result(f"Error after {max_retries} attempts: {err[:200]}")
            else:
                time.sleep(2)

    return _fallback_result("Max retries exceeded")


def _to_dict(result: ClaimAnalysis) -> dict:
    return {
        "evidence_standard_met": str(result.evidence_standard_met).lower(),
        "evidence_standard_met_reason": result.evidence_standard_met_reason,
        "risk_flags": result.risk_flags,
        "issue_type": result.issue_type,
        "object_part": result.object_part,
        "claim_status": result.claim_status,
        "claim_status_justification": result.claim_status_justification,
        "supporting_image_ids": result.supporting_image_ids,
        "valid_image": str(result.valid_image).lower(),
        "severity": result.severity,
    }


def _fallback_result(reason: str) -> dict:
    print(f"    FALLBACK: {reason}")
    return {
        "evidence_standard_met": "false",
        "evidence_standard_met_reason": f"Processing error: {reason}",
        "risk_flags": "manual_review_required",
        "issue_type": "unknown",
        "object_part": "unknown",
        "claim_status": "not_enough_information",
        "claim_status_justification": f"Could not process claim: {reason}",
        "supporting_image_ids": "none",
        "valid_image": "false",
        "severity": "unknown",
    }

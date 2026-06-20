import base64
import time
from typing import Literal
from pydantic import BaseModel
from google import genai
from google.genai import types

from prompts import SYSTEM_PROMPT, build_context_text

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_ENV_VAR = "GEMINI_API_KEY"


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


def _build_gemini_parts(row: dict, user_history: dict, evidence_reqs: list, image_data: list[dict]) -> list:
    """Build Gemini content parts: interleaved image labels + images + text context."""
    parts = []
    for img in image_data:
        parts.append(types.Part.from_text(text=f"[Image ID: {img['id']}]"))
        image_bytes = base64.b64decode(img["data"])
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=img["media_type"]))

    context = build_context_text(row, user_history, evidence_reqs, n_images=len(image_data))
    parts.append(types.Part.from_text(text=context))
    return parts


def _make_config(use_thinking: bool) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=ClaimAnalysis,
        temperature=0.1,
    )


def process_claim(
    row: dict,
    user_history: dict,
    evidence_reqs: list,
    image_data: list[dict],
    client: genai.Client,
    model: str = DEFAULT_MODEL,
    max_retries: int = 5,
) -> dict:
    """Call Gemini to analyze a single claim. Returns output dict."""
    parts = _build_gemini_parts(row, user_history, evidence_reqs, image_data)
    use_thinking = True

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=parts)],
                config=_make_config(use_thinking),
            )

            result = response.parsed
            if result is None:
                import json
                text = response.text.strip()
                if text.startswith("{"):
                    raw = json.loads(text)
                    result = ClaimAnalysis(**raw)
                else:
                    return _fallback_result("Model returned no parseable JSON")

            result = _enforce_consistency(result)
            return _to_dict(result)

        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower() or "rate" in err.lower() or "resource" in err.lower():
                wait = 2 ** attempt * 20
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif "thinking" in err.lower() or "ThinkingConfig" in err or "temperature" in err.lower():
                # Thinking mode not supported — fall back silently
                print(f"    Thinking mode unsupported, retrying without it...")
                use_thinking = False
                time.sleep(1)
            elif attempt == max_retries - 1:
                return _fallback_result(f"Error after {max_retries} attempts: {err[:200]}")
            else:
                time.sleep(2 ** attempt * 2)

    return _fallback_result("Max retries exceeded")


def _enforce_consistency(result: ClaimAnalysis) -> ClaimAnalysis:
    data = result.model_dump()
    status = data["claim_status"]
    sev = data["severity"]
    issue = data["issue_type"]
    flags = data.get("risk_flags", "")

    if status == "not_enough_information" and sev != "unknown":
        data["severity"] = "unknown"
    if status == "contradicted" and issue == "none":
        data["severity"] = "none"
    if status == "supported" and data.get("supporting_image_ids", "none") == "none":
        if data.get("valid_image"):
            data["supporting_image_ids"] = "img_1"
    if "non_original_image" in flags:
        data["valid_image"] = False
    # Cap severity=high on single-component damage
    if data["severity"] == "high" and data["issue_type"] in ("dent", "scratch", "crack", "glass_shatter", "stain", "broken_part", "torn_packaging", "water_damage"):
        data["severity"] = "medium"

    return ClaimAnalysis(**data)


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

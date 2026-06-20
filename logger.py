import os
import json
from datetime import datetime
from pathlib import Path


LOG_DIR = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / "hackerrank_orchestrate"
LOG_FILE = LOG_DIR / "log.txt"


def _ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _redact(text: str) -> str:
    """Redact API keys and secrets from log output."""
    import re
    # Redact Anthropic API keys (sk-ant-...)
    text = re.sub(r"sk-ant-[A-Za-z0-9_-]{20,}", "[REDACTED_API_KEY]", text)
    # Redact any Authorization headers
    text = re.sub(r"(Authorization:\s*Bearer\s+)\S+", r"\1[REDACTED]", text)
    return text


def log(level: str, message: str, data: dict = None):
    """Append a log entry to the shared log file."""
    _ensure_log_dir()
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    entry = f"[{timestamp}] [{level.upper()}] {message}"
    if data:
        entry += "\n" + json.dumps(data, indent=2, default=str)
    entry = _redact(entry)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n")


def log_session_start(mode: str, input_file: str):
    log("SESSION", f"=== Session started | mode={mode} | input={input_file} ===")


def log_claim_input(claim_id: str, user_id: str, claim_object: str, image_paths: str, user_claim: str):
    # Extract the core damage claim from the conversation for clarity
    lines = user_claim.split("|")
    customer_lines = [l.strip() for l in lines if "Customer:" in l or "Cliente:" in l or "customer:" in l.lower()]
    extracted = " | ".join(customer_lines[-2:]) if customer_lines else user_claim[:200]
    log("INPUT", f"Claim {claim_id} | user={user_id} | object={claim_object} | images={image_paths}",
        {
            "image_count": len([p for p in image_paths.split(";") if p.strip()]),
            "extracted_customer_claim": extracted[:400]
        })


def log_agent_reasoning(claim_id: str, model: str, result: dict):
    log("REASONING", f"Claim {claim_id} | model={model}", {
        "visual_analysis": result.get("claim_status_justification", "")[:800],
        "evidence_met": result.get("evidence_standard_met"),
        "risk_flags": result.get("risk_flags"),
        "issue_identified": result.get("issue_type"),
        "part_identified": result.get("object_part"),
    })


def log_decision(claim_id: str, result: dict):
    summary = {k: v for k, v in result.items() if k not in ("image_paths", "user_claim")}
    log("DECISION", f"Claim {claim_id} | status={result.get('claim_status')} | severity={result.get('severity')}",
        summary)


def log_error(claim_id: str, error: str):
    log("ERROR", f"Claim {claim_id} | {error}")


def log_session_end(total: int, succeeded: int, failed: int):
    log("SESSION", f"=== Session ended | total={total} | succeeded={succeeded} | failed={failed} ===")

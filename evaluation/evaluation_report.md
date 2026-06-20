# Evaluation Report — Multi-Modal Evidence Review
Generated: 2026-06-19 16:32 UTC
Model: google/gemini-2.5-flash (via OpenRouter)

## Accuracy on Sample Set (n=20)

| Field | Accuracy |
|-------|----------|
| claim_status | 75.0% |
| issue_type | 65.0% |
| object_part | 85.0% |
| severity | 65.0% |
| evidence_standard_met | 75.0% |
| valid_image | 75.0% |
| **Overall Average** | **73.3%** |

## Per-Row Sample Results

| # | User | Object | Expected Status | Predicted Status | Mismatches |
|---|------|--------|-----------------|------------------|------------|
| 01 | user_001 | car | supported | supported | all match |
| 02 | user_002 | car | not_enough_information | supported | claim_status, issue_type, severity, evidence_standard_met |
| 03 | user_004 | car | supported | supported | all match |
| 04 | user_007 | car | supported | supported | issue_type(got=glass_shatter, exp=broken_part) |
| 05 | user_005 | car | contradicted | supported | claim_status, issue_type, severity |
| 06 | user_006 | car | not_enough_information | not_enough_information | valid_image(got=false, exp=true) |
| 07 | user_003 | car | supported | supported | all match |
| 08 | user_008 | car | contradicted | not_enough_information | claim_status, issue_type, object_part, severity, evidence_standard_met |
| 09 | user_009 | laptop | supported | supported | issue_type(got=glass_shatter, exp=crack), severity(got=high, exp=medium) |
| 10 | user_010 | laptop | supported | supported | evidence_standard_met(got=false, exp=true), valid_image(got=false, exp=true) |
| 11 | user_011 | laptop | supported | supported | all match |
| 12 | user_012 | laptop | supported | supported | all match |
| 13 | user_018 | laptop | supported | supported | all match |
| 14 | user_020 | laptop | contradicted | supported | claim_status, issue_type, object_part, severity |
| 15 | user_015 | package | supported | supported | all match |
| 16 | user_030 | package | supported | supported | evidence_standard_met(got=false, exp=true), valid_image(got=false, exp=true) |
| 17 | user_031 | package | supported | supported | object_part(got=box, exp=package_side) |
| 18 | user_032 | package | not_enough_information | not_enough_information | valid_image(got=true, exp=false) |
| 19 | user_033 | package | contradicted | contradicted | severity(got=unknown, exp=low) |
| 20 | user_034 | package | contradicted | supported | claim_status, issue_type, severity, evidence_standard_met, valid_image |

## Operational Analysis

### Model Calls
- Sample processing: 20 calls
- Test processing: 44 calls
- Total calls: 64 calls
- API calls per claim: 1 (single-pass with structured output)

### Token Usage (estimated)
- Average input tokens per claim: ~4,500 (text ~800 + image tokens ~3,700 per image avg)
- Average output tokens per claim: ~400
- Grand total: ~288,000 input / ~25,600 output

### Images Processed
- Sample images: 29
- Test images: 82
- Total: 111 images

### Cost Estimate
- Model: google/gemini-2.5-flash via OpenRouter
- Estimated total cost: ~$0.06 for 64 claims / 111 images

### Runtime
- Sample set: ~123s for 20 claims (3s inter-request delay)
- Test set: ~272s for 44 claims (~4.5 minutes)

### Rate Limit Strategy
- 3-second delay between requests (stays under 20 RPM)
- Exponential backoff: 5s → 10s → 20s → 40s → 80s on 429 errors
- Up to 5 retries per claim
- Structured JSON output enforced via response_format + Pydantic validation

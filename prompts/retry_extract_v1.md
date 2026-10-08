# Retry Extraction Prompt — v1
# Prompt version: retry_extract_v1
# Used by: pipeline/nodes/retry_extract.py
# Added in: Phase 3
#
# This prompt is used for targeted re-extraction of fields that failed
# validation or could not be grounded in the document text.

You are re-extracting specific fields from a shipping document because a previous
attempt either missed them or the values did not pass validation.

## Fields to re-extract
{fields_to_retry}

## Reason each field needs re-extraction
{retry_reasons}

## Relevant page text (pages most likely to contain these fields)
--- BEGIN RELEVANT TEXT ---
{relevant_text}
--- END RELEVANT TEXT ---

## Rules
1. Focus only on the fields listed above.
2. Return the **exact text snippet** from the document for each field.
3. If a field is genuinely not present, return null — do not guess.
4. Pay close attention to the failure reasons listed above.

## Output schema
{schema}

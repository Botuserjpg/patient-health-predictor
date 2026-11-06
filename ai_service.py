"""OpenAI-compatible client for short, non-diagnostic screening remarks."""

import logging
import os
import re
from datetime import date, datetime

import requests

DISCLAIMER = "Informational only; not medical advice."
FALLBACK = "Prediction unavailable. Please consult a doctor for interpretation. " + DISCLAIMER
AUTH_FALLBACK = (
    "AI prediction unavailable: the provider rejected the API key. Check AI_API_KEY in .env, then restart the app and rerun the prediction. "
    + DISCLAIMER
)
REQUEST_FALLBACK = (
    "AI prediction unavailable: the provider rejected this request. Check AI_BASE_URL and AI_MODEL in .env, then restart the app and rerun the prediction. "
    + DISCLAIMER
)
logger = logging.getLogger(__name__)


def predict_health_risk(patient: dict) -> str:
    """Return a validated short remark; provider trouble never blocks a record."""
    required = {
        "AI_BASE_URL": os.getenv("AI_BASE_URL", "").strip(),
        "AI_MODEL": os.getenv("AI_MODEL", "").strip(),
        "AI_API_KEY": os.getenv("AI_API_KEY", "").strip(),
    }
    missing = [
        key
        for key, value in required.items()
        if not value or value.upper().startswith(("YOUR_", "REPLACE_", "<"))
    ]
    if missing:
        for key in missing:
            logger.warning("AI configuration missing: %s", key)
        return (
            "AI prediction unavailable. Add the missing setting(s) to your local .env and restart the app: "
            + ", ".join(missing)
            + ". Please consult a doctor. "
            + DISCLAIMER
        )
    dob = patient["dob"]
    birth = datetime.strptime(dob, "%d/%m/%Y").date()
    today = date.today()
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    prompt = (
        "Write a structured, non-diagnostic screening remark in at most 40 words. Include possible condition or risk, exactly one risk level (Low, Moderate, High), and a one-line suggestion to consult a doctor. Use cautious language. "
        f"Age: {age}. Fasting glucose: {patient['glucose']} mg/dL (70-99 normal, 100-125 pre-diabetes, 126+ diabetes range); "
        f"haemoglobin: {patient['haemoglobin']} g/dL (about 12-17.5 normal); total cholesterol: {patient['cholesterol']} mg/dL (under 200 desirable, 200-239 borderline, 240+ high)."
    )
    payload = {
        "model": required["AI_MODEL"],
        "messages": [
            {"role": "system", "content": "Provide cautious health education, never a diagnosis."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if "api.groq.com" in required["AI_BASE_URL"] and required["AI_MODEL"].startswith(
        "openai/gpt-oss-"
    ):
        payload.update(
            {"max_completion_tokens": 512, "include_reasoning": False, "reasoning_effort": "low"}
        )
    else:
        payload["max_tokens"] = 140

    for attempt in range(2):
        try:
            response = requests.post(
                required["AI_BASE_URL"],
                headers={
                    "Authorization": f"Bearer {required['AI_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("AI response content must be text")
            content = re.sub(r"\s+", " ", content).strip()
            if (
                len(content.split()) + len(DISCLAIMER.split()) > 40
                or not re.search(r"\b(Low|Moderate|High)\b", content, re.I)
                or not re.search(
                    r"\b(doctor|physician|clinician|healthcare provider)\b", content, re.I
                )
            ):
                raise ValueError("AI response did not match required structure")
            return f"{content} {DISCLAIMER}"
        except requests.HTTPError as exc:
            response = exc.response
            status = response.status_code if response is not None else 0
            try:
                detail = str(response.json()).lower() if response is not None else ""
            except (ValueError, TypeError):
                detail = ""
            if (
                status in (401, 403)
                or "invalid auth key" in detail
                or "invalid api key" in detail
                or "api_key_invalid" in detail
            ):
                logger.error("AI provider rejected the configured API key (HTTP %s)", status)
                return AUTH_FALLBACK
            if 400 <= status < 500 and status not in (408, 429):
                logger.error("AI provider rejected the request (HTTP %s); skipping retry", status)
                return REQUEST_FALLBACK
            if attempt == 0:
                logger.warning("AI provider request failed; retrying once (HTTP %s)", status)
            else:
                logger.error("AI provider failed after retry (HTTP %s)", status)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
            if attempt == 0:
                logger.warning("AI provider request failed (%s); retrying once", type(exc).__name__)
            else:
                logger.error("AI provider failed after retry (%s)", type(exc).__name__)
    return FALLBACK

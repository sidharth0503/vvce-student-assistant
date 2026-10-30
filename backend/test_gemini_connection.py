"""One-shot diagnostic for Gemini API key, model availability, and generation."""

from __future__ import annotations

import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = (20, 20)  # connect timeout, read timeout
PROMPT = "Reply with exactly: Gemini connection successful"


def _classify(status: int | None, provider_status: str = "", message: str = "") -> str:
    provider_status = provider_status.upper()
    text = message.lower()
    if status in (401, 403) or provider_status in {"UNAUTHENTICATED", "PERMISSION_DENIED"} or any(
        term in text for term in ("api key", "invalid credential", "authentication")
    ):
        return "authentication error"
    if status == 404 or provider_status in {"NOT_FOUND", "MODEL_NOT_FOUND"} or (
        "model" in text and any(term in text for term in ("not found", "does not exist", "unsupported"))
    ):
        return "invalid or unavailable model"
    if status == 429 or provider_status in {"RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED"} or any(
        term in text for term in ("quota", "rate limit", "resource exhausted")
    ):
        return "quota or rate-limit error"
    if status is not None and status >= 500:
        return "temporary service error"
    return "request error"


def _safe_error(response: requests.Response, api_key: str) -> tuple[str, str]:
    """Extract only the short provider error category/details; redact credentials and URLs."""
    try:
        payload = response.json()
    except ValueError:
        return "", f"HTTP {response.status_code} (non-JSON error response)"
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return "", f"HTTP {response.status_code} (unrecognized error response)"
    provider_status = error.get("status", "")
    message = error.get("message", "")
    if not isinstance(provider_status, str):
        provider_status = ""
    if not isinstance(message, str):
        message = ""
    message = message.replace(api_key, "[redacted]")
    message = re.sub(r"https?://\S+", "[URL redacted]", message)
    message = re.sub(r"([?&](?:key|token|access_token)=)[^&\s]+", r"\1[redacted]", message, flags=re.I)
    message = re.sub(r"[\r\n\t]+", " ", message).strip()[:240]
    return provider_status, message or f"HTTP {response.status_code}"


def _request_failure(exc: requests.RequestException) -> str:
    # Do not print exception text: it can contain the complete URL and API key query parameter.
    if isinstance(exc, requests.ConnectTimeout):
        return "connection timeout (20 seconds)"
    if isinstance(exc, requests.ReadTimeout):
        return "read timeout (20 seconds)"
    if isinstance(exc, requests.Timeout):
        return "network timeout (20 seconds)"
    if isinstance(exc, requests.ConnectionError):
        return "network connection failure"
    return f"network/request failure ({type(exc).__name__})"


def _timed_request(method: str, url: str, **kwargs):
    started = time.perf_counter()
    try:
        response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
        return response, time.perf_counter() - started, None
    except requests.RequestException as exc:
        return None, time.perf_counter() - started, _request_failure(exc)


def _model_records(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return []
    records = []
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        methods = item.get("supportedGenerationMethods", [])
        if isinstance(name, str):
            records.append({
                "name": name,
                "methods": [method for method in methods if isinstance(method, str)] if isinstance(methods, list) else [],
            })
    return records


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    import os

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    configured_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()
    model_path = configured_model.removeprefix("models/")

    print("Gemini connection diagnostic")
    print("----------------------------")
    print(f"API key present: {'yes' if api_key else 'no'} (value hidden)")
    print(f"Configured model: {configured_model or '(empty)'}")
    print("Timeouts: 20s connect / 20s read; no retries")

    if not api_key:
        print("\nConfiguration result: GEMINI_API_KEY is missing from backend/.env.")
        print("No API requests were sent.")
        return 1
    if not model_path:
        print("\nConfiguration result: GEMINI_MODEL is empty.")
        return 1

    models_url = f"{API_ROOT}/models"
    models_response, models_elapsed, models_failure = _timed_request(
        "GET", models_url, params={"key": api_key}
    )
    records: list[dict] = []
    compatible: list[str] = []
    print("\nModels listing")
    if models_failure:
        print(f"  HTTP status: unavailable; elapsed: {models_elapsed:.2f}s")
        print(f"  Result: {models_failure}")
    else:
        print(f"  HTTP status: {models_response.status_code}; elapsed: {models_elapsed:.2f}s")
        if models_response.ok:
            try:
                records = _model_records(models_response.json())
            except ValueError:
                records = []
            if records:
                print("  Available models and supported generation methods:")
                for record in records:
                    methods = ", ".join(record["methods"]) or "(none listed)"
                    print(f"    {record['name']} — {methods}")
                compatible = [record["name"] for record in records
                              if "generateContent" in record["methods"]]
                configured_entry = next(
                    (record for record in records
                     if record["name"].removeprefix("models/") == model_path), None
                )
                if configured_entry is None:
                    print(f"  Configured model exists: no ({configured_model})")
                else:
                    supports = "generateContent" in configured_entry["methods"]
                    print(f"  Configured model exists: yes; supports generateContent: {'yes' if supports else 'no'}")
            else:
                print("  Result: response did not contain a readable models list.")
        else:
            provider_status, detail = _safe_error(models_response, api_key)
            print(f"  Error type: {_classify(models_response.status_code, provider_status, detail)}")
            print(f"  Error details: {detail}")

    print("\nMinimal generation request")
    print(f"  Model identifier: {configured_model}")
    generation_url = f"{API_ROOT}/models/{model_path}:generateContent"
    generation_response, generation_elapsed, generation_failure = _timed_request(
        "POST", generation_url,
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": PROMPT}]}]},
    )
    print(f"  HTTP status: {generation_response.status_code if generation_response is not None else 'unavailable'}; "
          f"elapsed: {generation_elapsed:.2f}s")
    generation_success = False
    if generation_failure:
        category = "timeout" if "timeout" in generation_failure else "network failure"
        print(f"  Error type: {category}")
        print(f"  Error details: {generation_failure}")
    elif generation_response.ok:
        try:
            payload = generation_response.json()
        except ValueError:
            payload = None
        candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
        texts = []
        if isinstance(candidates, list):
            for candidate in candidates:
                content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
                parts = content.get("parts", []) if isinstance(content, dict) else []
                if isinstance(parts, list):
                    texts.extend(part["text"] for part in parts
                                 if isinstance(part, dict) and isinstance(part.get("text"), str))
        generation_success = bool(texts)
        if generation_success:
            print("  Generation success: yes")
            print(f"  Response: {' '.join(texts).strip()[:300]}")
        else:
            block_reason = payload.get("promptFeedback", {}).get("blockReason") if isinstance(payload, dict) else None
            if block_reason:
                print(f"  Generation success: no (blocked: {str(block_reason)[:80]})")
            else:
                print("  Generation success: no (empty or malformed response)")
    else:
        provider_status, detail = _safe_error(generation_response, api_key)
        print(f"  Error type: {_classify(generation_response.status_code, provider_status, detail)}")
        print(f"  Error details: {detail}")

    configured_available = any(name.removeprefix("models/") == model_path for name in compatible)
    if records and not configured_available:
        print("\nAvailable models that support generateContent:")
        if compatible:
            for name in compatible:
                print(f"  {name}")
        else:
            print("  None were reported by the API.")
        print("No model was selected or substituted automatically.")

    print("\nDiagnostic summary")
    print(f"  Configured model: {configured_model}")
    print(f"  Generation successful: {'yes' if generation_success else 'no'}")
    print(f"  Models-list request: {'completed' if models_response is not None else models_failure}")
    return 0 if generation_success else 1


if __name__ == "__main__":
    raise SystemExit(main())

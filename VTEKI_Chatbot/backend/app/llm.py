"""Pembungkus Gemini: keluaran JSON, model cadangan otomatis, dan error yang jelas."""
from __future__ import annotations

import json
import logging
import re
import time

from .config import GEMINI_API_KEY, GEMINI_MODELS

log = logging.getLogger("vteki.llm")

TEMP_ERRORS = ("503", "429", "500", "502", "504", "unavailable", "resource_exhausted", "timeout", "deadline", "overloaded", "internal")


class LLMError(RuntimeError):
    def __init__(self, message: str, kind: str = "unavailable"):
        super().__init__(message)
        self.kind = kind  # not_configured | auth | unavailable | bad_output


_client = None


def _get_client():
    global _client
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY belum diisi. Periksa file backend/.env.", "not_configured")
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def is_configured() -> bool:
    return bool(GEMINI_API_KEY)


def _config(model: str, system: str, temperature: float, max_tokens: int):
    from google.genai import types
    cfg = dict(temperature=temperature, max_output_tokens=max_tokens, system_instruction=system,
               response_mime_type="application/json")
    # Model 2.5 Flash memakai token "thinking" yang bisa menghabiskan batas output -> jawaban kosong.
    if model.startswith("gemini-2.5-flash"):
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(**cfg)


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError("Gemini tidak mengembalikan JSON.", "bad_output")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("JSON dari Gemini tidak valid.", "bad_output") from exc
    if not isinstance(data, dict):
        raise LLMError("Format JSON dari Gemini tidak sesuai.", "bad_output")
    return data


def generate_json(system: str, prompt: str, *, temperature: float = 0.1, max_tokens: int = 1024) -> dict:
    client = _get_client()
    last_error = None
    for model in GEMINI_MODELS:  # model utama dulu, lalu cadangan
        for attempt in range(2):
            try:
                resp = client.models.generate_content(model=model, contents=prompt,
                                                      config=_config(model, system, temperature, max_tokens))
                return parse_json(resp.text or "")
            except LLMError as exc:
                last_error = exc
                log.warning("Output Gemini tidak valid (model=%s): %s", model, exc)
                break
            except Exception as exc:
                last_error = exc
                text = str(exc).lower()
                log.warning("Gemini gagal (model=%s, percobaan=%s): %s: %s", model, attempt + 1, type(exc).__name__, str(exc)[:300])
                if any(k in text for k in ("api key not valid", "api_key_invalid", "permission_denied")):
                    raise LLMError("API key Gemini ditolak. Periksa GEMINI_API_KEY di backend/.env.", "auth") from exc
                if "404" in text or "not found" in text:
                    break  # nama model salah/tidak tersedia -> coba model cadangan
                if not any(k in text for k in TEMP_ERRORS):
                    break
                time.sleep(2 * (attempt + 1))
    log.error("Semua model Gemini gagal. Terakhir: %s", last_error)
    raise LLMError("Gemini sedang tidak bisa memproses pesan (kuota habis atau layanan sibuk).", "unavailable")

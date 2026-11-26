"""Adapter LLM multi-penyedia: keluaran JSON, rantai cadangan antar-penyedia, error yang jelas.

Penyedia dipilih lewat .env (tanpa mengubah kode):
  LLM_PROVIDER=gemini            # gemini (default, gratis) | anthropic | openai | ollama
  LLM_FALLBACK_PROVIDERS=ollama  # dicoba berurutan bila penyedia utama gagal/kuota habis

- gemini    : SDK google-genai (sudah ada), model GEMINI_MODEL + GEMINI_FALLBACK_MODELS.
- anthropic : SDK resmi `anthropic` (pip install anthropic), model ANTHROPIC_MODEL (default claude-opus-5-5).
- openai    : HTTP ke /v1/chat/completions, model OPENAI_MODEL (wajib diisi).
- ollama    : HTTP ke OLLAMA_URL/api/chat (lokal, default model llama3) — cadangan sesuai spesifikasi.

Semua penyedia menerima `system` + `prompt` dan WAJIB mengembalikan satu objek JSON.
"""
from __future__ import annotations

import json
import logging
import re
import time

from .config import (ANTHROPIC_API_KEY, ANTHROPIC_MODEL, GEMINI_API_KEY, GEMINI_MODELS, LLM_FALLBACK_PROVIDERS,
                     LLM_PROVIDER, OLLAMA_MODEL, OLLAMA_URL, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL)

log = logging.getLogger("vteki.llm")

TEMP_ERRORS = ("503", "429", "500", "502", "504", "unavailable", "resource_exhausted", "timeout", "deadline", "overloaded", "internal")
PROVIDERS = ("gemini", "anthropic", "openai", "ollama")


class LLMError(RuntimeError):
    def __init__(self, message: str, kind: str = "unavailable"):
        super().__init__(message)
        self.kind = kind  # not_configured | auth | unavailable | bad_output


def provider_chain() -> list[str]:
    chain = [LLM_PROVIDER] + [p for p in LLM_FALLBACK_PROVIDERS if p != LLM_PROVIDER]
    return [p for p in chain if p in PROVIDERS]


def provider_configured(name: str) -> bool:
    if name == "gemini":
        return bool(GEMINI_API_KEY)
    if name == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    if name == "openai":
        return bool(OPENAI_API_KEY and OPENAI_MODEL)
    if name == "ollama":
        return bool(OLLAMA_URL and OLLAMA_MODEL)
    return False


def is_configured() -> bool:
    return any(provider_configured(p) for p in provider_chain())


def active_model() -> str:
    for p in provider_chain():
        if provider_configured(p):
            return {"gemini": GEMINI_MODELS[0], "anthropic": ANTHROPIC_MODEL, "openai": OPENAI_MODEL,
                    "ollama": f"ollama/{OLLAMA_MODEL}"}[p]
    return GEMINI_MODELS[0]


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError("LLM tidak mengembalikan JSON.", "bad_output")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("JSON dari LLM tidak valid.", "bad_output") from exc
    if not isinstance(data, dict):
        raise LLMError("Format JSON dari LLM tidak sesuai.", "bad_output")
    return data


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
_gemini_client = None


def _gemini(system: str, prompt: str, temperature: float, max_tokens: int) -> dict:
    global _gemini_client
    from google.genai import types
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    last_error = None
    for model in GEMINI_MODELS:  # model utama dulu, lalu cadangan
        for attempt in range(2):
            cfg = dict(temperature=temperature, max_output_tokens=max_tokens, system_instruction=system,
                       response_mime_type="application/json")
            # 2.5 Flash memakai token "thinking" yang bisa menghabiskan batas output -> jawaban kosong.
            if model.startswith("gemini-2.5-flash"):
                cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            try:
                resp = _gemini_client.models.generate_content(model=model, contents=prompt,
                                                              config=types.GenerateContentConfig(**cfg))
                return parse_json(resp.text or "")
            except LLMError as exc:
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                text = str(exc).lower()
                log.warning("Gemini gagal (model=%s, percobaan=%s): %s: %s", model, attempt + 1, type(exc).__name__, str(exc)[:300])
                if any(k in text for k in ("api key not valid", "api_key_invalid", "permission_denied")):
                    raise LLMError("API key Gemini ditolak. Periksa GEMINI_API_KEY di backend/.env.", "auth") from exc
                if "404" in text or "not found" in text or not any(k in text for k in TEMP_ERRORS):
                    break
                time.sleep(2 * (attempt + 1))
    raise LLMError(f"Gemini tidak bisa memproses pesan: {last_error}", "unavailable")


# ---------------------------------------------------------------------------
# Anthropic (SDK resmi)
# ---------------------------------------------------------------------------
_anthropic_client = None


def _anthropic(system: str, prompt: str, temperature: float, max_tokens: int) -> dict:
    global _anthropic_client
    try:
        import anthropic
    except ImportError as exc:
        raise LLMError("Paket `anthropic` belum terpasang. Jalankan: pip install anthropic", "not_configured") from exc
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    # Model Claude terbaru selalu berpikir (adaptive); token berpikir ikut dihitung di max_tokens,
    # jadi batasnya diberi ruang. Kedalaman diatur lewat effort (klasifikasi = low).
    # `temperature` tidak dikirim: ditolak (400) oleh model Claude terbaru.
    effort = "low" if max_tokens <= 300 else "medium"
    try:
        resp = _anthropic_client.beta.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max(max_tokens * 4, 4096),
            system=system + "\n\nBalas HANYA dengan satu objek JSON yang valid, tanpa teks lain.",
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # bila model menolak (refusal), API mencoba model cadangan otomatis
        )
    except anthropic.AuthenticationError as exc:
        raise LLMError("API key Anthropic ditolak. Periksa ANTHROPIC_API_KEY.", "auth") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Batas kuota Anthropic tercapai.", "unavailable") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic error {exc.status_code}: {exc.message}", "unavailable") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Tidak dapat terhubung ke Anthropic.", "unavailable") from exc
    if resp.stop_reason == "refusal":
        raise LLMError("Model menolak memproses permintaan ini.", "bad_output")
    text = "".join(b.text for b in resp.content if b.type == "text")
    if resp.stop_reason == "max_tokens" and not text.strip():
        raise LLMError("Jawaban Anthropic terpotong (max_tokens).", "bad_output")
    return parse_json(text)


# ---------------------------------------------------------------------------
# OpenAI & Ollama (HTTP)
# ---------------------------------------------------------------------------
def _http_post(url: str, payload: dict, headers: dict | None = None, timeout: float = 60) -> dict:
    import httpx
    try:
        r = httpx.post(url, json=payload, headers=headers or {}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise LLMError(f"Tidak dapat terhubung ke {url.split('/')[2]}: {exc}", "unavailable") from exc
    if r.status_code in (401, 403):
        raise LLMError("API key ditolak penyedia LLM.", "auth")
    if r.status_code >= 400:
        raise LLMError(f"Penyedia LLM error {r.status_code}: {r.text[:200]}", "unavailable")
    return r.json()


def _openai(system: str, prompt: str, temperature: float, max_tokens: int) -> dict:
    data = _http_post(f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
                      {"model": OPENAI_MODEL, "temperature": temperature, "max_tokens": max_tokens,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
                      headers={"Authorization": f"Bearer {OPENAI_API_KEY}"})
    return parse_json(data["choices"][0]["message"]["content"])


def _ollama(system: str, prompt: str, temperature: float, max_tokens: int) -> dict:
    data = _http_post(f"{OLLAMA_URL.rstrip('/')}/api/chat",
                      {"model": OLLAMA_MODEL, "stream": False, "format": "json",
                       "options": {"temperature": temperature, "num_predict": max_tokens},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
                      timeout=120)
    return parse_json(data.get("message", {}).get("content", ""))


_CALL = {"gemini": _gemini, "anthropic": _anthropic, "openai": _openai, "ollama": _ollama}


def generate_json(system: str, prompt: str, *, temperature: float = 0.1, max_tokens: int = 1024) -> dict:
    """Panggil penyedia utama; bila gagal, coba penyedia cadangan berurutan."""
    chain = [p for p in provider_chain() if provider_configured(p)]
    if not chain:
        raise LLMError("Belum ada penyedia LLM yang dikonfigurasi. Isi GEMINI_API_KEY (atau penyedia lain) di backend/.env.",
                       "not_configured")
    last = None
    for name in chain:
        try:
            return _CALL[name](system, prompt, temperature, max_tokens)
        except LLMError as exc:
            last = exc
            log.warning("Penyedia LLM %s gagal (%s): %s", name, exc.kind, exc)
            if exc.kind == "auth" and len(chain) == 1:
                raise
    log.error("Semua penyedia LLM gagal. Terakhir: %s", last)
    if last and last.kind == "auth":
        raise last
    raise LLMError("Asisten AI sedang tidak bisa memproses pesan (kuota habis atau layanan sibuk).", "unavailable")

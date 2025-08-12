# services/tts.py
# -*- coding: utf-8 -*-
import os
import time
import requests

ELEVEN_BASE   = os.getenv("ELEVEN_API_BASE", "https://api.elevenlabs.io/v1/text-to-speech")
TIMEOUT_S     = float(os.getenv("ELEVEN_TIMEOUT", "60"))
MAX_RETRIES   = int(os.getenv("ELEVEN_RETRIES", "3"))
BACKOFF_S     = float(os.getenv("ELEVEN_BACKOFF", "1.5"))
# حدود آمنة للنص (ElevenLabs عادةً يتحمل ~5000 حرف). نخليها قابلة للتغيير:
MAX_TTS_CHARS = int(os.getenv("ELEVEN_MAX_CHARS", "4500"))

def _clean_text(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 3] + "..."

def synth_eleven(api_key: str, voice_id: str, text: str) -> bytes:
    if not api_key or not voice_id:
        # نعيد مقطع ogg بسيط جدًا فارغ لتجنّب الكراش — أو بإمكانك ترجيع None و التعامل من caller
        # هنا بنرجّع رسالة نصية في TG بدل الصوت عندما يكون config ناقص (شوف tg_bot.py).
        raise ValueError("ElevenLabs API key/voice_id غير مضبوطين.")

    url = f"{ELEVEN_BASE}/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/ogg",        # تيليجرام voice
        "content-type": "application/json",
    }
    payload = {
        "text": _clean_text(text, MAX_TTS_CHARS),
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.7},
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_S)
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"{r.status_code} {r.text[:200]}"
                time.sleep(BACKOFF_S * attempt)
                continue
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(BACKOFF_S * attempt)

    raise RuntimeError(f"تعذّر توليد الصوت حاليًا. (تفاصيل: {last_err})")

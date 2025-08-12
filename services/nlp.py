# services/nlp.py
# -*- coding: utf-8 -*-
import os
import time
import requests
from typing import List, Dict, Any

OPENAI_BASE  = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1/chat/completions")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TIMEOUT_S    = float(os.getenv("OPENAI_TIMEOUT", "45"))
MAX_RETRIES  = int(os.getenv("OPENAI_RETRIES", "3"))
BACKOFF_S    = float(os.getenv("OPENAI_BACKOFF", "1.5"))
MAX_MSGS     = int(os.getenv("OPENAI_MAX_MSGS", "24"))   # آخر كم رسالة من التاريخ
MAX_USER_LEN = int(os.getenv("OPENAI_MAX_USER_LEN", "4000"))  # لحماية من النصوص الطويلة

def _shorten_text(txt: str, limit: int) -> str:
    txt = (txt or "").strip()
    if len(txt) <= limit:
        return txt
    # قصّ ذكي مع نقاط
    return txt[: limit - 1_0].rsplit(" ", 1)[0][: limit - 3] + "..."

def _clip_history(hist: List[Dict[str, Any]], max_msgs: int) -> List[Dict[str, Any]]:
    if not hist:
        return []
    # نتأكد أنه فورمات [{role, content}]
    clean = [m for m in hist if isinstance(m, dict) and "role" in m and "content" in m]
    return clean[-max_msgs:]

def generate_reply(openai_key: str, system_prompt: str, history: List[Dict[str, str]], user_text: str) -> str:
    if not openai_key:
        return "لم يتم ضبط مفتاح OpenAI. حدِّث الإعدادات ثم أعد المحاولة."
    system_prompt = (system_prompt or "").strip()
    user_text = _shorten_text(user_text or "", MAX_USER_LEN)
    msgs = [{"role": "system", "content": system_prompt}]
    msgs += _clip_history(history or [], MAX_MSGS)
    msgs += [{"role": "user", "content": user_text}]

    headers = {
        "Authorization": f"Bearer {openai_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "messages": msgs,
        "temperature": 0.7,
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(OPENAI_BASE, json=payload, headers=headers, timeout=TIMEOUT_S)
            if r.status_code in (429, 500, 502, 503, 504):
                # تهديء ثم إعادة محاولة
                last_err = f"{r.status_code} {r.text[:200]}"
                time.sleep(BACKOFF_S * attempt)
                continue
            r.raise_for_status()
            data = r.json()
            text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            if not text:
                text = "لم أتلقَّ ردًا صالحًا من نموذج الذكاء. جرّب لاحقًا."
            return text
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(BACKOFF_S * attempt)

    return f"تعذّر توليد الرد الآن. (تفاصيل: {last_err})"

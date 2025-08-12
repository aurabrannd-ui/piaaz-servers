# services/nlp.py
# -*- coding: utf-8 -*-
import time
import requests

OPENAI_BASE  = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"   # غيّره هنا لو أردت موديل آخر من OpenAI

def _post_openai(openai_key: str, data: dict) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {openai_key}",
        "Content-Type": "application/json"
    }
    return requests.post(OPENAI_BASE, json=data, headers=headers, timeout=45)

def generate_reply(openai_key: str, system_prompt: str, history: list, user_text: str) -> str:
    """
    يولّد رد باستخدام OpenAI مع 4 محاولات تلقائية عند 429/5xx
    - يقلّص السياق ويطلب ردًا أقصر إذا رجعنا نحاول
    """
    # الرسالة الأساسية
    base_messages = [{"role": "system", "content": system_prompt}] + history + [
        {"role": "user", "content": user_text}
    ]

    # إعدادات أساسية
    base_payload = {
        "model": OPENAI_MODEL,
        "messages": base_messages,
        "temperature": 0.7,
    }

    # محاولات مع backoff تصاعدي
    backoffs = [0.6, 1.2, 2.5, 5.0]  # ثواني
    for attempt in range(len(backoffs)):
        payload = dict(base_payload)  # نسخة لكل محاولة

        # عند إعادة المحاولة: قصّر الرد و السياق
        if attempt > 0:
            # قلل الميموري: خذ آخر 8 تبادلات فقط
            trimmed_hist = history[-16:] if history else []
            payload["messages"] = [{"role": "system", "content": system_prompt}] + trimmed_hist + [
                {"role": "user", "content": user_text + "\n\n(رجاءً رد موجز ومباشر بحد 3 أسطر.)"}
            ]
            payload["temperature"] = 0.5
            payload["max_tokens"] = 180  # رد أقصر لتقليل التوكنز

        try:
            r = _post_openai(openai_key, payload)
            # 429/5xx → جرّب مرة ثانية
            if r.status_code in (429, 500, 502, 503, 504):
                # آخر محاولة؟ ارجع برسالة مفهومة
                if attempt == len(backoffs) - 1:
                    try:
                        detail = r.json()
                    except Exception:
                        detail = {"status": r.status_code}
                    return f"تعذّر توليد الرد الآن. الرجاء المحاولة بعد قليل. (تفاصيل: {detail})"
                time.sleep(backoffs[attempt])
                continue

            r.raise_for_status()
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip()

        except requests.RequestException as e:
            # لو مشكلة شبكة/مهلة: أعد المحاولة، وإلا أرجع رسالة لطيفة
            if attempt == len(backoffs) - 1:
                return "صار تعذّر مؤقت أثناء توليد الرد، جرّب كمان شوي."
            time.sleep(backoffs[attempt])

    # احتياط (ما لازم نوصلها)
    return "حدث خطأ غير متوقع، حاول مرة أخرى."

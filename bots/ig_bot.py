# bots/ig_bot.py
# -*- coding: utf-8 -*-
import requests
from typing import Dict, Any, Optional
from services.nlp import generate_reply

GRAPH = "https://graph.facebook.com/v19.0"

class InstagramDMClientBot:
    """
    بوت رسائل Instagram (حساب Business/Creator موصول بصفحة)
    - يرد نصياً (صوت ممكن لاحقاً عبر روابط ملفات مستضافة)
    """
    def __init__(self, bot_id: str, page_id: str, ig_user_id: str, page_access_token: str, openai_key: str, profile: dict):
        self.id = bot_id
        self.page_id = page_id
        self.ig_user_id = ig_user_id
        self.token = page_access_token
        self.openai_key = openai_key
        self.profile = profile
        self.history: Dict[str, list] = {}  # ig_user -> history

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def send_text(self, recipient_id: str, text: str):
        """
        endpoint: POST /{ig_user_id}/messages
        """
        url = f"{GRAPH}/{self.ig_user_id}/messages"
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
            "messaging_type": "RESPONSE"
        }
        r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def handle_webhook(self, entry_change_value: Dict[str, Any]):
        """
        value = payload['entry'][...]['changes'][...]['value']
        نتعامل مع أول event فيه رسائل
        """
        # Instagram يحط الرسائل تحت "messaging"
        events = entry_change_value.get("messaging") or []
        for ev in events:
            sender = ev.get("sender", {}).get("id")
            message = (ev.get("message") or {})
            if not sender or not message:
                continue

            if "text" in message:
                user_text = (message.get("text") or "").strip()
            else:
                user_text = "(رسالة غير نصية)"

            sys = self._build_system_prompt(self.profile.get("company", {}))
            hist = self.history.get(sender, [])
            reply = generate_reply(self.openai_key, sys, hist, user_text)

            hist += [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}]
            self.history[sender] = hist[-30:]

            self.send_text(sender, reply)

    def _build_system_prompt(self, company: dict) -> str:
        name  = company.get("name", "الشركة")
        city  = company.get("city", "")
        hours = company.get("hours", {})
        days  = ", ".join(hours.get("days", []))
        time_from = hours.get("from", "")
        time_to   = hours.get("to", "")
        phone_cc  = company.get("phone", {}).get("cc", "")
        phone_no  = company.get("phone", {}).get("number", "")
        prompt    = company.get("prompt", "")
        return f"""أنت مساعد دعم لحساب Instagram الخاص بـ {name} في {city}.
ساعات العمل: من {time_from} إلى {time_to}. الأيام: {days}.
رقم التواصل (يُرسل نصيًا فقط): {phone_cc} {phone_no}.
{prompt}
كن مهذبًا ولطيفًا وضمن نطاق العمل."""

    def update_profile(self, new_profile: dict, new_openai: Optional[str] = None):
        if new_openai:
            self.openai_key = new_openai
        self.profile = new_profile

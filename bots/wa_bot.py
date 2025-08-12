# bots/wa_bot.py
# -*- coding: utf-8 -*-
import io
import json
import time
import requests
from typing import Dict, Any, Optional
from services.nlp import generate_reply
from services.tts import synth_eleven

GRAPH = "https://graph.facebook.com/v19.0"

class WhatsAppCloudBot:
    """
    بوت واتساب سحابي (Meta WhatsApp Cloud API)
    - يردّ نصياً دائماً
    - اختياري: يرسل صوت (ogg) برفعه كـ media ثم إرسال audio.id
    """
    def __init__(self, bot_id: str, wa_token: str, phone_number_id: str, openai_key: str, profile: dict):
        self.id = bot_id
        self.token = wa_token
        self.phone_number_id = phone_number_id
        self.openai_key = openai_key
        self.profile = profile
        self.history: Dict[str, list] = {}  # wa_user -> chat history

    # ========= إرسال =========
    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def send_text(self, to: str, text: str):
        url = f"{GRAPH}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text}
        }
        r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def _upload_audio(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str:
        """
        يرفع ملف صوت إلى واتساب ويرجع media_id
        """
        url = f"{GRAPH}/{self.phone_number_id}/media"
        files = {
            "file": (filename, io.BytesIO(audio_bytes), "audio/ogg"),
            "type": (None, "audio/ogg"),
            "messaging_product": (None, "whatsapp")
        }
        r = requests.post(url, headers=self._headers(), files=files, timeout=60)
        r.raise_for_status()
        data = r.json()
        return data.get("id")

    def send_voice(self, to: str, audio_bytes: bytes):
        media_id = self._upload_audio(audio_bytes)
        url = f"{GRAPH}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "audio",
            "audio": {"id": media_id}
        }
        r = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    # ========= معالجة الوِبهُوك =========
    def handle_webhook(self, value: Dict[str, Any]):
        """
        يستقبل value = payload['entry'][...]['changes'][...]['value']
        ويعالج أول رسالة واردة.
        """
        msgs = (value or {}).get("messages") or []
        if not msgs:
            return  # لا رسائل

        msg = msgs[0]
        from_ = msg.get("from")             # رقم المستخدم (MSISDN)
        mtype = msg.get("type")
        user_text = ""
        if mtype == "text":
            user_text = (msg.get("text", {}).get("body") or "").strip()
        elif mtype in ("audio", "voice"):
            user_text = "(المستخدم أرسل رسالة صوتية)"
        else:
            user_text = "(نوع رسالة غير نصية)"

        # نبني الـ system prompt من بروفايل الشركة
        sys = self._build_system_prompt(self.profile.get("company", {}))

        # الرد من OpenAI
        hist = self.history.get(from_, [])
        reply = generate_reply(self.openai_key, sys, hist, user_text)

        # نحفظ آخر 30 تفاعل
        hist += [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}]
        self.history[from_] = hist[-30:]

        # وضع الرد
        mode = self.profile.get("reply_mode", "text")
        voice_cfg = self.profile.get("voice")  # {"ek","vid"} أو None

        if mode == "text" or not voice_cfg:
            self.send_text(from_, reply)
        elif mode == "voice":
            try:
                audio = synth_eleven(voice_cfg["ek"], voice_cfg["vid"], reply)
                self.send_voice(from_, audio)
            except Exception:
                # fallback نصي
                self.send_text(from_, reply)
        else:  # both
            self.send_text(from_, reply)
            if voice_cfg:
                try:
                    audio = synth_eleven(voice_cfg["ek"], voice_cfg["vid"], reply)
                    self.send_voice(from_, audio)
                except Exception:
                    pass

    # ========= أدوات =========
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
        return f"""أنت مساعد دعم للشركة {name} في {city}.
ساعات العمل: من {time_from} إلى {time_to}. الأيام: {days}.
رقم التواصل (يُرسل نصيًا فقط): {phone_cc} {phone_no}.
{prompt}
كن مهذبًا ولطيفًا وضمن نطاق العمل."""

    def update_profile(self, new_profile: dict, new_openai: Optional[str] = None):
        if new_openai:
            self.openai_key = new_openai
        self.profile = new_profile

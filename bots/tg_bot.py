# bots/tg_bot.py
# -*- coding: utf-8 -*-
import io
from typing import Dict, Optional, List

from telebot import TeleBot
from telebot.types import Update, Message

from services.nlp import generate_reply
from services.tts import synth_eleven


WELCOME = "مرحبًا {name}! أنا مساعد دعم {company}. أُجيبك فورًا وأرشدك لما تحتاجه."


def build_system_prompt(company: dict) -> str:
    """
    يبني برومبت النظام اعتمادًا على بيانات الشركة — بالعربية الفصحى.
    """
    name  = company.get("name", "الشركة")
    city  = company.get("city", "")
    hours = company.get("hours", {}) or {}
    days  = ", ".join(hours.get("days", []))
    time_from = hours.get("from", "")
    time_to   = hours.get("to", "")
    phone_cc  = (company.get("phone", {}) or {}).get("cc", "")
    phone_no  = (company.get("phone", {}) or {}).get("number", "")
    prompt    = company.get("prompt", "")

    sys = f"""
أنت مساعد دعم للشركة "{name}" وتتحدث بالعربية الفصحى بنبرة مهذبة وواضحة.
- الموقع: {city}
- ساعات العمل: من {time_from} إلى {time_to} — الأيام: {days}
- رقم التواصل (يُرسل نصيًا فقط، ولا يُقرأ صوتيًا): {phone_cc} {phone_no}

التزم بنطاق خدمات الشركة بدقة، وقدّم حلولًا وعروضًا مناسبة عند الحاجة، دون مبالغة.
إذا كان السؤال خارج النطاق، أعد تركيز الحوار برفق نحو ما تقدّمه الشركة.
المعلومات المرجعية من صاحب الحساب:
{prompt}
""".strip()
    return sys


class TelegramClientBot:
    """
    بوت تيليجرام يعمل بنمط الـ Webhook:
      - يستقبل السيرفر POST من تيليجرام ويستدعي process_update(data).
      - لا يستخدم polling.
    """

    def __init__(self, bot_id: str, tg_token: str, openai_key: str, profile: dict):
        self.id = bot_id
        self.tg_token = tg_token
        self.tg = TeleBot(tg_token, parse_mode="HTML")
        self.openai_key = openai_key
        self.profile = profile or {}
        self.history: Dict[int, List[dict]] = {}  # chat_id -> [{"role":...,"content":...}]
        self._bind_handlers()

    # -------- Handlers --------
    def _bind_handlers(self):
        @self.tg.message_handler(content_types=["text", "voice", "audio"])
        def _on_message(m: Message):
            self._handle_message(m)

        @self.tg.message_handler(commands=["start", "help"])
        def _on_start(m: Message):
            try:
                name = (m.from_user.first_name or m.from_user.username or "صديقي").strip()
                company = (self.profile.get("company") or {}).get("name", "الشركة")
                self.tg.send_message(m.chat.id, WELCOME.format(name=name, company=company))
            except Exception as e:
                print(f"[TG:{self.id}] start/help error:", e)

    def _handle_message(self, m: Message):
        try:
            chat_id = m.chat.id
            # اسم المستخدم إن وُجد
            user_name = ""
            if getattr(m, "from_user", None):
                user_name = (m.from_user.first_name or m.from_user.username or "").strip()

            sys = build_system_prompt(self.profile.get("company", {}))

            # قراءة نص المستخدم (لا تحويل كلام-لنص الآن)
            if m.content_type == "text":
                user_text = (m.text or "").strip()
            else:
                user_text = "(أرسل المستخدم رسالة صوتية/ملفًا صوتيًا)"

            # تحية بسيطة
            if user_text in {"مرحبا", "مرحبا.", "مرحبا!", "مرحبًا", "أهلا", "أهلًا", "السلام عليكم"}:
                reply = WELCOME.format(
                    name=user_name or "صديقي",
                    company=self.profile.get("company", {}).get("name", "الشركة")
                )
            else:
                hist = self.history.get(chat_id, [])
                reply = generate_reply(self.openai_key, sys, hist, user_text)

            # تحديث الذاكرة (آخر 30 تبادل)
            hist = self.history.get(chat_id, [])
            hist += [{"role": "user", "content": user_text},
                     {"role": "assistant", "content": reply}]
            self.history[chat_id] = hist[-30:]

            # وضع الرد
            mode = (self.profile.get("reply_mode") or "text").lower()
            voice_cfg = self.profile.get("voice")  # {"ek": "...", "vid": "..."} أو None

            if mode == "text":
                self._send_text(chat_id, reply, reply_to=m.message_id)
            elif mode == "voice":
                if self._can_voice(voice_cfg):
                    self._send_voice(chat_id, reply, voice_cfg, reply_to=m.message_id)
                else:
                    self._send_text(chat_id, reply, reply_to=m.message_id)
            else:  # both
                self._send_text(chat_id, reply)
                if self._can_voice(voice_cfg):
                    self._send_voice(chat_id, reply, voice_cfg)

        except Exception as e:
            print(f"[TG:{self.id}] handle_message error:", e)
            try:
                self.tg.send_message(m.chat.id, "حدث خطأ بسيط. حاول مرة أخرى لاحقًا.")
            except Exception:
                pass

    # -------- Send helpers --------
    def _send_text(self, chat_id: int, text: str, reply_to: Optional[int] = None):
        self.tg.send_message(chat_id, text, reply_to_message_id=reply_to)

    def _can_voice(self, voice_cfg: Optional[dict]) -> bool:
        return bool(voice_cfg and voice_cfg.get("ek") and voice_cfg.get("vid"))

    def _send_voice(self, chat_id: int, text: str, voice_cfg: dict, reply_to: Optional[int] = None):
        """
        يُنتج صوت OGG مناسب لإرساله كـ voice في تيليجرام.
        """
        audio_bytes = synth_eleven(voice_cfg["ek"], voice_cfg["vid"], text)
        self.tg.send_voice(chat_id, io.BytesIO(audio_bytes), reply_to_message_id=reply_to)

    # -------- Webhook integration --------
    def process_update(self, data: dict):
        """
        تُستدعى من مسار الويبهوك في Flask:
            bot.process_update(request.get_json())
        """
        try:
            upd = Update.de_json(data)
            self.tg.process_new_updates([upd])
        except Exception as e:
            print(f"[TG:{self.id}] process_update error:", e)

    # -------- Lifecycle --------
    def start(self):
        """
        لا نستخدم polling هنا. يمكنك ضبط الـ webhook خارجياً عبر BotFather أو عبر:
            self.tg.set_webhook(url_full)
        (اتركها فارغة إن كنت تضبط الويبهوك من خارج الكود.)
        """
        return

    def stop(self):
        """
        لا يوجد شيء محدد لإيقافه في وضع الويبهوك.
        """
        try:
            self.tg.remove_webhook()  # لا يضر إن لم يكن مضبوطًا
        except Exception:
            pass

    # تحديث الإعدادات دون إعادة التشغيل (إن أمكن)
    def update_profile(self, new_profile: dict, new_openai: Optional[str] = None):
        if new_openai:
            self.openai_key = new_openai
        self.profile = new_profile


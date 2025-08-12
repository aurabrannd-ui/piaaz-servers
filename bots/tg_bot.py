# bots/tg_bot.py
# -*- coding: utf-8 -*-
import io
from telebot import TeleBot
from telebot.types import Update
from services.nlp import generate_reply
from services.tts import synth_eleven

WELCOME = "أهلًا {name}! أنا بوت الدعم الخاص بـ {company}. اسألني ما تشاء وسأساعدك فورًا 😉"


def build_system_prompt(company: dict) -> str:
    name  = company.get("name", "الشركة")
    city  = company.get("city", "")
    hours = company.get("hours", {}) or {}
    days  = ", ".join(hours.get("days", []) or [])
    time_from = hours.get("from", "")
    time_to   = hours.get("to", "")
    phone     = company.get("phone", {}) or {}
    phone_cc  = phone.get("cc", "+962")
    phone_no  = phone.get("number", "")
    prompt    = company.get("prompt", "")

    sys = f"""
أنت مساعد دعم لبق يتحدث العربية الفصحى بنبرة ودودة، مع خفة لطيفة دون مبالغة.
اسم الشركة: {name}. المدينة: {city}.
ساعات العمل: من {time_from} إلى {time_to}. الأيام: {days}.
رقم التواصل (أرسله نصيًا فقط ولا تنطقه بالصوت): {phone_cc} {phone_no}.
التزم بنطاق خدمات الشركة، واقترح حلولًا وعروضًا بشكل مقنع.
إذا خرج السؤال عن النطاق، أعد الحوار بلطف إلى الموضوع الأساسي.
معلومات الشركة/الإرشادات: {prompt}
عند التحية، استخدم اسم الشخص إن توفّر وعرّف بنفسك بلطف.
""".strip()
    return sys


class TelegramClientBot:
    """
    نسخة Webhook فقط (بدون polling).
    - السيرفر يستقبل POST على /webhook/telegram/<bot_id>
    - يستدعي bot.process_update(data)
    """

    def __init__(self, bot_id: str, tg_token: str, openai_key: str, profile: dict):
        self.id = bot_id
        self.tg_token = tg_token
        self.tg = TeleBot(tg_token, parse_mode="HTML")
        self.openai_key = openai_key
        self.profile = profile or {}
        self.history = {}  # chat_id -> [{"role":...,"content":...}]

        @self.tg.message_handler(content_types=["text", "voice", "audio"])
        def on_msg(m):
            try:
                chat_id = m.chat.id
                user_name = ""
                if getattr(m, "from_user", None):
                    user_name = (m.from_user.first_name or m.from_user.username or "").strip()

                sys = build_system_prompt(self.profile.get("company", {}) or {})

                # استخراج نص المستخدم
                if m.content_type == "text":
                    user_text = (m.text or "").strip()
                else:
                    # لاحقًا يمكن إضافة STT؛ الآن نرد نصيًا.
                    user_text = "(رسالة صوتية من المستخدم)"

                # تحية بسيطة
                greetings = {"مرحبا", "مرحبا.", "مرحبا!", "أهلا", "أهلًا", "السلام عليكم", "هاي", "سلام"}
                if user_text.strip(".!؟ ").replace("اً", "ا") in greetings:
                    reply = WELCOME.format(
                        name=user_name or "صديقي",
                        company=(self.profile.get("company", {}) or {}).get("name", "الشركة")
                    )
                else:
                    hist = self.history.get(chat_id, [])
                    reply = generate_reply(self.openai_key, sys, hist, user_text)

                # تحديث الذاكرة (آخر 30 تبادل)
                hist = self.history.get(chat_id, [])
                hist += [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": reply},
                ]
                self.history[chat_id] = hist[-30:]

                # وضع الرد
                mode = (self.profile.get("reply_mode") or "text").lower()
                voice_cfg = self.profile.get("voice")  # {"ek","vid"} أو None

                if mode == "text":
                    self.tg.send_message(chat_id, reply, reply_to_message_id=m.message_id)
                elif mode == "voice":
                    if not voice_cfg:
                        self.tg.send_message(chat_id, reply, reply_to_message_id=m.message_id)
                        return
                    audio_bytes = synth_eleven(voice_cfg["ek"], voice_cfg["vid"], reply)
                    self.tg.send_voice(chat_id, io.BytesIO(audio_bytes), reply_to_message_id=m.message_id)
                else:  # both
                    self.tg.send_message(chat_id, reply, reply_to_message_id=m.message_id)
                    if voice_cfg:
                        audio_bytes = synth_eleven(voice_cfg["ek"], voice_cfg["vid"], reply)
                        self.tg.send_voice(chat_id, io.BytesIO(audio_bytes))
            except Exception as e:
                try:
                    self.tg.send_message(m.chat.id, "حدث خطأ بسيط، جرّب بعد قليل.")
                except Exception:
                    pass
                print(f"[TG:{self.id}] error:", e)

    # استدعاء من مسار الويبهوك في app.py
    def process_update(self, data: dict):
        try:
            upd = Update.de_json(data)
            self.tg.process_new_updates([upd])
        except Exception as e:
            print(f"[TG:{self.id}] process_update error:", e)

    # واجهات مطلوبة من المانجر
    def start(self):
        # لا شيء هنا لأننا نعمل Webhook فقط (بدون polling)
        return True

    def stop(self):
        # لا يوجد polling لتوقيفه؛ نتركها للاتساق مع الواجهة
        return True

    # تحديث إعدادات البوت لاحقًا من الداشبورد
    def update_profile(self, new_profile: dict, new_openai: str | None = None):
        if new_openai:
            self.openai_key = new_openai
        self.profile = new_profile or {}



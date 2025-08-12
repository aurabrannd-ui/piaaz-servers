import threading, io
from telebot import TeleBot
from services.nlp import generate_reply
from services.tts import synth_eleven

WELCOME = "أهلاً {name}! أنا بوت الدعم تبع {company}. اسألني أي إشي وساعدك فورًا 😉"

def build_system_prompt(company: dict) -> str:
    name  = company.get("name","الشركة")
    city  = company.get("city","")
    hours = company.get("hours",{})
    days  = ", ".join(hours.get("days",[]))
    time_from = hours.get("from","")
    time_to   = hours.get("to","")
    phone_cc  = company.get("phone",{}).get("cc","+962")
    phone_no  = company.get("phone",{}).get("number","")
    prompt    = company.get("prompt","")
    sys = f"""
أنت مساعد دعم أردني لبق يتكلم بلهجة أردنية طبيعية مع مزح خفيف بدون مبالغة.
اسم الشركة: {name}. المدينة: {city}.
ساعات العمل: من {time_from} إلى {time_to}. الأيام: {days}.
رقم التواصل (أرسله نصيًا فقط ولا تقرأه بالصوت): {phone_cc} {phone_no}.
التزم بنطاق الشغل، واقترح حلول/عروض بشكل مقنع.
لو السؤال خارج النطاق، رجّع اللطافة للموضوع الأساسي بسلاسة.
المعلومات الخاصة بالشركة: {prompt}
عند التحية استخدم اسم الشخص لو متوفر، وعرّف بنفسك بلطف.
""".strip()
    return sys

class TelegramClientBot(threading.Thread):
    def __init__(self, bot_id: str, tg_token: str, openai_key: str, profile: dict):
        super().__init__(daemon=True)
        self.id = bot_id
        self.tg = TeleBot(tg_token)
        self.openai_key = openai_key
        self.profile = profile
        self.history = {}  # chat_id -> [{"role":...,"content":...}]
        self.running = False

        @self.tg.message_handler(content_types=['text','voice','audio'])
        def on_msg(m):
            try:
                chat_id = m.chat.id
                user_name = (m.from_user.first_name or m.from_user.username or "").strip() if m.from_user else ""
                sys = build_system_prompt(self.profile["company"])

                if m.content_type == 'text':
                    user_text = (m.text or "").strip()
                else:
                    user_text = "(المستخدم أرسل رسالة صوتية)"

                if user_text in ["مرحبا","مرحبا.","مرحبا!","أهلاً","اهلا","سلام","هاي"]:
                    reply = WELCOME.format(name=user_name or "صديقي", company=self.profile["company"].get("name","الشركة"))
                else:
                    hist = self.history.get(chat_id, [])
                    reply = generate_reply(self.openai_key, sys, hist, user_text)

                hist = self.history.get(chat_id, [])
                hist += [{"role":"user","content": user_text}, {"role":"assistant","content": reply}]
                self.history[chat_id] = hist[-30:]

                mode = self.profile.get("reply_mode","text")
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
                    self.tg.send_message(chat_id, reply)
                    if voice_cfg:
                        audio_bytes = synth_eleven(voice_cfg["ek"], voice_cfg["vid"], reply)
                        self.tg.send_voice(chat_id, io.BytesIO(audio_bytes))
            except Exception as e:
                try: self.tg.send_message(m.chat.id, "صار خطأ بسيط، جرّب كمان شوي.")
                except: pass
                print(f"[TG:{self.id}] error:", e)

    def update_profile(self, new_profile: dict, new_openai: str|None=None):
        if new_openai: self.openai_key = new_openai
        self.profile = new_profile

    def run(self):
        self.running = True
        while self.running:
            try:
                self.tg.infinity_polling(skip_pending=True, timeout=40)
            except Exception as e:
                print(f"[TG:{self.id}] polling restart", e)

    def stop(self):
        self.running = False
        try: self.tg.stop_polling()
        except: pass


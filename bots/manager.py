# bots/manager.py
# -*- coding: utf-8 -*-
import time
import logging
import threading
from typing import Dict

# منصّة تيليجرام (موجودة لديك)
from bots.tg_bot import TelegramClientBot

# نحاول استيراد واتساب/إنستغرام إن كانت الملفات موجودة
try:
    from bots.wa_bot import WhatsAppCloudBot as WhatsAppClientBot
except Exception:
    WhatsAppClientBot = None

try:
    from bots.ig_bot import InstagramDMClientBot as InstagramClientBot
except Exception:
    InstagramClientBot = None


class BotManager:
    """
    يدير بوتات متعددة عبر منصّات مختلفة بأسلوب webhook-only:
    - Telegram / WhatsApp / Instagram
    """

    def __init__(self):
        self.bots_meta: Dict[str, dict] = {}   # id -> meta/config
        self.bots_obj:  Dict[str, object] = {} # id -> running object (أو None)
        # خرائط لتوجيه الويبهوك
        self.wa_by_phone: Dict[str, str] = {}  # phone_number_id -> bot_id
        self.ig_by_page:  Dict[str, str] = {}  # page_id         -> bot_id
        self._lock = threading.RLock()
        logging.getLogger(__name__).setLevel(logging.INFO)

    # ------------- أدوات داخليّة -------------

    def _gen_id(self) -> str:
        return f"bot_{int(time.time() * 1000)}"

    def _required_creds(self, platform: str):
        if platform == "telegram":
            return ["openai", "tgToken"]
        if platform == "whatsapp":
            # نطلب WABA ID للتماشي مع الواجهة، حتى لو ما نستخدمه مباشرة
            return ["openai", "waToken", "waPhoneId", "waWabaId"]
        if platform == "instagram":
            return ["openai", "igPageId", "igUserId", "igAccess"]
        return []

    def _has_all_creds(self, meta: dict) -> bool:
        platform = meta.get("platform")
        creds = (meta.get("creds") or {})
        for k in self._required_creds(platform):
            v = creds.get(k)
            if not isinstance(v, str) or not v.strip():
                return False
        return True

    def _build_profile(self, meta: dict) -> dict:
        return {
            "reply_mode": meta.get("reply_mode", "text"),
            "company": meta.get("company", {}),
            "voice": meta.get("voice"),
        }

    def _need_restart_after_update(self, old: dict, new: dict) -> bool:
        """
        هل يلزم إعادة تشغيل؟ نعم إذا تغيّرت المنصّة أو مفاتيح حسّاسة.
        (هنا "إعادة التشغيل" تعني إعادة إنشاء الكائن وربطه بالخرائط)
        """
        if old.get("platform") != new.get("platform"):
            return True

        o, n = old.get("creds", {}) or {}, new.get("creds", {}) or {}
        sensitive_by_platform = {
            "telegram":   ["openai", "tgToken"],
            "whatsapp":   ["openai", "waToken", "waPhoneId", "waWabaId"],
            "instagram":  ["openai", "igPageId", "igUserId", "igAccess"],
        }
        keys = sensitive_by_platform.get(new.get("platform"), [])
        return any((o.get(k) != n.get(k)) for k in keys)

    # ------------- واجهة عامة -------------

    def list(self):
        with self._lock:
            out = []
            for i, meta in self.bots_meta.items():
                out.append({
                    "id": i,
                    "platform": meta.get("platform"),
                    "active": bool(self.bots_obj.get(i)),
                    "reply_mode": meta.get("reply_mode", "text"),
                    "company": meta.get("company", {}),
                })
            return out

    def create(self, meta: dict) -> str:
        with self._lock:
            bot_id = meta.get("id") or self._gen_id()
            meta["id"] = bot_id

            platform = meta.get("platform")
            if platform not in ("telegram", "whatsapp", "instagram"):
                raise ValueError("Unsupported platform")

            if not self._has_all_creds(meta):
                logging.warning("Missing required credentials for %s (bot_id=%s)", platform, bot_id)

            self.bots_meta[bot_id] = meta
            try:
                self._start_unlocked(bot_id)
            except Exception as e:
                logging.exception("Start failed for %s: %s", bot_id, e)
                self.bots_obj[bot_id] = None

            return bot_id

    def start(self, bot_id: str):
        with self._lock:
            self._start_unlocked(bot_id)

    def _start_unlocked(self, bot_id: str):
        meta = self.bots_meta.get(bot_id)
        if not meta:
            raise KeyError(f"Unknown bot_id: {bot_id}")

        platform = meta.get("platform")
        creds = meta.get("creds") or {}
        profile = self._build_profile(meta)

        # أوقف القديم ثم أنشئ كائن جديد
        self._stop_unlocked(bot_id)

        if platform == "telegram":
            tg_token   = creds.get("tgToken", "")
            openai_key = creds.get("openai", "")
            bot = TelegramClientBot(bot_id, tg_token, openai_key, profile)
            # Webhook-only: لا حاجة لاستدعاء start()
            self.bots_obj[bot_id] = bot
            logging.info("Telegram bot ready: %s", bot_id)

        elif platform == "whatsapp":
            if WhatsAppClientBot is None:
                logging.warning("WhatsAppClientBot not available. Skipping start for %s", bot_id)
                self.bots_obj[bot_id] = None
                return
            wa_token  = creds.get("waToken", "")
            phone_id  = creds.get("waPhoneId", "")
            openai_key = creds.get("openai", "")
            bot = WhatsAppClientBot(bot_id, wa_token, phone_id, openai_key, profile)
            self.bots_obj[bot_id] = bot
            if phone_id:
                self.wa_by_phone[phone_id] = bot_id
            logging.info("WhatsApp bot ready: %s (phone_id=%s)", bot_id, phone_id)

        elif platform == "instagram":
            if InstagramClientBot is None:
                logging.warning("InstagramClientBot not available. Skipping start for %s", bot_id)
                self.bots_obj[bot_id] = None
                return
            page_id   = creds.get("igPageId", "")
            ig_user   = creds.get("igUserId", "")
            ig_token  = creds.get("igAccess", "")
            openai_key = creds.get("openai", "")
            bot = InstagramClientBot(bot_id, page_id, ig_user, ig_token, openai_key, profile)
            self.bots_obj[bot_id] = bot
            if page_id:
                self.ig_by_page[page_id] = bot_id
            logging.info("Instagram bot ready: %s (page_id=%s)", bot_id, page_id)

        else:
            raise ValueError("Unsupported platform")

    def stop(self, bot_id: str):
        with self._lock:
            self._stop_unlocked(bot_id)

    def _stop_unlocked(self, bot_id: str):
        bot = self.bots_obj.get(bot_id)
        if bot:
            try:
                if hasattr(bot, "stop"):
                    bot.stop()
            except Exception:
                logging.exception("Error while stopping bot %s", bot_id)
        # نظّف الخرائط
        for k, v in list(self.wa_by_phone.items()):
            if v == bot_id:
                self.wa_by_phone.pop(k, None)
        for k, v in list(self.ig_by_page.items()):
            if v == bot_id:
                self.ig_by_page.pop(k, None)
        self.bots_obj[bot_id] = None
        logging.info("Bot stopped: %s", bot_id)

    def restart(self, bot_id: str):
        with self._lock:
            self._stop_unlocked(bot_id)
            self._start_unlocked(bot_id)

    def update(self, bot_id: str, meta_update: dict):
        with self._lock:
            old = self.bots_meta.get(bot_id)
            if not old:
                return

            merged = {**old}
            for k, v in meta_update.items():
                if k == "company" and isinstance(v, dict):
                    merged.setdefault("company", {}).update(v)
                elif k == "creds" and isinstance(v, dict):
                    merged.setdefault("creds", {}).update(v)
                else:
                    merged[k] = v

            need_restart = self._need_restart_after_update(old, merged)
            self.bots_meta[bot_id] = merged

            if need_restart:
                logging.info("Config changed (requires restart) for %s", bot_id)
                self._stop_unlocked(bot_id)
                try:
                    self._start_unlocked(bot_id)
                except Exception:
                    logging.exception("Restart failed for %s", bot_id)
            else:
                bot = self.bots_obj.get(bot_id)
                if bot and hasattr(bot, "update_profile"):
                    profile = self._build_profile(merged)
                    new_openai = (merged.get("creds") or {}).get("openai")
                    try:
                        bot.update_profile(profile, new_openai)
                        logging.info("Hot-updated bot %s", bot_id)
                    except Exception:
                        logging.exception("Hot update failed for %s", bot_id)

    # ------------- توجيه Webhooks -------------

    def route_whatsapp(self, value: dict):
        """
        يأخذ value من payload['entry'][...]['changes'][...]['value']
        ويستخرج phone_number_id لتحديد البوت.
        """
        meta = (value or {}).get("metadata") or {}
        phone_number_id = meta.get("phone_number_id") or ""
        # بعض الأحداث قد لا تحملها بوضوح—يمكن تعديل هذا الجزء لو تغيّر شكل الـ payload
        bot_id = self.wa_by_phone.get(phone_number_id)
        if not bot_id:
            return
        bot = self.bots_obj.get(bot_id)
        if bot and hasattr(bot, "handle_webhook"):
            bot.handle_webhook(value)

    def route_instagram(self, value: dict):
        """
        يأخذ value من payload['entry'][...]['changes'][...]['value']
        نحاول استنتاج page_id لربطه بالبوت.
        """
        page_id = value.get("id") or value.get("page_id") or ""
        bot_id = self.ig_by_page.get(page_id)
        if not bot_id:
            return
        bot = self.bots_obj.get(bot_id)
        if bot and hasattr(bot, "handle_webhook"):
            bot.handle_webhook(value)


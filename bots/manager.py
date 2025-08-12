import time
import logging
import threading
from typing import Dict, Optional

# عميل تيليجرام (مطلوب حالياً)
from bots.tg_bot import TelegramClientBot

# ملاحظة: سنحاول استيراد بقية المنصات إن وجدت ملفاتها
try:
    from bots.wa_bot import WhatsAppClientBot  # اختياري
except Exception:
    WhatsAppClientBot = None  # لا تكسر التطبيق لو غير متاح

try:
    from bots.ig_bot import InstagramClientBot  # اختياري
except Exception:
    InstagramClientBot = None  # لا تكسر التطبيق لو غير متاح


class BotManager:
    """
    مسؤول عن إدارة بوتات متعددة لمنصات مختلفة:
    - Telegram (مُفعّل)
    - WhatsApp / Instagram (اختياري: يعمل إذا وفّرت ملفات البوتات)
    """

    def __init__(self):
        self.bots_meta: Dict[str, dict] = {}   # id -> meta/config (كما أرسلتها من الواجهة)
        self.bots_obj:  Dict[str, object] = {} # id -> كائن البوت المشغّل أو None
        self._lock = threading.RLock()
        logging.getLogger(__name__).setLevel(logging.INFO)

    # --------------------- أدوات مساعدة داخلية ---------------------

    def _gen_id(self) -> str:
        return f"bot_{int(time.time() * 1000)}"

    def _required_creds(self, platform: str):
        if platform == "telegram":
            return ["openai", "tgToken"]
        if platform == "whatsapp":
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
        """نقرّر هل التحديث يتطلب إعادة تشغيل (مثلاً تغيّر المنصة أو مفاتيح حساسة)."""
        if old.get("platform") != new.get("platform"):
            return True

        o, n = old.get("creds", {}) or {}, new.get("creds", {}) or {}
        # مفاتيح حسّاسة لكل منصة
        sensitive_by_platform = {
            "telegram":   ["openai", "tgToken"],
            "whatsapp":   ["openai", "waToken", "waPhoneId", "waWabaId"],
            "instagram":  ["openai", "igPageId", "igUserId", "igAccess"],
        }
        keys = sensitive_by_platform.get(new.get("platform"), [])
        return any((o.get(k) != n.get(k)) for k in keys)

    # --------------------- واجهة الإدارة العامة ---------------------

    def list(self):
        """إرجاع قائمة مبسّطة للاستهلاك من الواجهة."""
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
        """حفظ الـ meta وتشغيل البوت."""
        with self._lock:
            bot_id = meta.get("id") or self._gen_id()
            meta["id"] = bot_id

            # تحقّق أساسي
            platform = meta.get("platform")
            if platform not in ("telegram", "whatsapp", "instagram"):
                raise ValueError("Unsupported platform")

            if not self._has_all_creds(meta):
                # لا نكسر، لكن نسجّل تحذير ونسمح بالحفظ (يمكن يكملها المستخدم لاحقاً)
                logging.warning("Missing required credentials for %s (bot_id=%s)", platform, bot_id)

            self.bots_meta[bot_id] = meta
            # حاول التشغيل (لو نقص شيء، ما نكسر)
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

        # أوقف أي بوت سابق ثم شغّل الجديد
        self._stop_unlocked(bot_id)

        if platform == "telegram":
            tg_token = creds.get("tgToken", "")
            openai_key = creds.get("openai", "")
            bot = TelegramClientBot(bot_id, tg_token, openai_key, profile)
            bot.start()
            self.bots_obj[bot_id] = bot
            logging.info("Telegram bot started: %s", bot_id)

        elif platform == "whatsapp":
            if WhatsAppClientBot is None:
                logging.warning("WhatsAppClientBot not available. Skipping start for %s", bot_id)
                self.bots_obj[bot_id] = None
                return
            bot = WhatsAppClientBot(
                bot_id=bot_id,
                wa_token=creds.get("waToken", ""),
                phone_id=creds.get("waPhoneId", ""),
                waba_id=creds.get("waWabaId", ""),
                openai_key=creds.get("openai", ""),
                profile=profile
            )
            bot.start()
            self.bots_obj[bot_id] = bot
            logging.info("WhatsApp bot started: %s", bot_id)

        elif platform == "instagram":
            if InstagramClientBot is None:
                logging.warning("InstagramClientBot not available. Skipping start for %s", bot_id)
                self.bots_obj[bot_id] = None
                return
            bot = InstagramClientBot(
                bot_id=bot_id,
                page_id=creds.get("igPageId", ""),
                ig_user_id=creds.get("igUserId", ""),
                page_access_token=creds.get("igAccess", ""),
                openai_key=creds.get("openai", ""),
                profile=profile
            )
            bot.start()
            self.bots_obj[bot_id] = bot
            logging.info("Instagram bot started: %s", bot_id)

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
        self.bots_obj[bot_id] = None
        logging.info("Bot stopped: %s", bot_id)

    def restart(self, bot_id: str):
        with self._lock:
            self._stop_unlocked(bot_id)
            self._start_unlocked(bot_id)

    def update(self, bot_id: str, meta_update: dict):
        """تحديث الميتا + إعادة تشغيل فقط عند الحاجة، وإلا نعمل hot update."""
        with self._lock:
            old = self.bots_meta.get(bot_id)
            if not old:
                return

            # دمج حقول مع الحفاظ على البنى الداخلية
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

            bot = self.bots_obj.get(bot_id)
            if need_restart:
                logging.info("Config changed (requires restart) for %s", bot_id)
                self._stop_unlocked(bot_id)
                try:
                    self._start_unlocked(bot_id)
                except Exception:
                    logging.exception("Restart failed for %s", bot_id)
            else:
                # تحديث حيّ إن كانت البنية تدعم
                if bot and hasattr(bot, "update_profile"):
                    profile = self._build_profile(merged)
                    new_openai = (merged.get("creds") or {}).get("openai")
                    try:
                        bot.update_profile(profile, new_openai)
                        logging.info("Hot-updated bot %s", bot_id)
                    except Exception:
                        logging.exception("Hot update failed for %s", bot_id)


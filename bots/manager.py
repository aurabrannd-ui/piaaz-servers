# bots/manager.py
# -*- coding: utf-8 -*-
import os
import time
import logging
import threading
from typing import Dict, Optional

import requests

# Telegram (مطلوب)
from bots.tg_bot import TelegramClientBot

# WhatsApp (اختياري)
try:
    from bots.wa_bot import WhatsAppCloudBot  # اسم الكلاس حسب ملفك
except Exception:
    WhatsAppCloudBot = None  # ما نكسر التطبيق لو الملف مش موجود

# Instagram (اختياري)
try:
    from bots.ig_bot import InstagramDMClientBot  # اسم الكلاس حسب ملفك
except Exception:
    InstagramDMClientBot = None  # ما نكسر التطبيق لو الملف مش موجود


GRAPH = "https://graph.facebook.com/v19.0"
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://piaaz-servers.onrender.com")

class BotManager:
    """
    إدارة عدة بوتات عبر منصات مختلفة:
      - Telegram (مُفعّل + تعيين ويبهوك تلقائي)
      - WhatsApp Cloud (اختياري: subscribe تلقائي للرقم)
      - Instagram DM (اختياري: subscribe تلقائي للصفحة)
    """

    def __init__(self):
        self.bots_meta: Dict[str, dict] = {}   # id -> meta/config (كما تأتي من الواجهة)
        self.bots_obj:  Dict[str, object] = {} # id -> كائن البوت المشغّل أو None
        self._lock = threading.RLock()
        logging.getLogger(__name__).setLevel(logging.INFO)

    # --------------- أدوات داخلية ---------------

    def _gen_id(self) -> str:
        return f"bot_{int(time.time() * 1000)}"

    def _required_creds(self, platform: str):
        if platform == "telegram":
            return ["openai", "tgToken"]
        if platform == "whatsapp":
            # نستخدم waPhoneId في التوجيه داخل الوِبهُوك
            return ["openai", "waToken", "waPhoneId"]
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
        """هل التحديث يتطلب إعادة تشغيل؟"""
        if old.get("platform") != new.get("platform"):
            return True
        o, n = old.get("creds", {}) or {}, new.get("creds", {}) or {}
        sensitive_by_platform = {
            "telegram":  ["openai", "tgToken"],
            "whatsapp":  ["openai", "waToken", "waPhoneId"],
            "instagram": ["openai", "igPageId", "igUserId", "igAccess"],
        }
        keys = sensitive_by_platform.get(new.get("platform"), [])
        return any((o.get(k) != n.get(k)) for k in keys)

    # ---------- تفعيل/اشتراك Webhook تلقائي ----------

    def _auto_webhook_telegram(self, bot_id: str, tg_token: str):
        """يضبط Webhook لتيليجرام مباشرة."""
        if not tg_token:
            return
        url = f"https://api.telegram.org/bot{tg_token}/setWebhook"
        webhook = f"{PUBLIC_BASE}/webhooks/telegram/{bot_id}"
        try:
            r = requests.post(url, json={"url": webhook}, timeout=15)
            ok = r.json().get("ok", False) if r.headers.get("content-type","").startswith("application/json") else False
            logging.info("[TG auto-webhook] %s -> %s | ok=%s status=%s", bot_id, webhook, ok, r.status_code)
        except Exception:
            logging.exception("[TG auto-webhook] failed for %s", bot_id)

    def _auto_subscribe_whatsapp(self, phone_id: str, wa_token: str):
        """
        يشترك الرقم في التطبيق (لا يضبط رابط الويبهوك من هنا؛
        الرابط يُضبط مرة من لوحة Meta على /webhooks/meta).
        """
        if not (phone_id and wa_token):
            return
        url = f"{GRAPH}/{phone_id}/subscribed_apps"
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {wa_token}"}, timeout=20)
            logging.info("[WA subscribe] phone_id=%s status=%s body=%s", phone_id, r.status_code, r.text[:200])
        except Exception:
            logging.exception("[WA subscribe] failed")

    def _auto_subscribe_instagram(self, page_id: str, page_token: str):
        """
        يشترك الصفحة في التطبيق لاستقبال رسائل IG (الويبهوك على مستوى التطبيق يجب ضبطه من لوحة Meta).
        """
        if not (page_id and page_token):
            return
        url = f"{GRAPH}/{page_id}/subscribed_apps"
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {page_token}"}, timeout=20)
            logging.info("[IG subscribe] page_id=%s status=%s body=%s", page_id, r.status_code, r.text[:200])
        except Exception:
            logging.exception("[IG subscribe] failed")

    # --------------- CRUD/إدارة عامة ---------------

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

            # تشغيل + تفعيل الويبهوك/الاشتراك
            try:
                self._start_unlocked(bot_id)

                creds = meta.get("creds") or {}
                if platform == "telegram":
                    self._auto_webhook_telegram(bot_id, creds.get("tgToken", ""))
                elif platform == "whatsapp":
                    self._auto_subscribe_whatsapp(creds.get("waPhoneId",""), creds.get("waToken",""))
                elif platform == "instagram":
                    # نستخدم page_id + page access token (igAccess)
                    self._auto_subscribe_instagram(creds.get("igPageId",""), creds.get("igAccess",""))
            except Exception:
                logging.exception("Start/auto-webhook failed for %s", bot_id)
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

        # أوقف القديم
        self._stop_unlocked(bot_id)

        if platform == "telegram":
            tg_token   = creds.get("tgToken", "")
            openai_key = creds.get("openai", "")
            bot = TelegramClientBot(bot_id, tg_token, openai_key, profile)
            if hasattr(bot, "start"):
                try: bot.start()
                except Exception: pass
            self.bots_obj[bot_id] = bot
            logging.info("Telegram bot started: %s", bot_id)

        elif platform == "whatsapp":
            if WhatsAppCloudBot is None:
                logging.warning("WhatsAppCloudBot not available. Skipping start for %s", bot_id)
                self.bots_obj[bot_id] = None
                return
            bot = WhatsAppCloudBot(
                bot_id=bot_id,
                wa_token=creds.get("waToken", ""),
                phone_number_id=creds.get("waPhoneId", ""),
                openai_key=creds.get("openai", ""),
                profile=profile
            )
            if hasattr(bot, "start"):
                try: bot.start()
                except Exception: pass
            self.bots_obj[bot_id] = bot
            logging.info("WhatsApp bot started: %s", bot_id)

        elif platform == "instagram":
            if InstagramDMClientBot is None:
                logging.warning("InstagramDMClientBot not available. Skipping start for %s", bot_id)
                self.bots_obj[bot_id] = None
                return
            bot = InstagramDMClientBot(
                bot_id=bot_id,
                page_id=creds.get("igPageId", ""),
                ig_user_id=creds.get("igUserId", ""),
                page_access_token=creds.get("igAccess", ""),
                openai_key=creds.get("openai", ""),
                profile=profile
            )
            if hasattr(bot, "start"):
                try: bot.start()
                except Exception: pass
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

            bot = self.bots_obj.get(bot_id)
            if need_restart:
                logging.info("Config changed (requires restart) for %s", bot_id)
                self._stop_unlocked(bot_id)
                try:
                    self._start_unlocked(bot_id)
                except Exception:
                    logging.exception("Restart failed for %s", bot_id)
            else:
                if bot and hasattr(bot, "update_profile"):
                    profile = self._build_profile(merged)
                    new_openai = (merged.get("creds") or {}).get("openai")
                    try:
                        bot.update_profile(profile, new_openai)
                        logging.info("Hot-updated bot %s", bot_id)
                    except Exception:
                        logging.exception("Hot update failed for %s", bot_id)

    # --------------- التوجيه للوِبهُوك ---------------

    def route_whatsapp(self, value: dict):
        """
        يستقبل value = payload['entry'][..]['changes'][..]['value'] من /webhooks/whatsapp
        ونوجهه للبوت المناسب عبر phone_number_id، ولو ما قدرنا، نبعثه لكل بوتات واتساب.
        """
        phone_id = (value.get("metadata") or {}).get("phone_number_id")
        targets = []

        with self._lock:
            for bot_id, meta in self.bots_meta.items():
                if meta.get("platform") != "whatsapp":
                    continue
                bot = self.bots_obj.get(bot_id)
                if not bot:
                    continue
                creds = meta.get("creds") or {}
                if phone_id and creds.get("waPhoneId") == phone_id:
                    targets.append(bot)

            if not targets:
                for bot_id, meta in self.bots_meta.items():
                    if meta.get("platform") == "whatsapp":
                        bot = self.bots_obj.get(bot_id)
                        if bot:
                            targets.append(bot)

        for bot in targets:
            try:
                bot.handle_webhook(value)  # اسم الدالة في wa_bot.py
            except Exception:
                logging.exception("WhatsApp handle_webhook failed")

    def route_instagram(self, value: dict):
        """
        يستقبل value = payload['entry'][..]['changes'][..]['value'] من /webhooks/instagram
        ما في page_id في value مباشرة، لذلك نوجّه لكل بوتات إنستغرام.
        """
        targets = []
        with self._lock:
            for bot_id, meta in self.bots_meta.items():
                if meta.get("platform") != "instagram":
                    continue
                bot = self.bots_obj.get(bot_id)
                if bot:
                    targets.append(bot)

        for bot in targets:
            try:
                bot.handle_webhook(value)  # اسم الدالة في ig_bot.py
            except Exception:
                logging.exception("Instagram handle_webhook failed")

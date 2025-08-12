# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, send_from_directory, abort
from flask_cors import CORS
from functools import wraps
import os
import requests

from bots.manager import BotManager

# إعدادات Supabase (واجهة)
SUPABASE_URL = "https://roahzmbpajuxyqqeloyu.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJvYWh6bWJwYWp1eHlxcWVsb3l1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTQ5OTczNzQsImV4cCI6MjA3MDU3MzM3NH0.Zk1YE_ikgVPHxDZu_gC4uJWywZbW9plD5SJ1igprCQ8"

app = Flask(__name__)
CORS(app)
manager = BotManager()

# ============ تحقق جلسة واجهة (Bearer من Supabase) ============
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401
        token = auth_header.split(" ", 1)[1]
        resp = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON_KEY},
            timeout=20,
        )
        if resp.status_code != 200:
            return jsonify({"error": "Invalid or expired token"}), 401
        return f(*args, **kwargs)
    return decorated

# ============ API (محمية) ============
@app.get("/api/bots")
@require_auth
def list_bots():
    return jsonify(manager.list())

@app.post("/api/activate")
@require_auth
def activate():
    data = request.get_json(force=True)
    if data.get("platform") not in ("telegram", "whatsapp", "instagram"):
        return jsonify({"error": "unsupported platform"}), 400
    bot_id = manager.create(data)
    return jsonify({"ok": True, "id": bot_id})

@app.post("/api/bots/<bot_id>/update")
@require_auth
def update_bot(bot_id):
    upd = request.get_json(force=True)
    manager.update(bot_id, upd)
    return jsonify({"ok": True})

@app.post("/api/bots/<bot_id>/restart")
@require_auth
def restart_bot(bot_id):
    manager.restart(bot_id)
    return jsonify({"ok": True})

@app.delete("/api/bots/<bot_id>")
@require_auth
def delete_bot(bot_id):
    manager.stop(bot_id)
    manager.bots_meta.pop(bot_id, None)
    return jsonify({"ok": True})

# ============ Webhooks (عامة) ============

# Telegram: نستخدم bot_id لسهولة التوجيه لـ manager.bots_obj[bot_id]
@app.post("/webhooks/telegram/<bot_id>")
def telegram_webhook(bot_id):
    bot = manager.bots_obj.get(bot_id)
    if not bot or not hasattr(bot, "process_update"):
        return jsonify({"error": "bot not found or not ready"}), 404
    try:
        payload = request.get_json(force=True, silent=True) or {}
        bot.process_update(payload)
        return jsonify({"ok": True})
    except Exception as e:
        print("[TG webhook] error:", e)
        return jsonify({"ok": False}), 500

# Meta verification (WhatsApp/Instagram) GET?hub.challenge
@app.get("/webhooks/meta")
def meta_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    # اختياري: تحقّق verify_token لو عاملينه
    # إن بدك تتحكم فيه، ضيف متغير بيئي VERIFY_TOKEN وقارن:
    VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "")
    if mode == "subscribe" and challenge is not None:
        if not VERIFY_TOKEN or token == VERIFY_TOKEN:
            return challenge, 200
        return "Forbidden", 403
    return "Bad Request", 400

# WhatsApp Cloud webhook (POST من Meta)
@app.post("/webhooks/whatsapp")
def whatsapp_webhook():
    data = request.get_json(force=True, silent=True) or {}
    try:
        # payload بصيغة Meta القياسية: entry -> changes -> value
        entries = data.get("entry", []) or []
        for entry in entries:
            changes = entry.get("changes", []) or []
            for ch in changes:
                value = ch.get("value") or {}
                manager.route_whatsapp(value)
        return jsonify({"ok": True})
    except Exception as e:
        print("[WA webhook] error:", e)
        return jsonify({"ok": False}), 500

# Instagram Messaging webhook (POST من Meta)
@app.post("/webhooks/instagram")
def instagram_webhook():
    data = request.get_json(force=True, silent=True) or {}
    try:
        entries = data.get("entry", []) or []
        for entry in entries:
            changes = entry.get("changes", []) or []
            for ch in changes:
                value = ch.get("value") or {}
                manager.route_instagram(value)
        return jsonify({"ok": True})
    except Exception as e:
        print("[IG webhook] error:", e)
        return jsonify({"ok": False}), 500

# ============ تقديم الواجهة الأمامية (اختياري) ============
FRONT = os.path.join(os.path.dirname(__file__), "..", "frontend")

@app.get("/")
def front_index():
    index_path = os.path.join(FRONT, "home.html")
    fallback = os.path.join(FRONT, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(FRONT, "home.html")
    if os.path.exists(fallback):
        return send_from_directory(FRONT, "index.html")
    return jsonify({"ok": True, "service": "piaaz-server"})

@app.get("/<path:path>")
def front_static(path):
    return send_from_directory(FRONT, path)

# ============ Health ============
@app.get("/healthz")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    # في الإنتاج (Render/Gunicorn) ما رح يُستخدم هذا غالبًا
    app.run(host="0.0.0.0", port=5000)

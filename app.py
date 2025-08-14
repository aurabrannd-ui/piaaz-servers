# app.py
# -- coding: utf-8 --
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from functools import wraps
from dotenv import load_dotenv
import os
import requests
import re

from bots.manager import BotManager

# تحميل المتغيرات من ملف .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("❌ Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env file")

app = Flask(_name_)
CORS(app)
manager = BotManager()

# ===== Rate limiting بسيط =====
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per minute"]  # تعديل حسب الحاجة
)

# ===== التحقق من التوكن =====
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401
        token = auth_header.split(" ", 1)[1]
        if not re.match(r"^[A-Za-z0-9\-\._~\+\/]+=*$", token):
            return jsonify({"error": "Invalid token format"}), 401

        try:
            resp = requests.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON_KEY},
                timeout=10
            )
        except requests.RequestException:
            return jsonify({"error": "Auth server not reachable"}), 503

        if resp.status_code != 200:
            return jsonify({"error": "Invalid or expired token"}), 401
        return f(*args, **kwargs)
    return decorated

# ===== API المحمية =====
@app.get("/api/bots")
@require_auth
@limiter.limit("50/minute")
def list_bots():
    return jsonify(manager.list())

@app.post("/api/activate")
@require_auth
@limiter.limit("20/minute")
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

# ===== Webhooks =====
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

@app.get("/webhooks/meta")
def meta_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and challenge is not None:
        if not META_VERIFY_TOKEN or token == META_VERIFY_TOKEN:
            return challenge, 200
        return "Forbidden", 403
    return "Bad Request", 400

@app.post("/webhooks/whatsapp")
def whatsapp_webhook():
    data = request.get_json(force=True, silent=True) or {}
    try:
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

# ===== Frontend =====
FRONT = os.path.join(os.path.dirname(_file_), "..", "frontend")

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

@app.get("/healthz")
def health():
    return jsonify({"ok": True})

if _name_ == "_main_":
    app.run(host="0.0.0.0", port=5000)

# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from functools import wraps
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os, requests, re, hmac, hashlib

from bots.manager import BotManager

# ================= Load env =================
load_dotenv()
SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY    = os.getenv("SUPABASE_ANON_KEY")
META_VERIFY_TOKEN    = os.getenv("META_VERIFY_TOKEN", "")
META_APP_SECRET      = os.getenv("META_APP_SECRET", "")  # optional: HMAC for Meta webhooks
PUBLIC_BASE          = os.getenv("PUBLIC_BASE", "https://piaaz.com")
FRONT_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv(
        "FRONT_ALLOWED_ORIGINS",
        f"{PUBLIC_BASE},https://www.piaaz.com,http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if o.strip()
]

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("❌ Missing SUPABASE_URL or SUPABASE_ANON_KEY in environment")

app = Flask(__name__)
# (اختياري) مفتاح سرّي للجلسات/التواقيع
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(32).hex())

# أقصى حجم للطلبات (1MB يكفي ل JSON / webhooks)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

# ================= CORS =================
CORS(app, resources={
    r"/api/*": {"origins": FRONT_ALLOWED_ORIGINS},
    # الويبهوكس لازم تكون مفتوحة لاستقبال من مزوّدين خارجيين
    r"/webhooks/*": {"origins": "*"},
})

# ================= Rate limiting =================
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per minute"],
)

manager = BotManager()

# ================= Security headers =================
@app.after_request
def harden_headers(resp):
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # CSP خفيفة حتى لا تكسر الـ inline الموجود عندك
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self' https: data: blob'; "
        "img-src 'self' https: data: blob; "
        "style-src 'self' 'unsafe-inline' https:; "
        "script-src 'self' 'unsafe-inline' https:; "
        "connect-src 'self' https:; "
        "frame-ancestors 'none';"
    )
    return resp

# ================= Supabase Bearer auth =================
token_re = re.compile(r"^[A-Za-z0-9\-\._~\+\/]+=*$")

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401

        token = auth_header.split(" ", 1)[1].strip()
        if not token_re.match(token):
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

# ================= Optional: verify Meta signature =================
def verify_meta_signature() -> bool:
    """يتحقق من X-Hub-Signature-256 إذا ضبّطت META_APP_SECRET."""
    if not META_APP_SECRET:
        return True
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not sig.startswith("sha256="):
        return False
    provided = sig.split("=", 1)[1]
    body = request.get_data() or b""
    expected = hmac.new(META_APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)

# ================= Protected API =================
@app.get("/api/bots")
@require_auth
@limiter.limit("50/minute")
def list_bots():
    return jsonify(manager.list())

@app.post("/api/activate")
@require_auth
@limiter.limit("20/minute")
def activate():
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid payload"}), 400
    if data.get("platform") not in ("telegram", "whatsapp", "instagram"):
        return jsonify({"error": "unsupported platform"}), 400
    bot_id = manager.create(data)
    return jsonify({"ok": True, "id": bot_id})

@app.post("/api/bots/<bot_id>/update")
@require_auth
@limiter.limit("30/minute")
def update_bot(bot_id):
    upd = request.get_json(force=True, silent=True)
    if not isinstance(upd, dict):
        return jsonify({"error": "invalid payload"}), 400
    manager.update(bot_id, upd)
    return jsonify({"ok": True})

@app.post("/api/bots/<bot_id>/restart")
@require_auth
@limiter.limit("20/minute")
def restart_bot(bot_id):
    manager.restart(bot_id)
    return jsonify({"ok": True})

@app.delete("/api/bots/<bot_id>")
@require_auth
@limiter.limit("20/minute")
def delete_bot(bot_id):
    manager.stop(bot_id)
    manager.bots_meta.pop(bot_id, None)
    return jsonify({"ok": True})

# أخطاء موحّدة لمسارات الـ API فقط
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "not found"}), 404
    return e

@app.errorhandler(405)
def not_allowed(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "method not allowed"}), 405
    return e

# ================= Webhooks (public) =================
# Telegram
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

# Meta verification (GET)
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

# WhatsApp
@app.post("/webhooks/whatsapp")
def whatsapp_webhook():
    if not verify_meta_signature():
        return jsonify({"error": "invalid signature"}), 401
    data = request.get_json(force=True, silent=True) or {}
    try:
        for entry in (data.get("entry") or []):
            for ch in (entry.get("changes") or []):
                manager.route_whatsapp(ch.get("value") or {})
        return jsonify({"ok": True})
    except Exception as e:
        print("[WA webhook] error:", e)
        return jsonify({"ok": False}), 500

# Instagram
@app.post("/webhooks/instagram")
def instagram_webhook():
    if not verify_meta_signature():
        return jsonify({"error": "invalid signature"}), 401
    data = request.get_json(force=True, silent=True) or {}
    try:
        for entry in (data.get("entry") or []):
            for ch in (entry.get("changes") or []):
                manager.route_instagram(ch.get("value") or {})
        return jsonify({"ok": True})
    except Exception as e:
        print("[IG webhook] error:", e)
        return jsonify({"ok": False}), 500

# ================= Frontend =================
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

# Health
@app.get("/healthz")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    # في الإنتاج (Gunicorn/Render) عادةً ما يُستبدَل بـ gunicorn
    app.run(host="0.0.0.0", port=5000)


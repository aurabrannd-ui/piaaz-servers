from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from bots.manager import BotManager
import os
import requests
from functools import wraps

# إعدادات Supabase
SUPABASE_URL = "https://roahzmbpajuxyqqeloyu.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJvYWh6bWJwYWp1eHlxcWVsb3l1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTQ5OTczNzQsImV4cCI6MjA3MDU3MzM3NH0.Zk1YE_ikgVPHxDZu_gC4uJWywZbW9plD5SJ1igprCQ8"

app = Flask(__name__)
CORS(app)
manager = BotManager()

# دالة التحقق من التوكن عبر Supabase
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401

        token = auth_header.split(" ")[1]
        resp = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": SUPABASE_ANON_KEY
            }
        )

        if resp.status_code != 200:
            return jsonify({"error": "Invalid or expired token"}), 401

        return f(*args, **kwargs)
    return decorated

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

# تقديم ملفات الواجهة الأمامية
FRONT = os.path.join(os.path.dirname(__file__), "..", "frontend")

@app.get("/")
def front_index():
    if os.path.exists(os.path.join(FRONT, "index.html")):
        return send_from_directory(FRONT, "index.html")
    return jsonify({"ok": True, "service": "piaaz-server"})

@app.get("/<path:path>")
def front_static(path):
    return send_from_directory(FRONT, path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)


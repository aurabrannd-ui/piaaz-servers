from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from bots.manager import BotManager
import os

app = Flask(__name__)
CORS(app)
manager = BotManager()

@app.get("/api/bots")
def list_bots():
    return jsonify(manager.list())

@app.post("/api/activate")
def activate():
    data = request.get_json(force=True)
    if data.get("platform") not in ("telegram","whatsapp","instagram"):
        return jsonify({"error":"unsupported platform"}), 400
    bot_id = manager.create(data)
    return jsonify({"ok":True, "id": bot_id})

@app.post("/api/bots/<bot_id>/update")
def update_bot(bot_id):
    upd = request.get_json(force=True)
    manager.update(bot_id, upd)
    return jsonify({"ok":True})

@app.post("/api/bots/<bot_id>/restart")
def restart_bot(bot_id):
    manager.restart(bot_id)
    return jsonify({"ok":True})

@app.delete("/api/bots/<bot_id>")
def delete_bot(bot_id):
    manager.stop(bot_id)
    manager.bots_meta.pop(bot_id, None)
    return jsonify({"ok":True})

# (اختياري) لو بدك تقدّم ملفات الواجهة من نفس الريبل
FRONT = os.path.join(os.path.dirname(__file__), "..", "frontend")
@app.get("/")
def front_index():
    if os.path.exists(os.path.join(FRONT,"index.html")):
        return send_from_directory(FRONT, "index.html")
    return jsonify({"ok":True, "service":"piaaz-server"})

@app.get("/<path:path>")
def front_static(path):
    return send_from_directory(FRONT, path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)



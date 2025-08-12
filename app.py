import time
from typing import Dict
from bots.tg_bot import TelegramClientBot

class BotManager:
    def __init__(self):
        self.bots_meta: Dict[str, dict] = {}   # id -> meta/config
        self.bots_obj: Dict[str, object] = {}  # id -> running object

    def list(self):
        out=[]
        for i,meta in self.bots_meta.items():
            out.append({
                "id": i,
                "platform": meta["platform"],
                "active": bool(self.bots_obj.get(i)),
                "reply_mode": meta.get("reply_mode","text"),
                "company": meta.get("company",{}),
            })
        return out

    def create(self, meta: dict) -> str:
        bot_id = meta.get("id") or f"bot_{int(time.time()*1000)}"
        meta["id"] = bot_id
        self.bots_meta[bot_id] = meta
        self.start(bot_id)
        return bot_id

    def start(self, bot_id: str):
        meta = self.bots_meta[bot_id]
        platform = meta["platform"]
        if platform == "telegram":
            tg_token   = meta["creds"].get("tgToken","")
            openai_key = meta["creds"].get("openai","")
            profile = {
                "reply_mode": meta.get("reply_mode","text"),
                "company": meta.get("company",{}),
                "voice": meta.get("voice")
            }
            bot = TelegramClientBot(bot_id, tg_token, openai_key, profile)
            bot.start()
            self.bots_obj[bot_id] = bot
        else:
            self.bots_obj[bot_id] = None  # WhatsApp/Instagram لاحقًا

    def stop(self, bot_id:str):
        bot = self.bots_obj.get(bot_id)
        if bot:
            try: bot.stop()
            except: pass
        self.bots_obj[bot_id] = None

    def restart(self, bot_id:str):
        self.stop(bot_id)
        self.start(bot_id)

    def update(self, bot_id:str, meta_update: dict):
        m = self.bots_meta.get(bot_id)
        if not m: return
        for k,v in meta_update.items():
            if k=="company" and isinstance(v,dict):
                m.setdefault("company",{}).update(v)
            elif k=="creds" and isinstance(v,dict):
                m.setdefault("creds",{}).update(v)
            else:
                m[k]=v
        bot = self.bots_obj.get(bot_id)
        if bot:
            profile = {
                "reply_mode": m.get("reply_mode","text"),
                "company": m.get("company",{}),
                "voice": m.get("voice")
            }
            new_openai = m.get("creds",{}).get("openai")
            bot.update_profile(profile, new_openai)


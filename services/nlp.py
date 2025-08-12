import requests

OPENAI_BASE = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"

def generate_reply(openai_key: str, system_prompt: str, history: list, user_text: str) -> str:
    headers = {"Authorization": f"Bearer {openai_key}"}
    messages = [{"role":"system","content": system_prompt}] + history + [{"role":"user","content": user_text}]
    data = {"model": OPENAI_MODEL, "messages": messages, "temperature": 0.7}
    r = requests.post(OPENAI_BASE, json=data, headers=headers, timeout=45)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

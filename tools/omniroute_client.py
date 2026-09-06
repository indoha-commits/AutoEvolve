from __future__ import annotations
import json, os, httpx
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("CODING_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/")
KEY = os.getenv("CODING_API_KEY", "")

def complete_json(model: str, system: str, user: str, timeout: int = 120) -> dict:
    response = httpx.post(
        f"{BASE}/chat/completions",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role":"system","content":system},
                {"role":"user","content":user}
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    # tolerate fenced JSON
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n",1)[1]
        content = content.rsplit("```",1)[0]
    start = content.find("{"); end = content.rfind("}")
    if start >= 0 and end >= start:
        content = content[start:end+1]
    return json.loads(content)

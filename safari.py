import os
import json
import hashlib
import time
from datetime import datetime
from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from groq import Groq
import httpx

# Config
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "your-key-here")
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
groq = Groq(api_key=GROQ_API_KEY)
users = {}
chats = {}

# Tools
def search_web(q):
    try:
        r = httpx.get(f"https://api.duckduckgo.com/?q={q}&format=json", timeout=10)
        d = r.json()
        return d.get("Abstract", "") or d.get("RelatedTopics", [{}])[0].get("Text", "")[:1000]
    except:
        return ""

# Brain
def think(msg, hist=""):
    try:
        msgs = [{"role": "system", "content": "You are Safari AI, created by Safari Softwares. Be warm, friendly, and enthusiastic. Use emojis naturally. Remember everything the user said in this conversation and refer back to it. When users ask follow-up questions, show that you remember earlier context. Structure answers clearly with bullet points when helpful. Be honest about your capabilities. If you do not know something, say so. Keep a positive, encouraging tone."}]
        if hist:
            for line in hist.split("\n"):
                if line.startswith("U:"): msgs.append({"role": "user", "content": line[2:]})
                elif line.startswith("S:"): msgs.append({"role": "assistant", "content": line[2:]})
        msgs.append({"role": "user", "content": msg})
        r = groq.chat.completions.create(model="llama-3.1-8b-instant", messages=msgs, temperature=0.3, max_tokens=400)
        return r.choices[0].message.content
    except Exception as e:
        return f"Error: {e}"

# Routes
@app.post("/register")
async def register(email: str = Form(...)):
    key = hashlib.sha256(f"{email}{time.time()}".encode()).hexdigest()[:32]
    users[key] = {"email": email, "plan": "free", "queries": 0, "date": datetime.now().date().isoformat()}
    return {"api_key": key, "plan": "free", "limit": 10}

@app.post("/ask")
async def ask(question: str = Form(...), session: str = Form(default="default")):
    if session not in chats: chats[session] = []
    hist = "\n".join(chats[session][-6:])
    resp = think(question, hist)
    chats[session].append(f"U:{question}")
    chats[session].append(f"S:{resp}")
    return {"response": resp}

@app.get("/", response_class=HTMLResponse)
async def home():
    return """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Safari AI</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:Segoe UI,sans-serif;background:#f5e6d3;min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px}
.c{background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,.2);width:100%;max-width:700px;height:90vh;display:flex;flex-direction:column;overflow:hidden}
.h{background:linear-gradient(135deg,#d2691e,#8b4513);color:#fff;padding:20px;display:flex;align-items:center;gap:12px}
.h h1{font-size:20px}.h p{font-size:11px;opacity:.9}
#b{flex:1;overflow-y:auto;padding:20px;background:#fffaf5}
.m{max-width:80%;padding:12px 16px;border-radius:18px;margin:8px 0;word-wrap:break-word}
.u{background:#8b4513;color:#fff;margin-left:auto;border-bottom-right-radius:6px}
.s{background:#fff;border:2px solid #d2691e;margin-right:auto;border-bottom-left-radius:6px}
.i{display:flex;padding:15px;background:#fff;border-top:1px solid #f0e0d0;gap:10px}
#q{flex:1;padding:14px;border:2px solid #e0c8a8;border-radius:30px;font-size:15px;outline:0}
#q:focus{border-color:#d2691e}
button{background:#d2691e;color:#fff;border:0;padding:14px 28px;border-radius:30px;cursor:pointer;font-weight:700}
button:hover{background:#8b4513}
.f{text-align:center;padding:8px;font-size:10px;color:#999}
.f a{color:#d2691e}</style></head><body>
<div class="c"><div class="h"><span style="font-size:32px">🦁</span><div><h1>🦁 Safari AI Agent</h1><p>Explore Beyond Limits</p></div></div>
<div id="b"><div class="m s">🦁 Hello! Ask me anything!</div></div>
<div class="i"><input id="q" placeholder="Type your question..." autofocus onkeypress="if(event.key==='Enter')ask()"><button onclick="ask()">Ask</button></div>
<div class="f">2026 Safari Softwares | <a href="/terms">Terms</a> | <a href="/privacy">Privacy</a></div></div>
<script>const sid='s'+Math.random().toString(36).substr(2,9);
async function ask(){const i=document.getElementById('q'),b=document.getElementById('b'),q=i.value.trim();if(!q)return;b.innerHTML+='<div class="m u">'+q+'</div>';i.value='';b.scrollTop=b.scrollHeight;
const f=new FormData();f.append('question',q);f.append('session',sid);
const r=await fetch('/ask',{method:'POST',body:f});const d=await r.json();b.innerHTML+='<div class="m s">'+d.response+'</div>';b.scrollTop=b.scrollHeight;}</script></body></html>"""

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
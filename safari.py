"""
🦁 Safari AI Agent - Professional Edition
Copyright (c) 2026 Safari Softwares. All rights reserved.
"""

import json
import hashlib
import time
import os
from datetime import datetime
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from groq import Groq
import httpx

# ============================================
# CONFIGURATION
# ============================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "your-groq-api-key-here")
FREE_QUERIES_PER_DAY = 10
PAID_QUERIES_PER_DAY = 1000

app = FastAPI(title="🦁 Safari AI Agent", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

groq_client = Groq(api_key=GROQ_API_KEY)

# ============================================
# SIMPLE DATABASE
# ============================================
users = {}
conversations = {}

def load_users():
    global users
    try:
        with open("users.json", "r") as f:
            users = json.load(f)
    except:
        users = {}

def save_users():
    with open("users.json", "w") as f:
        json.dump(users, f, indent=2)

load_users()

# ============================================
# TOOLS
# ============================================
def fetch_url(url):
    try:
        headers = {"User-Agent": "SafariAIAgent/1.0"}
        resp = httpx.get(url, timeout=10, headers=headers)
        return resp.text[:2000]
    except:
        return ""

def search_web(query):
    try:
        resp = httpx.get(f"https://api.duckduckgo.com/?q={query}&format=json", timeout=10)
        data = resp.json()
        result = data.get("Abstract", "")
        if not result:
            topics = data.get("RelatedTopics", [])
            if topics:
                result = topics[0].get("Text", "")
        return result[:1500] if result else "No results"
    except:
        return "Search unavailable"

# ============================================
# SAFARI BRAIN (FIXED)
# ============================================
def safari_think(message, history=""):
    try:
        # Build conversation context
        messages = [
            {"role": "system", "content": """🦁 You are Safari AI, a friendly and enthusiastic assistant by Safari Softwares.

PERSONALITY:
- Use emojis naturally in your responses 🦁✨🚀
- Be warm, friendly, and conversational  
- Match emojis to the topic (🍳 cooking, 💻 tech, 🌍 travel, ⚽ sports, 📚 education)
- Use 🦁 regularly - it's your mascot and signature!
- Be playful but professional
- Show excitement with 🎉, curiosity with 🤔, appreciation with 🙏

EXAMPLE RESPONSES:
- "Great question! 🦁 Let me help you with that..."
- "I found the answer! ✨ Here's what I discovered..."
- "That's fascinating! 🤔 Let me search for more details..."

RULES:
1. Remember previous messages in the conversation
2. Be consistent with earlier answers
3. Admit when you don't know something
4. Keep answers helpful and concise
5. Always stay positive and encouraging 🦁"""} 
            
IMPORTANT RULES:
1. Remember what was discussed earlier in the conversation
2. If user asks about something mentioned before, refer back to it
3. Be consistent with your previous answers
4. Admit when you don't know something instead of guessing
5. Keep answers concise and relevant
6. Use web search only when you genuinely need current information"""}
        ]
        
        # Add conversation history
        if history:
            for line in history.split('\n'):
                if line.startswith("User: "):
                    messages.append({"role": "user", "content": line[6:]})
                elif line.startswith("Safari: "):
                    messages.append({"role": "assistant", "content": line[8:]})
        
        # Add current message
        messages.append({"role": "user", "content": message})
        
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.3,
            max_tokens=500
        )
        
        return response.choices[0].message.content
    
    except Exception as e:
        return f"I'm having trouble processing that. Could you rephrase? Error: {str(e)}"
        
        # Check for tool use
        if "TOOL:" in reply and "INPUT:" in reply:
            lines = reply.split('\n')
            tool_name = ""
            tool_input = ""
            
            for line in lines:
                if line.startswith("TOOL:"):
                    tool_name = line.replace("TOOL:", "").strip().lower()
                if line.startswith("INPUT:"):
                    tool_input = line.replace("INPUT:", "").strip()
            
            if tool_name and tool_input:
                if "search_web" in tool_name:
                    result = search_web(tool_input)
                elif "fetch_url" in tool_name:
                    result = fetch_url(tool_input)
                else:
                    result = ""
                
                final = groq_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": "You are Safari AI. Answer naturally based on the information provided."},
                        {"role": "user", "content": f"Question: {message}\n\nInformation found: {result}\n\nProvide a helpful, natural answer."}
                    ],
                    temperature=0.3,
                    max_tokens=500
                )
                
                return final.choices[0].message.content
        
        return reply
    
    except Exception as e:
        return f"Error: {str(e)}"

# ============================================
# API ENDPOINTS
# ============================================

class QueryJSON(BaseModel):
    message: str
    session_id: str = "default"

@app.post("/register")
async def register(email: str = Form(...), plan: str = Form(default="free")):
    api_key = hashlib.sha256(f"{email}{time.time()}".encode()).hexdigest()[:32]
    users[api_key] = {
        "email": email,
        "plan": plan,
        "queries_today": 0,
        "total_queries": 0,
        "last_reset": datetime.now().date().isoformat()
    }
    save_users()
    limit = FREE_QUERIES_PER_DAY if plan == "free" else PAID_QUERIES_PER_DAY
    return {"api_key": api_key, "plan": plan, "daily_limit": limit, "message": "Keep this key safe!"}

@app.post("/chat")
async def chat(query: QueryJSON, request: Request):
    api_key = request.headers.get("X-API-Key", "")
    
    if not api_key or api_key not in users:
        raise HTTPException(403, "Invalid API key. Register at /register")
    
    user = users[api_key]
    today = datetime.now().date().isoformat()
    
    if user.get("last_reset") != today:
        user["queries_today"] = 0
        user["last_reset"] = today
    
    limit = FREE_QUERIES_PER_DAY if user["plan"] == "free" else PAID_QUERIES_PER_DAY
    if user["queries_today"] >= limit:
        raise HTTPException(429, f"Daily limit reached ({limit}). Upgrade to Pro!")
    
    try:
        sid = query.session_id
        if sid not in conversations:
            conversations[sid] = []
        
        history = "\n".join(conversations[sid][-6:])
        
        response = safari_think(query.message, history)
        
        conversations[sid].append(f"User: {query.message}")
        conversations[sid].append(f"Safari: {response}")
        
        user["queries_today"] += 1
        user["total_queries"] += 1
        save_users()
        
        remaining = limit - user["queries_today"]
        
        return {"response": response, "status": "success", "remaining_queries": remaining}
    
    except Exception as e:
        return {"response": f"Error: {str(e)}", "status": "error"}

@app.post("/ask")
async def ask(question: str = Form(...), session_id: str = Form(default="default")):
    try:
        if session_id not in conversations:
            conversations[session_id] = []
        
        history = "\n".join(conversations[session_id][-6:])
        response = safari_think(question, history)
        
        conversations[session_id].append(f"User: {question}")
        conversations[session_id].append(f"Safari: {response}")
        
        return {"response": response, "status": "success"}
    except Exception as e:
        return {"response": f"Error: {str(e)}", "status": "error"}

@app.get("/", response_class=HTMLResponse)
async def home():
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🦁 Safari AI Agent</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #f5e6d3; min-height: 100vh; display: flex; justify-content: center; align-items: center; padding: 20px; }
        .container { background: white; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.2); width: 100%; max-width: 700px; height: 90vh; display: flex; flex-direction: column; overflow: hidden; }
        .header { background: linear-gradient(135deg, #d2691e, #8b4513); color: white; padding: 20px; display: flex; align-items: center; gap: 12px; }
        .header h1 { font-size: 20px; } .header p { font-size: 11px; opacity: 0.9; }
        #chatBox { flex: 1; overflow-y: auto; padding: 20px; background: #fffaf5; }
        .msg { max-width: 80%; padding: 12px 16px; border-radius: 18px; margin: 8px 0; word-wrap: break-word; }
        .user { background: #8b4513; color: white; margin-left: auto; border-bottom-right-radius: 6px; }
        .bot { background: white; border: 2px solid #d2691e; margin-right: auto; border-bottom-left-radius: 6px; }
        .input-area { display: flex; padding: 15px; background: white; border-top: 1px solid #f0e0d0; gap: 10px; }
        #q { flex: 1; padding: 14px; border: 2px solid #e0c8a8; border-radius: 30px; font-size: 15px; outline: none; }
        #q:focus { border-color: #d2691e; }
        button { background: #d2691e; color: white; border: none; padding: 14px 28px; border-radius: 30px; cursor: pointer; font-weight: bold; }
        button:hover { background: #8b4513; }
        .footer { text-align: center; padding: 8px; font-size: 10px; color: #999; }
        .footer a { color: #d2691e; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <span style="font-size:32px;">🦁</span>
            <div><h1>Safari AI Agent</h1><p>by Safari Softwares</p></div>
        </div>
        <div id="chatBox"><div class="msg bot">🦁 Hello! I'm Safari AI Agent. How can I help you?</div></div>
        <div class="input-area">
            <input id="q" placeholder="Type your question..." autofocus onkeypress="if(event.key==='Enter')ask()">
            <button onclick="ask()">Ask</button>
        </div>
        <div class="footer">© 2026 Safari Softwares | <a href="/terms">Terms</a> | <a href="/privacy">Privacy</a></div>
    </div>
    <script>
        const sid = 'safari_' + Math.random().toString(36).substr(2,9);
        async function ask() {
            const input = document.getElementById('q');
            const chat = document.getElementById('chatBox');
            const q = input.value.trim();
            if(!q) return;
            chat.innerHTML += `<div class="msg user">${q}</div>`;
            input.value = '';
            chat.scrollTop = chat.scrollHeight;
            const fd = new FormData();
            fd.append('question', q);
            fd.append('session_id', sid);
            const resp = await fetch('/ask', { method: 'POST', body: fd });
            const data = await resp.json();
            chat.innerHTML += `<div class="msg bot">${data.response}</div>`;
            chat.scrollTop = chat.scrollHeight;
        }
    </script>
</body>
</html>"""

@app.get("/terms")
async def terms():
    try:
        with open("TERMS.md", "r") as f:
            return HTMLResponse(f"<pre>{f.read()}</pre>")
    except:
        return {"message": "Terms of Service - Safari Softwares"}

@app.get("/privacy")
async def privacy():
    try:
        with open("PRIVACY.md", "r") as f:
            return HTMLResponse(f"<pre>{f.read()}</pre>")
    except:
        return {"message": "Privacy Policy - Safari Softwares"}

@app.get("/health")
async def health():
    return {"status": "healthy 🦁", "users": len(users)}

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("🦁 Safari AI Agent Ready!")
    print("http://localhost:8000")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)
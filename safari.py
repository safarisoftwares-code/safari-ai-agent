import os
import json
import hashlib
import time
from datetime import datetime
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from groq import Groq
import httpx

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "your-groq-key-here")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

groq_client = Groq(api_key=GROQ_API_KEY)

users = {}
chats = {}
request_counts = {}
LAST_CLEANUP = time.time()

def load_users():
    global users
    try:
        with open("users.json", "r") as f:
            users = json.load(f)
    except:
        users = {}

def save_users():
    try:
        with open("users.json", "w") as f:
            json.dump(users, f, indent=2)
    except:
        pass

def cleanup_old_chats():
    global chats, LAST_CLEANUP
    now = time.time()
    if now - LAST_CLEANUP > 3600:  # Cleanup every hour
        # Remove chats older than 24 hours (based on session ID timestamp)
        to_delete = []
        for session_id in chats:
            try:
                if session_id.startswith("chat_"):
                    chat_time = int(session_id.split("_")[1]) / 1000
                    if now - chat_time > 86400:  # 24 hours
                        to_delete.append(session_id)
            except:
                pass
        for sid in to_delete:
            del chats[sid]
        LAST_CLEANUP = now

def sanitize_input(text):
    # Remove any potentially harmful characters
    return text.replace("<", "&lt;").replace(">", "&gt;").strip()[:1000]

load_users()

def think(msg, hist=""):
    try:
        msgs = [{"role": "system", "content": "You are Safari AI by Safari Softwares. Be helpful, friendly, use emojis. You are text-only. Keep answers concise. If you need current information, I will provide it."}]
        if hist:
            for line in hist.split("\n")[-4:]:
                if line.startswith("U:"): msgs.append({"role": "user", "content": line[2:]})
                elif line.startswith("S:"): msgs.append({"role": "assistant", "content": line[2:]})
        msgs.append({"role": "user", "content": msg})
        
        # Search for factual/current topics
        factual_triggers = [
            "who is", "what is", "when did", "where is", "why did", "how many",
            "president", "election", "today", "current", "latest", "news",
            "2024", "2025", "2026", "price", "score", "weather", "now",
            "world", "affairs", "recent", "happening", "hacked", "incident",
            "usa", "china", "kenya", "uk", "france", "leader", "prime minister",
            "capital of", "population", "currency", "stock", "bitcoin", "crypto",
            "where is", "located", "globe", "map"
        ]
        
        needs_search = any(kw in msg.lower() for kw in factual_triggers)
        
        if needs_search:
            result = ""
            try:
                query = msg.lower()
                # Try Wikipedia
                resp = httpx.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}",
                    timeout=5,
                    headers={"User-Agent": "SafariAI/1.0"}
                )
                if resp.status_code == 200:
                    result = resp.json().get("extract", "")[:800]
                
                # Fallback to DuckDuckGo
                if not result:
                    resp2 = httpx.get(
                        f"https://api.duckduckgo.com/?q={msg}&format=json",
                        timeout=5
                    )
                    d = resp2.json()
                    result = d.get("Abstract", "") or ""
            except:
                pass
            
            if result:
                msgs.append({"role": "user", "content": f"Real-time data:\n{result}\n\nAnswer: {msg}"})
        
        r = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=msgs,
            temperature=0.3,
            max_tokens=300
        )
        return r.choices[0].message.content
        
    except Exception as e:
        return f"Error: {e}"

@app.post("/register")
async def register(email: str = Form(...)):
    # Validate email format
    if "@" not in email or "." not in email or len(email) > 100:
        raise HTTPException(status_code=400, detail="Invalid email format")
    key = hashlib.sha256(f"{email}{time.time()}".encode()).hexdigest()[:32]
    users[key] = {"email": email, "plan": "free", "queries": 0, "date": datetime.now().date().isoformat()}
    save_users()
    return {"api_key": key, "plan": "free", "limit": 10}

@app.post("/ask")
async def ask(question: str = Form(...), session: str = Form(default="default")):
    # Input validation
    question = sanitize_input(question)
    if not question or len(question) < 1:
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if len(question) > 1000:
        raise HTTPException(status_code=400, detail="Question too long (max 1000 characters)")
    
    # Rate limiting: 20 requests per minute per session
    now = time.time()
    if session in request_counts:
        request_counts[session] = [t for t in request_counts[session] if now - t < 60]
        if len(request_counts[session]) >= 20:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait a moment.")
    else:
        request_counts[session] = []
    request_counts[session].append(now)
    
    # Periodic cleanup
    cleanup_old_chats()
    
    # Validate session ID
    if len(session) > 50:
        session = session[:50]
    
    if session not in chats: chats[session] = []
    hist = "\n".join(chats[session][-6:])
    resp = think(question, hist)
    chats[session].append(f"U:{question}")
    chats[session].append(f"S:{resp}")
    
    # Limit chat history to 100 messages per session
    if len(chats[session]) > 100:
        chats[session] = chats[session][-100:]
    
    return {"response": resp}

@app.post("/delete-data")
async def delete_data(session: str = Form(...)):
    if session in chats:
        del chats[session]
        return {"status": "deleted", "message": "Your chat data has been permanently deleted."}
    return {"status": "not_found", "message": "No data found for this session."}

@app.get("/", response_class=HTMLResponse)
async def home():
    return """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Safari AI</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Segoe UI,sans-serif;background:#f5e6d3;min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px}
.c{background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,.2);width:100%;max-width:700px;height:90vh;display:flex;flex-direction:column;overflow:hidden}
.h{background:linear-gradient(135deg,#d2691e,#8b4513);color:#fff;padding:20px;display:flex;align-items:center;gap:12px}
.h h1{font-size:20px}.h p{font-size:11px;opacity:.9}
.tabs{display:flex;overflow-x:auto;background:#fff;border-bottom:2px solid #f0e0d0;padding:0 5px;min-height:40px;align-items:flex-end}
.tab{padding:8px 16px;background:#f5e6d3;border:1px solid #e0c8a8;border-bottom:0;border-radius:10px 10px 0 0;margin:0 3px;cursor:pointer;white-space:nowrap;font-size:13px;position:relative;max-width:150px;overflow:hidden;text-overflow:ellipsis}
.tab.active{background:#fff;border-bottom:2px solid #fff;margin-bottom:-2px;font-weight:bold;color:#8b4513}
.tab .del{position:absolute;right:3px;top:3px;width:18px;height:18px;background:#ff6b6b;color:#fff;border-radius:50%;display:none;align-items:center;justify-content:center;font-size:12px;line-height:1;cursor:pointer}
.tab:hover .del{display:flex}
.tab.add{background:#d2691e;color:#fff;font-weight:bold;font-size:18px;padding:8px 12px;border-radius:10px 10px 0 0}
.tab.add:hover{background:#8b4513}
#b{flex:1;overflow-y:auto;padding:20px;background:#fffaf5}
.m{max-width:80%;padding:12px 16px;border-radius:18px;margin:8px 0;word-wrap:break-word;overflow-wrap:break-word}
.u{background:#8b4513;color:#fff;margin-left:auto;border-bottom-right-radius:6px}
.s{background:#fff;border:2px solid #d2691e;margin-right:auto;border-bottom-left-radius:6px}
.i{display:flex;padding:15px;background:#fff;border-top:1px solid #f0e0d0;gap:10px}
#q{flex:1;padding:14px;border:2px solid #e0c8a8;border-radius:30px;font-size:15px;outline:0}
#q:focus{border-color:#d2691e}
button{background:#d2691e;color:#fff;border:0;padding:14px 28px;border-radius:30px;cursor:pointer;font-weight:700}
button:hover{background:#8b4513}
.f{text-align:center;padding:8px;font-size:10px;color:#999}
.f a{color:#d2691e}
</style></head><body>
<div class="c">
<div class="h"><span style="font-size:32px">🦁</span><div><h1>Safari AI Agent</h1><p>Explore Beyond Limits</p></div></div>
<div class="tabs" id="tabs"></div>
<div id="b"></div>
<div class="i"><input id="q" placeholder="Type your question..." autofocus onkeypress="if(event.key==='Enter')ask()"><button onclick="ask()">Ask</button></div>
<div class="f">2026 Safari Softwares | <a href="/terms">Terms</a> | <a href="/privacy">Privacy</a></div>
</div>
<script>
let chats={};
let activeChat=null;

function loadChats(){
    try{
        const saved=localStorage.getItem('safari_chats');
        if(saved) chats=JSON.parse(saved);
    }catch(e){}
    if(Object.keys(chats).length===0){
        const id='chat_'+Date.now();
        chats[id]={name:'New Chat',messages:[]};
        saveChats();
    }
}

function saveChats(){
    try{
        localStorage.setItem('safari_chats',JSON.stringify(chats));
    }catch(e){}
}

function getChatPreview(messages){
    if(!messages||messages.length===0) return 'New Chat';
    const first=messages[0];
    if(first.startsWith('U:')) return first.substring(2).substring(0,30);
    return 'Chat';
}

function sanitize(str){
    return str.replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderTabs(){
    const tabs=document.getElementById('tabs');
    tabs.innerHTML='';
    const chatIds=Object.keys(chats);
    chatIds.forEach(id=>{
        const chat=chats[id];
        if(!chat.name||chat.name==='New Chat'){
            chat.name=getChatPreview(chat.messages);
        }
        const tab=document.createElement('div');
        tab.className='tab'+(id===activeChat?' active':'');
        const displayName=chat.name.length>20?chat.name.substring(0,20)+'...':chat.name;
        tab.innerHTML=sanitize(displayName);
        tab.title=chat.name;
        tab.addEventListener('click', function(e) {
            if (e.target.classList.contains('del')) return;
            switchChat(id);
        });
        if(chatIds.length>1){
            const del=document.createElement('span');
            del.className='del';
            del.innerHTML='×';
            del.title='Delete chat';
            del.addEventListener('click', function(e) {
                e.stopPropagation();
                e.preventDefault();
                if(confirm('Delete this chat permanently?')) {
                    deleteChat(id);
                }
            });
            tab.appendChild(del);
        }
        tabs.appendChild(tab);
    });
    const addBtn=document.createElement('div');
    addBtn.className='tab add';
    addBtn.innerHTML='+';
    addBtn.title='New Chat';
    addBtn.addEventListener('click', newChat);
    tabs.appendChild(addBtn);
}

function switchChat(id){
    activeChat=id;
    renderTabs();
    renderMessages();
}

function newChat(){
    const id='chat_'+Date.now();
    chats[id]={name:'New Chat',messages:[]};
    activeChat=id;
    saveChats();
    renderTabs();
    renderMessages();
}

function deleteChat(id){
    if(Object.keys(chats).length<=1) return;
    delete chats[id];
    saveChats();
    if(activeChat===id){
        activeChat=Object.keys(chats)[0];
    }
    renderTabs();
    renderMessages();
}

function renderMessages(){
    const box=document.getElementById('b');
    box.innerHTML='';
    if(!activeChat||!chats[activeChat]) return;
    const msgs=chats[activeChat].messages||[];
    msgs.forEach(m=>{
        if(m.startsWith('U:')){
            box.innerHTML+='<div class="m u">'+sanitize(m.substring(2))+'</div>';
        }else if(m.startsWith('S:')){
            box.innerHTML+='<div class="m s">'+sanitize(m.substring(2))+'</div>';
        }
    });
    if(msgs.length===0){
        box.innerHTML='<div class="m s">🦁 Hello! Ask me anything!</div>';
    }
    box.scrollTop=box.scrollHeight;
}

async function ask(){
    const input=document.getElementById('q');
    const question=input.value.trim();
    if(!question) return;
    if(question.length>1000){
        alert('Message too long. Please keep it under 1000 characters.');
        return;
    }
    if(!activeChat||!chats[activeChat]){
        newChat();
    }
    if(!chats[activeChat].messages) chats[activeChat].messages=[];
    chats[activeChat].messages.push('U:'+question);
    if(chats[activeChat].name==='New Chat'){
        chats[activeChat].name=getChatPreview(chats[activeChat].messages);
    }
    saveChats();
    renderTabs();
    renderMessages();
    input.value='';
    
    try{
        const form=new FormData();
        form.append('question',question);
        form.append('session',activeChat);
        const r=await fetch('/ask',{method:'POST',body:form});
        if(r.status===429){
            chats[activeChat].messages.push('S:⚠️ Rate limit reached. Please wait a moment before sending another message.');
        }else{
            const d=await r.json();
            chats[activeChat].messages.push('S:'+d.response);
        }
    }catch(e){
        chats[activeChat].messages.push('S:⚠️ Connection error. Please try again.');
    }
    saveChats();
    renderMessages();
}

loadChats();
if(!activeChat) activeChat=Object.keys(chats)[0]||'chat_'+Date.now();
if(!chats[activeChat]){chats[activeChat]={name:'New Chat',messages:[]};saveChats();}
renderTabs();
renderMessages();
</script></body></html>"""

@app.get("/terms", response_class=HTMLResponse)
async def terms():
    return """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Terms of Service - Safari AI</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Segoe UI,sans-serif;background:#f5e6d3;min-height:100vh;padding:20px;color:#333}
.container{max-width:800px;margin:0 auto;background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,.2);padding:40px}
h1{color:#8b4513;font-size:28px;margin-bottom:10px}
h2{color:#d2691e;font-size:20px;margin:25px 0 10px}
p{margin:10px 0;line-height:1.6}
ul{margin:10px 0 10px 20px;line-height:1.6}
a{color:#d2691e}
.back{display:inline-block;margin-top:30px;background:#d2691e;color:#fff;padding:12px 24px;border-radius:30px;text-decoration:none;font-weight:bold}
.back:hover{background:#8b4513}
</style></head><body>
<div class="container">
<h1>🦁 Terms of Service</h1>
<p><strong>Last Updated:</strong> August 8, 2026</p>

<h2>1. Acceptance of Terms</h2>
<p>By accessing or using Safari AI ("the Service"), provided by Safari Softwares ("we," "us," or "our"), you agree to be bound by these Terms of Service. If you do not agree to these terms, please discontinue use of the Service immediately.</p>

<h2>2. Description of Service</h2>
<p>Safari AI is an AI-powered conversational assistant that uses large language model technology to respond to user queries. The Service is provided for informational and entertainment purposes only.</p>

<h2>3. User Eligibility</h2>
<p>By using the Service, you represent that you are at least 13 years of age. The Service is not directed at children under 13, and we do not knowingly collect information from children under 13.</p>

<h2>4. User Conduct and Responsibilities</h2>
<p>You agree not to:</p>
<ul>
<li>Use the Service for any illegal, fraudulent, or unauthorized purpose</li>
<li>Attempt to disrupt, overload, or impair the Service or its servers</li>
<li>Use the Service to generate, distribute, or promote harmful, abusive, harassing, defamatory, or deceptive content</li>
<li>Attempt to reverse engineer, decompile, or extract the source code of the Service</li>
<li>Use automated means (bots, scrapers) to access the Service without permission</li>
<li>Violate any applicable local, national, or international laws or regulations</li>
<li>Upload or transmit viruses, malware, or malicious code</li>
</ul>

<h2>5. Intellectual Property Rights</h2>
<p>The Safari AI name, logo, branding, interface design, and underlying code are the exclusive intellectual property of Safari Softwares. You are granted a limited, non-exclusive, non-transferable license to use the Service for personal, non-commercial purposes. You may not copy, modify, distribute, sell, or create derivative works from any part of the Service without our express written permission.</p>

<h2>6. User-Generated Content</h2>
<p>By submitting queries to the Service, you grant us a worldwide, royalty-free license to use, process, and store such content solely for the purpose of providing and improving the Service. You retain ownership of your content but are solely responsible for its legality and appropriateness.</p>

<h2>7. AI-Generated Content Disclaimer</h2>
<p>Responses generated by Safari AI are produced by artificial intelligence and may contain errors, inaccuracies, biases, or outdated information. The Service should not be relied upon for:</p>
<ul>
<li>Medical, legal, financial, or professional advice</li>
<li>Emergency situations or critical decisions</li>
<li>Factual verification without independent confirmation</li>
</ul>
<p>Always verify important information through authoritative sources.</p>

<h2>8. Third-Party Services</h2>
<p>The Service utilizes third-party APIs and services, including Groq. We are not responsible for the availability, accuracy, or practices of third-party services. Your use of the Service constitutes acceptance of any applicable third-party terms.</p>

<h2>9. Disclaimer of Warranties</h2>
<p>THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, ACCURACY, RELIABILITY, OR NON-INFRINGEMENT. WE DO NOT WARRANT THAT THE SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR SECURE.</p>

<h2>10. Limitation of Liability</h2>
<p>TO THE MAXIMUM EXTENT PERMITTED BY LAW, SAFARI SOFTWARES SHALL NOT BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR EXEMPLARY DAMAGES ARISING FROM YOUR USE OF OR INABILITY TO USE THE SERVICE, INCLUDING BUT NOT LIMITED TO DAMAGES FOR LOSS OF DATA, PROFITS, OR GOODWILL.</p>

<h2>11. Indemnification</h2>
<p>You agree to indemnify and hold harmless Safari Softwares and its affiliates from any claims, damages, or expenses arising from your use of the Service or violation of these Terms.</p>

<h2>12. Service Availability</h2>
<p>We strive to maintain Service availability but do not guarantee uninterrupted access. We reserve the right to modify, suspend, or discontinue the Service at any time without notice.</p>

<h2>13. Rate Limiting</h2>
<p>To ensure fair usage, the Service employs rate limiting of 20 requests per minute per session. Excessive use may result in temporary or permanent access restrictions.</p>

<h2>14. Termination</h2>
<p>We reserve the right to terminate or restrict access to the Service for any reason, including violation of these Terms, without prior notice.</p>

<h2>15. Changes to Terms</h2>
<p>We may modify these Terms at any time. Changes become effective immediately upon posting. Your continued use of the Service after modifications constitutes acceptance of the updated Terms. We encourage periodic review of these Terms.</p>

<h2>16. Governing Law</h2>
<p>These Terms shall be governed by applicable laws. Any disputes shall be resolved through good-faith negotiations before pursuing other remedies.</p>

<h2>17. Severability</h2>
<p>If any provision of these Terms is found to be unenforceable, the remaining provisions shall remain in full force and effect.</p>

<h2>18. Contact Information</h2>
<p>For questions, concerns, or legal notices regarding these Terms, contact:</p>
<p>📧 <a href="mailto:safari.ai.agent@gmail.com">safari.ai.agent@gmail.com</a></p>

<a href="/" class="back">← Back to Safari AI</a>
</div></body></html>"""

@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Privacy Policy - Safari AI</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Segoe UI,sans-serif;background:#f5e6d3;min-height:100vh;padding:20px;color:#333}
.container{max-width:800px;margin:0 auto;background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,.2);padding:40px}
h1{color:#8b4513;font-size:28px;margin-bottom:10px}
h2{color:#d2691e;font-size:20px;margin:25px 0 10px}
p{margin:10px 0;line-height:1.6}
ul{margin:10px 0 10px 20px;line-height:1.6}
a{color:#d2691e}
.back{display:inline-block;margin-top:30px;background:#d2691e;color:#fff;padding:12px 24px;border-radius:30px;text-decoration:none;font-weight:bold}
.back:hover{background:#8b4513}
</style></head><body>
<div class="container">
<h1>🦁 Privacy Policy</h1>
<p><strong>Last Updated:</strong> August 8, 2026</p>

<h2>1. Introduction</h2>
<p>Safari Softwares ("we," "us," or "our") is committed to protecting your privacy. This Privacy Policy explains how we collect, use, store, and protect your information when you use Safari AI ("the Service").</p>

<h2>2. Information We Collect</h2>
<p><strong>2.1 Chat Data:</strong> We process the text queries you submit and the AI-generated responses. This data is temporarily stored in server memory to provide conversational context.</p>
<p><strong>2.2 Technical Data:</strong> Our servers automatically log standard technical information including IP address, browser type, request timestamps, and access patterns for operational purposes.</p>
<p><strong>2.3 Local Storage:</strong> The Service stores chat history in your browser's localStorage. This data remains on your device and is not transmitted to us except when you send messages.</p>
<p><strong>2.4 We Do NOT Collect:</strong> Names, physical addresses, phone numbers, payment information, or social media profiles (unless voluntarily shared in chat messages).</p>

<h2>3. How We Use Information</h2>
<p>We use collected information exclusively for:</p>
<ul>
<li>Processing and responding to your queries</li>
<li>Maintaining chat context during your session</li>
<li>Monitoring Service performance and diagnosing technical issues</li>
<li>Enforcing rate limits and preventing abuse</li>
<li>Improving the Service based on usage patterns</li>
</ul>

<h2>4. Data Storage and Retention</h2>
<p><strong>4.1 Server Storage:</strong> Chat conversations are stored temporarily in server memory (RAM) and are automatically deleted after 24 hours of inactivity or upon server restart.</p>
<p><strong>4.2 Local Storage:</strong> Chat history stored in your browser's localStorage persists until you clear your browser data or delete chats through the Service interface.</p>
<p><strong>4.3 No Permanent Database:</strong> We do not maintain a permanent database of user conversations. Data exists only in temporary server memory.</p>

<h2>5. Data Sharing and Disclosure</h2>
<p><strong>We do NOT:</strong></p>
<ul>
<li>Sell, rent, or trade your personal information to third parties</li>
<li>Share your chat data with advertisers or data brokers</li>
<li>Use your data for marketing purposes</li>
</ul>
<p><strong>Limited Sharing:</strong> Your queries are transmitted to Groq's API for AI processing. Please review <a href="https://groq.com/privacy" target="_blank">Groq's Privacy Policy</a> for their data handling practices.</p>
<p><strong>Legal Disclosure:</strong> We may disclose information if required by law, court order, or to protect our rights, safety, or property.</p>

<h2>6. Cookies and Tracking</h2>
<p><strong>6.1 No Advertising Cookies:</strong> Safari AI does not use cookies for advertising, tracking, or analytics purposes.</p>
<p><strong>6.2 Essential Functionality:</strong> The Service uses browser localStorage solely for saving your chat history and preferences. This is essential for the chat history feature to function.</p>
<p><strong>6.3 No Cross-Site Tracking:</strong> We do not employ cross-site tracking mechanisms, fingerprinting, or behavioral profiling.</p>

<h2>7. Data Security</h2>
<p>We implement appropriate technical measures to protect your data:</p>
<ul>
<li>Input sanitization to prevent injection attacks</li>
<li>Rate limiting to prevent abuse</li>
<li>Automatic data cleanup to minimize data retention</li>
<li>HTTPS encryption for data transmission</li>
</ul>
<p>However, no method of electronic storage or transmission is 100% secure. We cannot guarantee absolute security.</p>

<h2>8. Your Rights and Choices</h2>
<p>You have the right to:</p>
<ul>
<li><strong>Access:</strong> View your chat history through the Service interface</li>
<li><strong>Delete:</strong> Remove individual chats using the delete function in the interface</li>
<li><strong>Clear All Data:</strong> Clear your browser's localStorage to remove all locally stored chats</li>
<li><strong>Request Deletion:</strong> Contact us to request server-side data deletion</li>
<li><strong>Stop Using:</strong> Discontinue use of the Service at any time</li>
</ul>

<h2>9. Children's Privacy</h2>
<p>Safari AI is not intended for children under 13 years of age. We do not knowingly collect or process data from children under 13. If you believe a child has provided us with personal information, please contact us immediately for data removal.</p>

<h2>10. International Data Transfers</h2>
<p>Your data may be processed in countries where our servers or third-party services operate. By using the Service, you consent to such transfers. We take reasonable steps to ensure data protection regardless of processing location.</p>

<h2>11. Data Breach Procedures</h2>
<p>In the unlikely event of a data breach, we will take prompt action to investigate, mitigate, and notify affected users if required by applicable law.</p>

<h2>12. Changes to This Policy</h2>
<p>We may update this Privacy Policy periodically to reflect changes in our practices or legal requirements. Updates will be posted on this page with a revised date. Material changes will be noted prominently.</p>

<h2>13. Contact Us</h2>
<p>For privacy-related inquiries, data deletion requests, or concerns:</p>
<p>📧 <a href="mailto:safari.ai.agent@gmail.com">safari.ai.agent@gmail.com</a></p>
<p>We will respond to legitimate requests within 30 days.</p>

<a href="/" class="back">← Back to Safari AI</a>
</div></body></html>"""

# ============================================
# ADMIN PANEL
# ============================================

ADMIN_PASSWORD = "safari2026"

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, pw: str = ""):
    if pw != ADMIN_PASSWORD:
        return """<!DOCTYPE html><html><head><title>Admin Login</title>
<style>body{font-family:Segoe UI;display:flex;justify-content:center;align-items:center;height:100vh;background:#f5e6d3}
form{background:#fff;padding:30px;border-radius:10px;box-shadow:0 5px 20px rgba(0,0,0,.2)}
input{padding:10px;margin:10px 0;width:100%;border:2px solid #d2691e;border-radius:5px;font-size:16px}
button{background:#d2691e;color:#fff;border:0;padding:10px 20px;border-radius:5px;cursor:pointer;font-weight:bold;width:100%}</style></head><body>
<form><h2>Admin Login</h2><input type='password' name='pw' placeholder='Password'><button type='submit'>Login</button></form></body></html>"""
    
    # Admin panel
    user_rows = ""
    for key, user in users.items():
        user_rows += f"""<tr>
            <td>{user.get('email','N/A')}</td>
            <td>{user.get('plan','free')}</td>
            <td>{user.get('queries',0)}</td>
            <td>{user.get('total_queries',0)}</td>
            <td><code>{key[:16]}...</code></td>
            <td><a href='/admin/revoke?key={key}&pw={pw}' onclick='return confirm(\"Revoke this key?\")' style='color:red'>Revoke</a></td>
        </tr>"""
    
    return f"""<!DOCTYPE html><html><head><title>Admin - Safari AI</title>
<style>body{{font-family:Segoe UI;background:#f5e6d3;padding:20px}}
.c{{max-width:1000px;margin:auto;background:#fff;padding:20px;border-radius:10px}}
h1{{color:#8b4513}}table{{width:100%;border-collapse:collapse;margin:20px 0}}
th,td{{padding:10px;border:1px solid #e0c8a8;text-align:left}}
th{{background:#d2691e;color:#fff}}
.form{{background:#faf5f0;padding:15px;border-radius:10px;margin:20px 0}}
input,select{{padding:8px;margin:5px;border:2px solid #d2691e;border-radius:5px}}
.btn{{background:#d2691e;color:#fff;border:0;padding:10px 20px;cursor:pointer;border-radius:5px;font-weight:bold}}
.btn:hover{{background:#8b4513}}</style></head><body>
<div class='c'>
<h1>Admin Panel</h1>
<div class='form'>
<h3>Generate API Key</h3>
<form action='/admin/generate' method='post'>
<input type='hidden' name='pw' value='{pw}'>
<input type='email' name='email' placeholder='User email' required>
<select name='plan'><option value='free'>Free (10/day)</option><option value='pro'>Pro (1000/day)</option><option value='enterprise'>Enterprise (10000/day)</option></select>
<button class='btn' type='submit'>Generate Key</button>
</form></div>
<h3>Users ({len(users)})</h3>
<table><tr><th>Email</th><th>Plan</th><th>Today</th><th>Total</th><th>Key</th><th>Action</th></tr>
{user_rows}</table>
<a href='/admin?pw={pw}' class='btn'>Refresh</a>
</div></body></html>"""

@app.post("/admin/generate")
async def admin_generate(email: str = Form(...), plan: str = Form(default="free"), pw: str = Form(...)):
    if pw != ADMIN_PASSWORD:
        return {"error": "Invalid password"}
    
    api_key = hashlib.sha256(f"{email}{time.time()}".encode()).hexdigest()[:32]
    limit_map = {"free": 10, "pro": 1000, "enterprise": 10000}
    users[api_key] = {
        "email": email,
        "plan": plan,
        "queries_today": 0,
        "queries": 0,
        "total_queries": 0,
        "limit": limit_map.get(plan, 10),
        "last_reset": datetime.now().date().isoformat(),
        "created_at": datetime.now().isoformat()
    }
    save_users()
    
    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Key Generated</title>
<style>body{{font-family:Segoe UI;background:#f5e6d3;display:flex;justify-content:center;align-items:center;height:100vh}}
.c{{background:#fff;padding:30px;border-radius:10px;text-align:center;box-shadow:0 5px 20px rgba(0,0,0,.2);max-width:500px}}
.key-box{{display:flex;align-items:center;gap:10px;margin:15px 0;justify-content:center}}
code{{background:#faf5f0;padding:12px 15px;font-size:14px;word-break:break-all;border-radius:5px;flex:1;text-align:left}}
.copy-btn{{background:#d2691e;color:#fff;border:0;padding:12px 18px;border-radius:5px;cursor:pointer;font-weight:bold;white-space:nowrap;font-size:14px}}
.copy-btn:hover{{background:#8b4513}}
.copy-btn.copied{{background:#28a745}}
.btn{{background:#d2691e;color:#fff;padding:10px 20px;text-decoration:none;border-radius:5px;display:inline-block;margin-top:10px}}
.btn:hover{{background:#8b4513}}</style></head><body>
<div class='c'><h2>API Key Generated</h2>
<p>Email: <strong>{email}</strong></p>
<p>Plan: <strong>{plan}</strong></p>
<p>Daily Limit: <strong>{limit_map.get(plan, 10)}</strong></p>
<div class='key-box'><code id='apikey'>{api_key}</code><button class='copy-btn' id='copyBtn' onclick='copyKey()'>Copy</button></div>
<p style='color:red;font-size:14px'>Copy this key now! It won't be shown again.</p>
<a class='btn' href='/admin?pw={pw}'>Back to Admin</a></div>
<script>
function copyKey() {{
    var key = document.getElementById('apikey').innerText;
    navigator.clipboard.writeText(key).then(function() {{
        var btn = document.getElementById('copyBtn');
        btn.innerText = 'Copied!';
        btn.classList.add('copied');
        setTimeout(function() {{ btn.innerText = 'Copy'; btn.classList.remove('copied'); }}, 2000);
    }});
}}
</script></body></html>""")
@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
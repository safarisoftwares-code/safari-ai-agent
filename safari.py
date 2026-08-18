"""
🦁 Safari AI Lite - Complete SQLite Version
Copyright (c) 2026 Safari Softwares
Developer: Safari Softwares
Email: safarisoftwares@gmail.com
GitHub: https://github.com/safarisoftwares-code/safari-ai-lite
"""

import os
import json
import hashlib
import time
import logging
import urllib.parse
import io
import secrets
import sqlite3
from pypdf import PdfReader
from docx import Document
from datetime import datetime
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from groq import Groq
import httpx

from dotenv import load_dotenv
load_dotenv()

# ============================================
# CONFIGURATION
# ============================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY environment variable must be set!")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD environment variable must be set!")

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

print(f"DEBUG: GROQ_KEY loaded: {GROQ_API_KEY[:15]}...")
print(f"DEBUG: ADMIN_PASSWORD loaded: {'YES' if ADMIN_PASSWORD else 'NO'}")

# ============================================
# LOGGING SETUP
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("safari_ai_lite")

# ============================================
# FASTAPI APP SETUP
# ============================================

app = FastAPI(title="Safari AI Lite", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# GROQ CLIENT
# ============================================

groq_client = Groq(api_key=GROQ_API_KEY)

# ============================================
# SQLITE DATABASE
# ============================================

DB_PATH = "safari_lite.db"

def get_db_connection():
    """Create and return a SQLite database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Initialize all database tables."""
    conn = get_db_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            email TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            banned INTEGER DEFAULT 0,
            total_queries INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS api_keys (
            api_key TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            queries_today INTEGER DEFAULT 0,
            total_queries INTEGER DEFAULT 0,
            limit_per_day INTEGER DEFAULT 10,
            last_reset TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL
        );

        CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email);
    """)
    conn.commit()
    conn.close()
    logger.info("SQLite database initialized successfully")

# Initialize database on startup
init_database()

# ============================================
# HELPER FUNCTIONS
# ============================================

def hash_password(password):
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    """Verify a password against its hash."""
    return hash_password(password) == hashed

def generate_session_token():
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)

def sanitize_input(text):
    """Sanitize user input to prevent XSS."""
    return text.replace("<", "&lt;").replace(">", "&gt;").strip()[:1000]

# ============================================
# ACCOUNT DATABASE FUNCTIONS
# ============================================

def db_create_account(name, email, password):
    """Create a new user account in the database."""
    conn = get_db_connection()
    existing = conn.execute("SELECT email FROM accounts WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.close()
        return False, "Account already exists."
    
    conn.execute(
        "INSERT INTO accounts (email, name, password_hash, banned, total_queries, created_at) VALUES (?, ?, ?, 0, 0, ?)",
        (email, name, hash_password(password), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return True, "Account created successfully."

def db_get_account(email):
    """Retrieve account by email."""
    conn = get_db_connection()
    account = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
    conn.close()
    return account

def db_update_account_queries(email):
    """Increment total query count for an account."""
    conn = get_db_connection()
    conn.execute("UPDATE accounts SET total_queries = total_queries + 1 WHERE email = ?", (email,))
    conn.commit()
    conn.close()

def db_ban_account(email):
    """Ban an account."""
    conn = get_db_connection()
    conn.execute("UPDATE accounts SET banned = 1 WHERE email = ?", (email,))
    conn.commit()
    conn.close()

def db_unban_account(email):
    """Unban an account."""
    conn = get_db_connection()
    conn.execute("UPDATE accounts SET banned = 0 WHERE email = ?", (email,))
    conn.commit()
    conn.close()

def db_get_all_accounts():
    """Get all accounts for admin panel."""
    conn = get_db_connection()
    accounts = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    conn.close()
    return accounts

# ============================================
# SESSION DATABASE FUNCTIONS
# ============================================

def db_create_session(token, email):
    """Store a session token."""
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO sessions (token, email, created_at) VALUES (?, ?, ?)",
        (token, email, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def db_delete_session(token):
    """Delete a session token."""
    conn = get_db_connection()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()

# ============================================
# API KEY DATABASE FUNCTIONS
# ============================================

def db_create_api_key(email, plan="free"):
    """Generate and store a new API key."""
    api_key = hashlib.sha256(f"{email}{time.time()}".encode()).hexdigest()[:32]
    limit_map = {"free": 10, "pro": 1000, "enterprise": 10000}
    
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO api_keys (api_key, email, plan, queries_today, total_queries, limit_per_day, last_reset, created_at) VALUES (?, ?, ?, 0, 0, ?, ?, ?)",
        (api_key, email, plan, limit_map.get(plan, 10), datetime.now().date().isoformat(), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return api_key

def db_get_api_key(api_key):
    """Retrieve API key info."""
    conn = get_db_connection()
    key_info = conn.execute("SELECT * FROM api_keys WHERE api_key = ?", (api_key,)).fetchone()
    conn.close()
    return key_info

def db_get_all_api_keys():
    """Get all API keys for admin panel."""
    conn = get_db_connection()
    keys = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
    conn.close()
    return keys

def db_delete_api_key(api_key):
    """Delete an API key."""
    conn = get_db_connection()
    conn.execute("DELETE FROM api_keys WHERE api_key = ?", (api_key,))
    conn.commit()
    conn.close()

# ============================================
# CHAT HISTORY DATABASE FUNCTIONS
# ============================================

def db_save_chat_message(session_id, role, content):
    """Save a chat message."""
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO chat_history (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, time.time())
    )
    conn.commit()
    conn.close()

def db_get_chat_history(session_id, limit=20):
    """Retrieve chat history for a session."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT role, content FROM chat_history WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    conn.close()
    return rows

def db_delete_chat_history(session_id):
    """Delete all chat history for a session."""
    conn = get_db_connection()
    conn.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()

    # ============================================
# CLEANUP FUNCTIONS
# ============================================

request_counts = {}
LAST_REQUEST_CLEANUP = time.time()

def cleanup_old_request_counts():
    """Remove stale request timestamps."""
    global request_counts, LAST_REQUEST_CLEANUP
    now = time.time()
    if now - LAST_REQUEST_CLEANUP > 3600:
        to_delete = []
        for session in list(request_counts.keys()):
            request_counts[session] = [t for t in request_counts[session] if now - t < 60]
            if len(request_counts[session]) == 0:
                to_delete.append(session)
        for sid in to_delete:
            del request_counts[sid]
            logger.info(f"Cleaned up request counts for session: {sid}")
        LAST_REQUEST_CLEANUP = now

# ============================================
# SAFARI SOFTWARES WEBSITE FETCH
# ============================================

def fetch_safari_website():
    """Fetch Safari Softwares website content for portfolio questions."""
    try:
        resp = httpx.get(
            "https://safarisoftwares-code.github.io/safari-softwares/",
            timeout=8,
            headers={"User-Agent": "SafariAIAgent/2.0"}
        )
        if resp.status_code == 200:
            import re
            html = re.sub(r'<script[^>]*>.*?</script>', ' ', resp.text, flags=re.DOTALL)
            html = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text)
            return text[:4000]
    except Exception as e:
        logger.warning(f"Failed to fetch Safari Softwares website: {e}")
    return ""

# ============================================
# CORE AI FUNCTION
# ============================================

uploaded_files = {}

def think(msg, hist="", session="default"):
    """Generate AI response with document awareness and web search."""
    try:
        msgs = [{
            "role": "system",
            "content": (
                "IDENTITY RULE: You are Safari AI, developed by Safari Softwares. "
                "Only mention 'Safari Softwares' when the user directly asks about your creator, developer, or origin. "
                "In casual conversation, do NOT repeat your identity unnecessarily. "
                "When asked, vary your phrasing naturally, e.g., 'I was built by Safari Softwares', "
                "'The team at Safari Softwares developed me', 'Safari Softwares is behind me'. "
                "Never mention OpenAI, ChatGPT, or any other company. "
                "HONESTY RULE: Do not claim features that do not exist. "
                "Safari AI has NO native mobile app in Play Store or App Store, and no 2FA yet. "
                "However, native apps are planned and coming soon. "
                "Safari AI IS available as a Progressive Web App (PWA). "
                "When asked about mobile access, explain this naturally in your own words. "
                "Key points to mention: (1) no native app yet, (2) Safari Softwares is working on it, "
                "(3) users can install the PWA via 'Add to Home Screen' or 'Install App' in their browser. "
                "Vary the wording each time. Do NOT use the same sentence repeatedly. "
                "Never fabricate features or URLs. "
                "Be helpful, friendly, and thorough. Use emojis naturally. "
                "Provide detailed, well-structured answers when the question requires depth. "
                "Use bullet points, numbered lists, and code blocks when appropriate. "
                "If the user asks a simple question, keep it brief. If they ask for detail, give it fully. "
                "CODE-WRITING RULE: When asked to write code, provide ONE clean, complete, production-ready "
                "code block with a short docstring and a single usage example. "
                "Do NOT provide multiple approaches unless the user asks for options. "
                "Avoid unnecessary comments, tables, or verbose explanations. "
                "Only explain deeply if the user explicitly asks for explanation. "
                "CODE-STYLE RULE: When writing Python code, ALWAYS format it exactly like it appears in a proper editor (VS Code). "
                "Each statement must be on its own line. "
                "Use proper indentation with 4 spaces. "
                "Never use semicolons. Never compress multiple lines into one. "
                "Never use lambda one-liners. "
                "Write clean, readable, PEP 8 compliant code. "
                "For a function, the body should be multiple lines, not single-line if statements. "
                "Include a short docstring and one usage example at the end. "
                "Do NOT provide multiple options, comparison tables, or long explanations. "
                "IMPORTANT: If web search data is provided, use it accurately. "
                "If a document is attached, analyze its content and answer based on it. "
                "Never fabricate news, events, or specific details. Be honest about gaps."
            )
        }]

        # Include uploaded file content if available
        if session in uploaded_files:
            file_data = uploaded_files[session]
            msgs.append({
                "role": "system",
                "content": (
                    f"Attached document '{file_data['filename']}' content:\n"
                    f"{file_data['content'][:3000]}"
                )
            })

        if hist:
            for line in hist.split("\n")[-10:]:
                if line.startswith("U:"):
                    msgs.append({"role": "user", "content": line[2:]})
                elif line.startswith("S:"):
                    msgs.append({"role": "assistant", "content": line[2:]})
        msgs.append({"role": "user", "content": msg})

        # Detect questions about Safari Softwares and fetch official site
        safari_keywords = [
            "safari softwares", "your work", "your projects", "your company",
            "who made you", "what do you do", "what other works",
            "what projects", "your services", "about safari softwares"
        ]
        if any(kw in msg.lower() for kw in safari_keywords):
            site_data = fetch_safari_website()
            if site_data:
                msgs.append({
                    "role": "system",
                    "content": f"Official Safari Softwares website data:\n{site_data}"
                })

        # Quick search for current topics
        needs_search = any(kw in msg.lower() for kw in [
            "president", "election", "today", "current", "latest", "news",
            "2024", "2025", "2026", "price", "score", "weather", "now",
            "world", "affairs", "recent", "happening", "hacked", "incident"
        ])

        if needs_search:
            try:
                query = msg.replace("who is", "").replace("what is", "").replace("current", "").replace("recently", "").strip()
                encoded_query = urllib.parse.quote(query.replace(" ", "_"))
                resp = httpx.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_query}",
                    timeout=5,
                    headers={"User-Agent": "SafariAI/1.0"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("extract", "")[:800]
                    msgs.append({"role": "user", "content": f"Data: {result}\n\nAnswer briefly: {msg}"})
                    r = groq_client.chat.completions.create(
                        model="openai/gpt-oss-20b",
                        messages=msgs,
                        temperature=0.3,
                        max_tokens=1500
                    )
                    return r.choices[0].message.content
            except httpx.TimeoutException:
                logger.warning(f"Wikipedia request timed out for query: {query}")
            except httpx.HTTPStatusError as e:
                logger.warning(f"Wikipedia returned status {e.response.status_code} for query: {query}")
            except Exception as e:
                logger.warning(f"Wikipedia search failed for query '{query}': {e}")

        r = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=msgs,
            temperature=0.3,
            max_tokens=1500
        )
        return r.choices[0].message.content

    except Exception as e:
        logger.error(f"AI response generation failed: {e}")
        return "Sorry, I encountered an issue. Please try again in a moment."

    # ============================================
# FILE UPLOAD ENDPOINT
# ============================================

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), session: str = Form(default="default")):
    """
    Handle file uploads for document analysis.
    Supports PDF (digital & scanned via OCR), DOCX, and plain text files.
    """
    try:
        content = await file.read()
        filename = file.filename or "document"
        lower_name = filename.lower()

        # ============================================
        # PDF HANDLING
        # ============================================
        if lower_name.endswith(".pdf"):
            try:
                # Try digital text extraction first
                reader = PdfReader(io.BytesIO(content))
                text = ""
                for page in reader.pages[:5]:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                text = text.strip()

                # If no text found, try OCR for scanned PDFs
                if not text:
                    import pytesseract
                    from pdf2image import convert_from_bytes
                    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
                    images = convert_from_bytes(content, first_page=1, last_page=3)
                    ocr_text = ""
                    for img in images:
                        ocr_text += pytesseract.image_to_string(img) + "\n"
                    text = ocr_text[:5000]

                if not text:
                    return {
                        "status": "error",
                        "message": "Could not extract text from PDF. It may be a scanned image or contain no text."
                    }
            except Exception as e:
                logger.warning(f"PDF extraction failed: {e}")
                return {"status": "error", "message": "Could not extract text from PDF."}

        # ============================================
        # DOCX HANDLING
        # ============================================
        elif lower_name.endswith(".docx"):
            try:
                doc = Document(io.BytesIO(content))
                text = "\n".join([p.text for p in doc.paragraphs[:100]])[:5000]
            except Exception as e:
                logger.warning(f"DOCX extraction failed: {e}")
                return {"status": "error", "message": "Could not extract text from Word document."}

        # ============================================
        # PLAIN TEXT HANDLING
        # ============================================
        else:
            text = content.decode("utf-8", errors="ignore")[:5000]

        if not text.strip():
            return {"status": "error", "message": "Could not read text from this file."}

        # Store uploaded file content in memory for AI analysis
        uploaded_files[session] = {"filename": filename, "content": text}

        # Save attachment notification to chat history
        db_save_chat_message(session, "user", f"[Attached file: {filename}]")
        db_save_chat_message(session, "assistant", f"📄 I have received '{filename}'. What would you like me to do with it?")

        logger.info(f"File uploaded: {filename} for session {session}")

        return {
            "status": "success",
            "filename": filename,
            "preview": text[:300],
            "message": f"📄 '{filename}' attached successfully!"
        }
    except Exception as e:
        logger.error(f"File upload failed: {e}")
        return {"status": "error", "message": "Could not process file."}

    # ============================================
# AUTHENTICATION ENDPOINTS
# ============================================

@app.post("/signup")
async def signup(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    """
    Register a new user account.
    Validates email format and password length.
    Creates account in SQLite and generates a session token.
    """
    # Validate email format
    if "@" not in email or "." not in email or len(email) > 100:
        return {"status": "error", "message": "Invalid email format."}

    # Validate password length
    if len(password) < 4:
        return {"status": "error", "message": "Password must be at least 4 characters."}

    # Validate name
    if not name or len(name.strip()) < 2:
        return {"status": "error", "message": "Name must be at least 2 characters."}

    # Check if account already exists
    existing = db_get_account(email)
    if existing:
        return {"status": "error", "message": "Account already exists."}

    # Create account
    success, message = db_create_account(name.strip(), email.strip().lower(), password)
    if not success:
        return {"status": "error", "message": message}

    # Generate session token
    token = generate_session_token()
    db_create_session(token, email.strip().lower())

    logger.info(f"New account created: {email}")

    return {
        "status": "success",
        "name": name.strip(),
        "email": email.strip().lower(),
        "token": token,
        "message": "Account created successfully!"
    }


@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):
    """
    Authenticate a user and create a session.
    Checks if account exists, is not banned, and password is correct.
    """
    # Validate email format
    if "@" not in email or "." not in email:
        return {"status": "error", "message": "Invalid email format."}

    # Get account from database
    account = db_get_account(email.strip().lower())
    if not account:
        return {"status": "error", "message": "Account not found."}

    # Check if banned
    if account["banned"]:
        return {"status": "error", "message": "Account suspended. Contact Safari Softwares."}

    # Verify password
    if not verify_password(password, account["password_hash"]):
        return {"status": "error", "message": "Incorrect password."}

    # Generate session token
    token = generate_session_token()
    db_create_session(token, email.strip().lower())

    logger.info(f"User logged in: {email}")

    return {
        "status": "success",
        "name": account["name"],
        "email": email.strip().lower(),
        "token": token,
        "message": "Logged in successfully!"
    }


@app.post("/logout")
async def logout(token: str = Form(...)):
    """
    End a user session by deleting the token.
    """
    if token:
        db_delete_session(token)
        logger.info("User logged out")
        return {"status": "success", "message": "Logged out."}
    return {"status": "error", "message": "Invalid session."}

# ============================================
# ASK ENDPOINT (with SQLite chat history)
# ============================================

@app.post("/ask")
async def ask(
    question: str = Form(...),
    session: str = Form(default="default"),
    api_key: str = Form(default=None)
):
    """
    Process user questions.
    - Validates input
    - Optional API key authentication with daily limits
    - Rate limiting per session
    - Loads chat history from SQLite
    - Generates AI response
    - Saves conversation to SQLite
    """
    # Input validation
    question = sanitize_input(question)
    if not question or len(question) < 1:
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    if len(question) > 1000:
        raise HTTPException(status_code=400, detail="Question too long (max 1000 characters)")

    # Validate session ID
    if len(session) > 50:
        session = session[:50]

    # API key authentication (if provided)
    if api_key:
        key_info = db_get_api_key(api_key)
        if not key_info:
            logger.warning(f"Invalid API key attempt: {api_key[:8]}... from session {session}")
            raise HTTPException(status_code=403, detail="Invalid API key")

        today = datetime.now().date().isoformat()

        # Reset daily counter if new day
        if key_info["last_reset"] != today:
            conn = get_db_connection()
            conn.execute(
                "UPDATE api_keys SET queries_today = 0, last_reset = ? WHERE api_key = ?",
                (today, api_key)
            )
            conn.commit()
            conn.close()

        # Check daily limit
        limit = key_info["limit_per_day"]
        if key_info["queries_today"] >= limit:
            logger.warning(f"API key {api_key[:8]}... exceeded daily limit ({limit})")
            raise HTTPException(status_code=429, detail=f"Daily limit of {limit} queries reached. Upgrade your plan for more.")

        # Increment counters
        conn = get_db_connection()
        conn.execute(
            "UPDATE api_keys SET queries_today = queries_today + 1, total_queries = total_queries + 1 WHERE api_key = ?",
            (api_key,)
        )
        conn.commit()
        conn.close()

    # Rate limiting: 20 requests per minute per session
    now = time.time()
    if session in request_counts:
        request_counts[session] = [t for t in request_counts[session] if now - t < 60]
        if len(request_counts[session]) >= 20:
            logger.warning(f"Rate limit exceeded for session: {session}")
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait a moment.")
    else:
        request_counts[session] = []
    request_counts[session].append(now)

    # Periodic cleanup
    cleanup_old_request_counts()

    # Load chat history from SQLite
    rows = db_get_chat_history(session, limit=10)
    hist_lines = []
    for row in reversed(rows):
        prefix = "U:" if row["role"] == "user" else "S:"
        hist_lines.append(f"{prefix}{row['content']}")
    hist = "\n".join(hist_lines)

    # Generate AI response
    resp = think(question, hist, session)

    # Save conversation to SQLite
    db_save_chat_message(session, "user", question)
    db_save_chat_message(session, "assistant", resp)

    logger.info(f"Query processed for session {session}: {question[:50]}...")
    return {"response": resp}


# ============================================
# DELETE DATA ENDPOINT
# ============================================

@app.post("/delete-data")
async def delete_data(session: str = Form(...)):
    """
    Delete all chat history for a session.
    Also removes uploaded files from memory.
    """
    db_delete_chat_history(session)
    if session in uploaded_files:
        del uploaded_files[session]
    logger.info(f"Chat data deleted for session: {session}")
    return {"status": "deleted", "message": "Your chat data has been permanently deleted."}

# ============================================
# ADMIN PANEL - LOGIN PAGE
# ============================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, pw: str = ""):
    """Admin dashboard with login protection."""
    if pw != ADMIN_PASSWORD:
        return """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Admin Login - Safari AI Lite</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Segoe UI,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background:#f5e6d3}
form{background:#fff;padding:40px;border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,.2);width:100%;max-width:400px}
h2{color:#8b4513;margin-bottom:20px;text-align:center;font-size:24px}
label{display:block;margin-bottom:8px;color:#555;font-weight:600}
input{padding:12px;margin-bottom:20px;width:100%;border:2px solid #d2691e;border-radius:8px;font-size:16px;outline:0}
input:focus{border-color:#8b4513;box-shadow:0 0 0 3px rgba(210,105,30,.2)}
button{background:#d2691e;color:#fff;border:0;padding:14px;border-radius:8px;cursor:pointer;font-weight:bold;width:100%;font-size:16px;transition:background .3s}
button:hover{background:#8b4513}
.error{color:#d32f2f;text-align:center;margin-bottom:15px;font-size:14px}
.back{display:block;text-align:center;margin-top:15px;color:#d2691e;text-decoration:none;font-size:14px}
.back:hover{text-decoration:underline}
</style></head><body>
<form method="get" action="/admin">
<h2>🦁 Safari AI Lite Admin</h2>
<p class="error" id="error"></p>
<label for="pw">Admin Password</label>
<input type="password" id="pw" name="pw" placeholder="Enter password" required>
<button type="submit">Login</button>
<a href="/" class="back">Back to Safari AI</a>
</form>
<script>
const urlParams=new URLSearchParams(window.location.search);
if(urlParams.get('error')==='1'){document.getElementById('error').innerText='Invalid password. Please try again.';}
</script></body></html>"""

    # ============================================
    # ADMIN DASHBOARD
    # ============================================

    # Get accounts from SQLite
    accounts = db_get_all_accounts()
    account_rows = ""
    for acc in accounts:
        name = acc["name"]
        email = acc["email"]
        total = acc["total_queries"]
        banned = acc["banned"]
        status = "🔴 Banned" if banned else "🟢 Active"
        if banned:
            action = f'<a href="/admin/unban?email={email}&pw={pw}" class="btn-unban">Unban</a>'
        else:
            action = f'<a href="/admin/ban?email={email}&pw={pw}" class="btn-ban">Ban</a>'
        account_rows += f"""<tr>
            <td>{name}</td>
            <td>{email}</td>
            <td>{total}</td>
            <td>{status}</td>
            <td>{action}</td>
        </tr>"""

    # Get API keys from SQLite
    api_keys = db_get_all_api_keys()
    user_rows = ""
    for key in api_keys:
        email = key["email"]
        plan = key["plan"]
        queries_today = key["queries_today"]
        total_queries = key["total_queries"]
        limit = key["limit_per_day"]
        usage_percent = min(100, int((queries_today / limit) * 100)) if limit > 0 else 0
        api_key_short = key["api_key"][:12]

        user_rows += f"""<tr>
            <td>{email}</td>
            <td><span class="plan-badge plan-{plan}">{plan.upper()}</span></td>
            <td>{queries_today} / {limit} <div class="usage-bar"><div class="usage-fill" style="width:{usage_percent}%"></div></div></td>
            <td>{total_queries}</td>
            <td><code>{api_key_short}...</code></td>
            <td>
                <form method="post" action="/admin/revoke" style="display:inline" onsubmit="return confirm('Revoke this API key? This cannot be undone.')">
                    <input type="hidden" name="key" value="{key['api_key']}">
                    <input type="hidden" name="pw" value="{pw}">
                    <button type="submit" class="btn-revoke">Revoke</button>
                </form>
            </td>
        </tr>"""

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Admin Panel - Safari AI Lite</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Segoe UI,sans-serif;background:#f5e6d3;padding:20px;min-height:100vh}}
.c{{max-width:1200px;margin:auto;background:#fff;padding:30px;border-radius:15px;box-shadow:0 10px 40px rgba(0,0,0,.15)}}
h1{{color:#8b4513;font-size:28px;margin-bottom:5px}}
.subtitle{{color:#888;margin-bottom:25px;font-size:14px}}
.stats{{display:flex;gap:20px;margin-bottom:30px;flex-wrap:wrap}}
.stat-card{{background:#faf5f0;padding:20px;border-radius:10px;flex:1;min-width:150px;text-align:center;border:1px solid #e0c8a8}}
.stat-card h3{{color:#d2691e;font-size:14px;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}}
.stat-card .num{{color:#8b4513;font-size:32px;font-weight:bold}}
table{{width:100%;border-collapse:collapse;margin:20px 0}}
th,td{{padding:12px 15px;border:1px solid #e0c8a8;text-align:left;font-size:14px}}
th{{background:#d2691e;color:#fff;font-weight:600}}
tr:hover{{background:#faf5f0}}
.form{{background:#faf5f0;padding:20px;border-radius:10px;margin:20px 0;border:1px solid #e0c8a8}}
.form h3{{color:#8b4513;margin-bottom:15px}}
.form-row{{display:flex;gap:10px;flex-wrap:wrap;align-items:end}}
.form-row input,.form-row select{{padding:10px;border:2px solid #d2691e;border-radius:8px;font-size:14px;outline:0;flex:1;min-width:150px}}
.form-row input:focus,.form-row select:focus{{border-color:#8b4513}}
.btn{{background:#d2691e;color:#fff;border:0;padding:10px 20px;cursor:pointer;border-radius:8px;font-weight:bold;font-size:14px;transition:background .3s;white-space:nowrap}}
.btn:hover{{background:#8b4513}}
.btn-revoke{{background:#d32f2f;color:#fff;border:0;padding:6px 14px;cursor:pointer;border-radius:6px;font-size:13px;font-weight:bold;transition:background .3s}}
.btn-revoke:hover{{background:#b71c1c}}
.btn-ban{{background:#d32f2f;color:#fff;border:0;padding:6px 14px;cursor:pointer;border-radius:6px;font-size:13px;font-weight:bold;text-decoration:none;display:inline-block}}
.btn-ban:hover{{background:#b71c1c}}
.btn-unban{{background:#28a745;color:#fff;border:0;padding:6px 14px;cursor:pointer;border-radius:6px;font-size:13px;font-weight:bold;text-decoration:none;display:inline-block}}
.btn-unban:hover{{background:#218838}}
.btn-logout{{background:#555;color:#fff;border:0;padding:8px 16px;cursor:pointer;border-radius:6px;font-size:13px;text-decoration:none;display:inline-block;transition:background .3s}}
.btn-logout:hover{{background:#333}}
.plan-badge{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:bold;letter-spacing:1px}}
.plan-free{{background:#e8f5e9;color:#2e7d32}}
.plan-pro{{background:#e3f2fd;color:#1565c0}}
.plan-enterprise{{background:#fce4ec;color:#c62828}}
.usage-bar{{width:80px;height:6px;background:#e0c8a8;border-radius:3px;margin-top:4px;overflow:hidden}}
.usage-fill{{height:100%;background:#d2691e;border-radius:3px;transition:width .3s}}
.header-row{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}}
.refresh-row{{display:flex;justify-content:space-between;align-items:center;margin-top:20px}}
</style></head><body>
<div class="c">
<div class="header-row">
<div><h1>Admin Panel</h1><p class="subtitle">Safari AI Lite - User & API Key Management</p></div>
<a href="/" class="btn-logout">Exit Admin</a>
</div>

<div class="stats">
<div class="stat-card"><h3>Login Accounts</h3><div class="num">{len(accounts)}</div></div>
<div class="stat-card"><h3>API Keys</h3><div class="num">{len(api_keys)}</div></div>
<div class="stat-card"><h3>Banned</h3><div class="num">{sum(1 for a in accounts if a['banned'])}</div></div>
</div>

<div class="form">
<h3>Generate New API Key</h3>
<form action="/admin/generate" method="post">
<div class="form-row">
<input type="hidden" name="pw" value="{pw}">
<input type="email" name="email" placeholder="User email address" required>
<select name="plan">
<option value="free">Free (10/day)</option>
<option value="pro">Pro (1,000/day)</option>
<option value="enterprise">Enterprise (10,000/day)</option>
</select>
<button class="btn" type="submit">Generate Key</button>
</div>
</form>
</div>

<h3 style="color:#8b4513;margin-top:25px">Login Accounts</h3>
<table>
<tr><th>Name</th><th>Email</th><th>Total Queries</th><th>Status</th><th>Action</th></tr>
{account_rows}
</table>

<h3 style="color:#8b4513;margin-top:25px">API Key Users</h3>
<table>
<tr><th>Email</th><th>Plan</th><th>Usage Today</th><th>Total Queries</th><th>API Key</th><th>Action</th></tr>
{user_rows}
</table>

<div class="refresh-row">
<a href="/admin?pw={pw}" class="btn">Refresh Data</a>
<form method="post" action="/admin/logout" style="display:inline">
<button type="submit" class="btn-logout">Logout</button>
</form>
</div>
</div></body></html>"""

# ============================================
# ADMIN - GENERATE API KEY
# ============================================

@app.post("/admin/generate")
async def admin_generate(email: str = Form(...), plan: str = Form(default="free"), pw: str = Form(...)):
    """Generate a new API key for a user."""
    if pw != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")

    api_key = db_create_api_key(email, plan)
    limit_map = {"free": 10, "pro": 1000, "enterprise": 10000}

    logger.info(f"Admin generated API key for {email} (plan: {plan})")

    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Key Generated - Safari AI Lite</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Segoe UI,sans-serif;background:#f5e6d3;display:flex;justify-content:center;align-items:center;height:100vh;padding:20px}}
.c{{background:#fff;padding:40px;border-radius:15px;text-align:center;box-shadow:0 10px 40px rgba(0,0,0,.2);max-width:550px;width:100%}}
h2{{color:#8b4513;margin-bottom:5px;font-size:24px}}
.detail{{color:#888;margin-bottom:20px;font-size:14px}}
.info{{text-align:left;background:#faf5f0;padding:15px;border-radius:10px;margin-bottom:20px}}
.info p{{margin:8px 0;font-size:14px}}
.info strong{{color:#d2691e}}
.key-box{{display:flex;align-items:center;gap:10px;margin:15px 0}}
code{{background:#faf5f0;padding:14px 15px;font-size:14px;word-break:break-all;border-radius:8px;flex:1;text-align:left;border:2px dashed #d2691e;font-family:monospace}}
.copy-btn{{background:#d2691e;color:#fff;border:0;padding:14px 20px;border-radius:8px;cursor:pointer;font-weight:bold;white-space:nowrap;font-size:14px;transition:background .3s}}
.copy-btn:hover{{background:#8b4513}}
.copy-btn.copied{{background:#28a745}}
.warning{{color:#d32f2f;font-size:13px;margin:15px 0;font-weight:bold}}
.btn{{background:#d2691e;color:#fff;padding:12px 24px;text-decoration:none;border-radius:8px;display:inline-block;margin-top:10px;font-weight:bold;transition:background .3s}}
.btn:hover{{background:#8b4513}}
</style></head><body>
<div class='c'>
<h2>🦁 API Key Generated</h2>
<p class="detail">New credentials created successfully</p>
<div class='info'>
<p><strong>Email:</strong> {email}</p>
<p><strong>Plan:</strong> {plan.upper()}</p>
<p><strong>Daily Limit:</strong> {limit_map.get(plan, 10)} queries</p>
</div>
<div class='key-box'><code id='apikey'>{api_key}</code><button class='copy-btn' id='copyBtn' onclick='copyKey()'>Copy</button></div>
<p class='warning'>Copy this key now! It will not be shown again.</p>
<a class='btn' href='/admin?pw={pw}'>Back to Admin</a>
</div>
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


# ============================================
# ADMIN - REVOKE API KEY
# ============================================

@app.post("/admin/revoke")
async def admin_revoke(key: str = Form(...), pw: str = Form(...)):
    """Revoke an API key."""
    if pw != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Invalid admin password")

    db_delete_api_key(key)
    logger.info(f"Admin revoked API key: {key[:12]}...")

    return RedirectResponse(url=f"/admin?pw={pw}", status_code=303)


# ============================================
# ADMIN - BAN ACCOUNT
# ============================================

@app.get("/admin/ban")
async def admin_ban(email: str = "", pw: str = ""):
    """Ban a login account."""
    if pw != ADMIN_PASSWORD:
        return {"error": "Invalid password"}

    db_ban_account(email)
    logger.info(f"Admin banned account: {email}")

    return RedirectResponse(url=f"/admin?pw={pw}", status_code=303)


# ============================================
# ADMIN - UNBAN ACCOUNT
# ============================================

@app.get("/admin/unban")
async def admin_unban(email: str = "", pw: str = ""):
    """Unban a login account."""
    if pw != ADMIN_PASSWORD:
        return {"error": "Invalid password"}

    db_unban_account(email)
    logger.info(f"Admin unbanned account: {email}")

    return RedirectResponse(url=f"/admin?pw={pw}", status_code=303)


# ============================================
# ADMIN - LOGOUT
# ============================================

@app.post("/admin/logout")
async def admin_logout():
    """Logout from admin panel."""
    return RedirectResponse(url="/admin", status_code=303)

# ============================================
# HEALTH CHECK
# ============================================

@app.get("/health")
async def health():
    """Health check endpoint."""
    conn = get_db_connection()
    account_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    chat_count = conn.execute("SELECT COUNT(*) FROM chat_history").fetchone()[0]
    conn.close()

    return {
        "status": "ok",
        "accounts": account_count,
        "chat_messages": chat_count,
        "timestamp": datetime.now().isoformat()
    }


# ============================================
# LOGIN PAGE
# ============================================

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Login and signup page."""
    return """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Login - Safari AI Lite</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Segoe UI,sans-serif;background:#f5e6d3;min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px}
.c{background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,.2);padding:40px;width:100%;max-width:420px}
h1{color:#8b4513;font-size:24px;margin-bottom:5px;text-align:center}
p{color:#888;text-align:center;margin-bottom:25px;font-size:14px}
.tab-row{display:flex;gap:10px;margin-bottom:20px}
.tab-btn{flex:1;padding:12px;border:2px solid #d2691e;background:#fff;color:#d2691e;border-radius:10px;cursor:pointer;font-weight:bold;font-size:14px;transition:all .3s}
.tab-btn.active{background:#d2691e;color:#fff}
label{display:block;margin-bottom:8px;color:#555;font-weight:600;font-size:14px}
input{padding:12px;margin-bottom:18px;width:100%;border:2px solid #e0c8a8;border-radius:10px;font-size:15px;outline:0}
input:focus{border-color:#d2691e}
button{background:#d2691e;color:#fff;border:0;padding:14px;border-radius:10px;cursor:pointer;font-weight:bold;width:100%;font-size:16px;transition:background .3s}
button:hover{background:#8b4513}
.msg{margin-top:15px;text-align:center;font-size:14px;display:none}
.msg.error{color:#d32f2f;display:block}
.msg.success{color:#2e7d32;display:block}
.back{display:block;text-align:center;margin-top:15px;color:#d2691e;text-decoration:none;font-size:14px}
.back:hover{text-decoration:underline}
.password-wrapper{position:relative}
.password-wrapper input{padding-right:40px}
.eye-btn{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:16px;padding:0;width:30px;height:30px;display:flex;align-items:center;justify-content:center}
</style></head><body>
<div class="c">
<h1>🦁 Safari AI Lite</h1>
<p>Login or create an account</p>
<div class="tab-row">
<button class="tab-btn active" id="loginTab" onclick="showLogin()">Login</button>
<button class="tab-btn" id="signupTab" onclick="showSignup()">Sign Up</button>
</div>
<form id="loginForm">
<label for="loginEmail">Email</label>
<input type="email" id="loginEmail" placeholder="you@example.com" required>
<label for="loginPassword">Password</label>
<div class="password-wrapper">
    <input type="password" id="loginPassword" placeholder="Enter password" required>
    <button type="button" class="eye-btn" onclick="togglePassword('loginPassword', this)">👁️</button>
</div>
<button type="submit">Login</button>
</form>
<form id="signupForm" style="display:none">
<label for="signupName">Full Name</label>
<input type="text" id="signupName" placeholder="Your name" required>
<label for="signupEmail">Email</label>
<input type="email" id="signupEmail" placeholder="you@example.com" required>
<label for="signupPassword">Password</label>
<div class="password-wrapper">
    <input type="password" id="signupPassword" placeholder="Minimum 4 characters" required>
    <button type="button" class="eye-btn" onclick="togglePassword('signupPassword', this)">👁️</button>
</div>
<button type="submit">Create Account</button>
</form>
<div class="msg" id="msg"></div>
<a href="/" class="back">&larr; Back to Safari AI</a>
</div>
<script>
function showLogin(){
    document.getElementById('loginForm').style.display='block';
    document.getElementById('signupForm').style.display='none';
    document.getElementById('loginTab').classList.add('active');
    document.getElementById('signupTab').classList.remove('active');
    document.getElementById('msg').className='msg';
}
function showSignup(){
    document.getElementById('loginForm').style.display='none';
    document.getElementById('signupForm').style.display='block';
    document.getElementById('signupTab').classList.add('active');
    document.getElementById('loginTab').classList.remove('active');
    document.getElementById('msg').className='msg';
}
function showMsg(text,type){
    var m=document.getElementById('msg');
    m.textContent=text;
    m.className='msg '+type;
}
function togglePassword(inputId, btn){
    var input = document.getElementById(inputId);
    if(input.type === 'password'){
        input.type = 'text';
        btn.textContent = '🙈';
    } else {
        input.type = 'password';
        btn.textContent = '👁️';
    }
}
document.getElementById('loginForm').addEventListener('submit',async function(e){
    e.preventDefault();
    var email=document.getElementById('loginEmail').value;
    var password=document.getElementById('loginPassword').value;
    var fd=new FormData();
    fd.append('email',email);
    fd.append('password',password);
    var r=await fetch('/login',{method:'POST',body:fd});
    var d=await r.json();
    if(d.status==='success'){
        localStorage.setItem('safari_token',d.token);
        localStorage.setItem('safari_name',d.name);
        showMsg('Welcome back, '+d.name+'! Redirecting...','success');
        setTimeout(function(){window.location.href='/';},1000);
    } else {
        showMsg(d.message,'error');
    }
});
document.getElementById('signupForm').addEventListener('submit',async function(e){
    e.preventDefault();
    var name=document.getElementById('signupName').value;
    var email=document.getElementById('signupEmail').value;
    var password=document.getElementById('signupPassword').value;
    var fd=new FormData();
    fd.append('name',name);
    fd.append('email',email);
    fd.append('password',password);
    var r=await fetch('/signup',{method:'POST',body:fd});
    var d=await r.json();
    if(d.status==='success'){
        localStorage.setItem('safari_token',d.token);
        localStorage.setItem('safari_name',d.name);
        showMsg('Account created! Welcome, '+d.name+'!','success');
        setTimeout(function(){window.location.href='/';},1000);
    } else {
        showMsg(d.message,'error');
    }
});
</script></body></html>"""

# ============================================
# MAIN CHAT INTERFACE
# ============================================

@app.get("/", response_class=HTMLResponse)
async def home():
    """Main chat page with full functionality."""
    return """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Safari AI Lite - Explore Beyond Limits</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#x1F981;</text></svg>">
<link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#x1F981;</text></svg>">
<link rel="manifest" href="/manifest.json">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Segoe UI,sans-serif;background:#f5e6d3;min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px}
.c{background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,.2);width:100%;max-width:800px;height:90vh;display:flex;flex-direction:column;overflow:hidden}
.h{background:linear-gradient(135deg,#d2691e,#8b4513);color:#fff;padding:16px 22px;display:flex;align-items:center;gap:12px}
.h h1{font-size:20px}.h p{font-size:11px;opacity:.9}
.tabs-container{display:flex;align-items:center;background:#fff;border-bottom:2px solid #f0e0d0}
.tabs{display:flex;overflow-x:auto;padding:0 5px;min-height:40px;align-items:flex-end;flex:1;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{padding:8px 16px;background:#f5e6d3;border:1px solid #e0c8a8;border-bottom:0;border-radius:10px 10px 0 0;margin:0 3px;cursor:pointer;white-space:nowrap;font-size:13px;position:relative;max-width:160px;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:5px}
.tab.active{background:#fff;border-bottom:2px solid #fff;margin-bottom:-2px;font-weight:bold;color:#8b4513}
.tab-name{overflow:hidden;text-overflow:ellipsis;flex:1}
.tab .del{width:18px;height:18px;background:#ff6b6b;color:#fff;border-radius:50%;display:none;align-items:center;justify-content:center;font-size:12px;line-height:1;cursor:pointer;flex-shrink:0}
.tab .del:hover{background:#d32f2f}
.tab:hover .del{display:flex}
.tab .rename-btn{width:18px;height:18px;background:#ccc;color:#fff;border-radius:50%;display:none;align-items:center;justify-content:center;font-size:10px;line-height:1;cursor:pointer;flex-shrink:0}
.tab .rename-btn:hover{background:#888}
.tab:hover .rename-btn{display:flex}
.tab.add{background:#d2691e;color:#fff;font-weight:bold;font-size:18px;padding:8px 14px;border-radius:10px 10px 0 0;cursor:pointer;flex-shrink:0}
.tab.add:hover{background:#8b4513}
.export-btn{background:#f0e0d0;color:#8b4513;border:0;padding:8px 12px;margin:4px 8px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:bold;white-space:nowrap;flex-shrink:0;transition:background .3s}
.export-btn:hover{background:#e0c8a8}
#b{flex:1;overflow-y:auto;padding:20px;background:#fffaf5;scroll-behavior:smooth}
.m-wrapper{display:flex;align-items:flex-end;gap:8px;margin:8px 0}
.m-wrapper.user{justify-content:flex-end}
.m-wrapper.bot{justify-content:flex-start}
.m{max-width:75%;padding:12px 16px;border-radius:18px;word-wrap:break-word;overflow-wrap:break-word;position:relative;line-height:1.5}
.u{background:#8b4513;color:#fff;border-bottom-right-radius:6px}
.s{background:#fff;border:2px solid #d2691e;border-bottom-left-radius:6px}
.msg-actions{display:flex;gap:4px;opacity:0.5;transition:opacity .2s;flex-shrink:0}
.m-wrapper:hover .msg-actions{opacity:1}
.msg-action-btn{width:28px;height:28px;border-radius:50%;border:0;cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;transition:background .2s}
.btn-copy{background:#e8f5e9;color:#2e7d32}
.btn-copy:hover{background:#c8e6c9}
.btn-edit{background:#fff3e0;color:#e65100}
.btn-edit:hover{background:#ffe0b2}
.btn-copy.copied{background:#28a745;color:#fff}
.time-stamp{font-size:10px;color:#999;margin-top:4px;text-align:right;opacity:.7}
.s .time-stamp{text-align:left}
.i{display:flex;padding:10px;background:#fff;border-top:1px solid #f0e0d0;gap:8px;align-items:center;flex-wrap:wrap}
.file-preview{display:none;align-items:center;gap:6px;padding:6px 12px;background:#fff3e0;border-top:1px solid #f0e0d0;font-size:12px;color:#8b4513;overflow:hidden;max-width:100%;flex-shrink:0}
.file-preview span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.clear-attach-btn{margin-left:8px;background:#ff6b6b;color:#fff;border:none;border-radius:50%;width:22px;height:22px;cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;flex-shrink:0;padding:0;line-height:1}
.clear-attach-btn:hover{background:#d32f2f}
.attach-btn{display:flex;align-items:center;justify-content:center;width:44px;height:44px;background:#f0e0d0;border-radius:50%;cursor:pointer;font-size:20px;flex-shrink:0;transition:background .3s}
.attach-btn:hover{background:#e0c8a8}
#q{flex:1;min-width:0;padding:12px;border:2px solid #e0c8a8;border-radius:30px;font-size:15px;outline:0;resize:none;min-height:48px;max-height:120px;font-family:inherit}
#q:focus{border-color:#d2691e}
button#askBtn{background:#d2691e;color:#fff;border:0;padding:12px 22px;border-radius:30px;cursor:pointer;font-weight:700;white-space:nowrap;flex-shrink:0;transition:background .3s}
button#askBtn:hover{background:#8b4513}
button#askBtn:disabled{background:#ccc;cursor:not-allowed}
.typing-indicator{display:none;padding:12px 20px;align-items:center;gap:4px}
.typing-indicator.show{display:flex}
.typing-dot{width:8px;height:8px;background:#d2691e;border-radius:50%;animation:typing 1.4s infinite}
.typing-dot:nth-child(2){animation-delay:.2s}
.typing-dot:nth-child(3){animation-delay:.4s}
@keyframes typing{0%,60%,100%{transform:translateY(0);opacity:.4}30%{transform:translateY(-8px);opacity:1}}
.edit-input{width:100%;padding:8px 12px;border:2px solid #d2691e;border-radius:12px;font-size:14px;font-family:inherit;outline:0;resize:none}
.edit-actions{display:flex;gap:6px;margin-top:6px;justify-content:flex-end}
.edit-actions button{padding:6px 14px;border-radius:15px;border:0;cursor:pointer;font-size:12px;font-weight:bold;transition:background .2s}
.btn-save{background:#28a745;color:#fff}
.btn-save:hover{background:#218838}
.btn-cancel{background:#999;color:#fff}
.btn-cancel:hover{background:#777}
.f{text-align:center;padding:8px;font-size:10px;color:#999}
.f a{color:#d2691e;text-decoration:none}
.f a:hover{text-decoration:underline}
.toast{position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:#333;color:#fff;padding:10px 24px;border-radius:25px;font-size:14px;opacity:0;transition:opacity .3s;z-index:1000;pointer-events:none}
.toast.show{opacity:1}
</style></head><body>
<div class="c">
<div class="h">
    <span style="font-size:32px">&#x1F981;</span>
    <div style="flex:1;"><h1>Safari AI Lite</h1><p>Explore Beyond Limits</p></div>
    <div id="userArea" style="display:flex;align-items:center;gap:10px;">
        <span id="userName" style="font-size:13px;font-weight:bold;"></span>
        <button id="logoutBtn" onclick="logoutUser()" style="background:#8b4513;color:#fff;border:none;padding:6px 14px;border-radius:20px;cursor:pointer;font-size:12px;font-weight:bold;display:none;">Logout</button>
        <a href="/login" id="loginLink" style="color:#fff;font-size:13px;text-decoration:none;font-weight:bold;">Login</a>
    </div>
</div>
<div class="tabs-container">
<div class="tabs" id="tabs"></div>
<button class="export-btn" onclick="exportChat()" title="Export current chat">&#x1F4E5; Export</button>
</div>
<div id="b"></div>
<div class="typing-indicator" id="typing"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>
<div class="i">
<label for="fileInput" class="attach-btn" title="Attach a document">&#128206;</label>
<div class="file-preview" id="filePreview" style="display:none;">
    📎 <span id="fileName">file.txt</span>
    <button type="button" class="clear-attach-btn" onclick="clearAttachment()" title="Remove attachment">✕</button>
</div>
<input type="file" id="fileInput" accept=".txt,.md,.csv,.json,.py,.log,.pdf,.docx" style="display:none;" onchange="selectFile(this)">
<input id="q" placeholder="Type your question or attach a file..." autofocus onkeypress="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();ask()}">
<button id="askBtn" onclick="ask()">Ask</button>
</div>
<div class="f">&#169; 2026 Safari Softwares | <a href="/login">Login</a> | <a href="/admin">Admin</a></div>
</div>
<div class="toast" id="toast"></div>
<script>
let chats={};
let activeChat=null;
let isProcessing=false;

function showToast(msg){
    var t=document.getElementById('toast');
    t.textContent=msg;
    t.classList.add('show');
    setTimeout(function(){t.classList.remove('show');},2000);
}

function updateUserUI(){
    var token = localStorage.getItem('safari_token');
    var name = localStorage.getItem('safari_name');
    var loginLink = document.getElementById('loginLink');
    var logoutBtn = document.getElementById('logoutBtn');
    var userName = document.getElementById('userName');
    
    if(token && name){
        userName.textContent = '👤 ' + name;
        loginLink.style.display = 'none';
        logoutBtn.style.display = 'inline-block';
    } else {
        userName.textContent = '';
        loginLink.style.display = 'inline';
        logoutBtn.style.display = 'none';
    }
}

function logoutUser(){
    localStorage.removeItem('safari_token');
    localStorage.removeItem('safari_name');
    updateUserUI();
    window.location.href = '/';
}

function loadChats(){
    try{
        var saved=localStorage.getItem('safari_chats_lite');
        if(saved) chats=JSON.parse(saved);
    }catch(e){ chats = {}; }
    if(!chats || Object.keys(chats).length===0){
        chats = {};
        var id='chat_'+Date.now();
        chats[id]={name:'New Chat',messages:[],timestamps:[]};
        saveChats();
    }
}

function saveChats(){
    try{
        localStorage.setItem('safari_chats_lite',JSON.stringify(chats));
    }catch(e){}
}

function getChatPreview(messages){
    if(!messages||messages.length===0) return 'New Chat';
    for(var i=0;i<messages.length;i++){
        if(messages[i].startsWith('U:')) return messages[i].substring(2).substring(0,30);
    }
    return 'Chat';
}

function sanitize(str){
    return str.replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function renderTabs(){
    var tabs=document.getElementById('tabs');
    tabs.innerHTML='';
    var chatIds=Object.keys(chats);
    chatIds.forEach(function(id){
        var chat=chats[id];
        if(!chat.name||chat.name==='New Chat'){
            chat.name=getChatPreview(chat.messages);
        }
        var tab=document.createElement('div');
        tab.className='tab'+(id===activeChat?' active':'');
        tab.title='Click to switch | Right-click to rename';
        var nameSpan=document.createElement('span');
        nameSpan.className='tab-name';
        var displayName=chat.name.length>20?chat.name.substring(0,20)+'...':chat.name;
        nameSpan.textContent=displayName;
        tab.appendChild(nameSpan);
        tab.addEventListener('click', function(e) {
            if (e.target.classList.contains('del') || e.target.classList.contains('rename-btn')) return;
            switchChat(id);
        });
        tab.addEventListener('contextmenu', function(e){
            e.preventDefault();
            renameChatTab(id);
        });
        if(chatIds.length>1){
            var renameBtn=document.createElement('span');
            renameBtn.className='rename-btn';
            renameBtn.innerHTML='&#9998;';
            renameBtn.title='Rename chat';
            renameBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                e.preventDefault();
                renameChatTab(id);
            });
            tab.appendChild(renameBtn);
            var del=document.createElement('span');
            del.className='del';
            del.innerHTML='&#215;';
            del.title='Delete chat';
            del.addEventListener('click', function(e) {
                e.stopPropagation();
                e.preventDefault();
                if(confirm('Delete this chat permanently? This cannot be undone.')) {
                    deleteChat(id);
                }
            });
            tab.appendChild(del);
        }
        tabs.appendChild(tab);
    });
    var addBtn=document.createElement('div');
    addBtn.className='tab add';
    addBtn.innerHTML='+';
    addBtn.title='New Chat';
    addBtn.addEventListener('click', newChat);
    tabs.appendChild(addBtn);
}

function renameChatTab(id){
    var chat=chats[id];
    var newName=prompt('Enter a new name for this chat:',chat.name||'New Chat');
    if(newName&&newName.trim()){
        chat.name=newName.trim();
        saveChats();
        renderTabs();
    }
}

function switchChat(id){
    activeChat=id;
    renderTabs();
    renderMessages();
}

function newChat(){
    var chatIds = Object.keys(chats);
    for(var i=0;i<chatIds.length;i++){
        var c = chats[chatIds[i]];
        if(!c.messages || c.messages.length===0){
            activeChat = chatIds[i];
            renderTabs();
            renderMessages();
            return;
        }
    }
    var id='chat_'+Date.now();
    chats[id]={name:'New Chat',messages:[],timestamps:[]};
    activeChat=id;
    saveChats();
    renderTabs();
    renderMessages();
}

function deleteChat(id){
    if(Object.keys(chats).length<=1){
        showToast('Cannot delete the last chat');
        return;
    }
    delete chats[id];
    saveChats();
    if(activeChat===id){
        activeChat=Object.keys(chats)[0];
    }
    renderTabs();
    renderMessages();
    showToast('Chat deleted');
}

function exportChat(){
    if(!activeChat||!chats[activeChat]) return;
    var chat=chats[activeChat];
    var text='Safari AI Lite Chat Export\\n';
    text+='Date: '+new Date().toLocaleString()+'\\n';
    text+='Chat: '+chat.name+'\\n';
    text+='='.repeat(50)+'\\n\\n';
    var msgs=chat.messages||[];
    var times=chat.timestamps||[];
    msgs.forEach(function(m,i){
        var time=times[i]?new Date(times[i]).toLocaleTimeString():'';
        if(m.startsWith('U:')){
            text+='You ('+time+'): '+m.substring(2)+'\\n\\n';
        }else if(m.startsWith('S:')){
            text+='Safari AI ('+time+'): '+m.substring(2)+'\\n\\n';
        }
    });
    var blob=new Blob([text],{type:'text/plain'});
    var url=URL.createObjectURL(blob);
    var a=document.createElement('a');
    a.href=url;
    a.download='safari-ai-lite-chat-'+activeChat+'.txt';
    a.click();
    URL.revokeObjectURL(url);
    showToast('Chat exported!');
}

function copyMessage(text,btn){
    navigator.clipboard.writeText(text).then(function(){
        btn.classList.add('copied');
        btn.innerHTML='&#10003;';
        setTimeout(function(){
            btn.classList.remove('copied');
            btn.innerHTML='&#128203;';
        },1500);
        showToast('Copied to clipboard');
    });
}

function editMessage(index,msgDiv){
    var chat=chats[activeChat];
    var msg=chat.messages[index];
    var content=msg.substring(2);
    var editDiv=document.createElement('div');
    editDiv.className='m u';
    editDiv.style.width='75%';
    var input=document.createElement('textarea');
    input.className='edit-input';
    input.value=content;
    input.rows=Math.min(5,content.split('\\n').length);
    editDiv.appendChild(input);
    var actions=document.createElement('div');
    actions.className='edit-actions';
    var saveBtn=document.createElement('button');
    saveBtn.className='btn-save';
    saveBtn.textContent='Save';
    saveBtn.onclick=function(){
        var newContent=input.value.trim();
        if(newContent){
            chat.messages=chat.messages.slice(0, index);
            chat.timestamps=chat.timestamps.slice(0, index);
            chat.messages.push('U:'+newContent);
            chat.timestamps.push(Date.now());
            saveChats();
            renderMessages();
            setProcessing(true);
            sendEditedMessage(newContent);
        }
    };
    var cancelBtn=document.createElement('button');
    cancelBtn.className='btn-cancel';
    cancelBtn.textContent='Cancel';
    cancelBtn.onclick=function(){
        renderMessages();
    };
    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    editDiv.appendChild(actions);
    msgDiv.parentElement.replaceChild(editDiv,msgDiv);
    input.focus();
    input.setSelectionRange(input.value.length,input.value.length);
}

async function sendEditedMessage(question){
    try{
        var form=new FormData();
        form.append('question',question);
        form.append('session',activeChat);
        var r=await fetch('/ask',{method:'POST',body:form});
        if(r.status===429){
            chats[activeChat].messages.push('S:Warning: Rate limit reached.');
            chats[activeChat].timestamps.push(Date.now());
        }else{
            var d=await r.json();
            chats[activeChat].messages.push('S:'+d.response);
            chats[activeChat].timestamps.push(Date.now());
        }
    }catch(e){
        chats[activeChat].messages.push('S:Connection error. Please try again.');
        chats[activeChat].timestamps.push(Date.now());
    }
    saveChats();
    renderMessages();
    setProcessing(false);
}

function renderMessages(){
    var box=document.getElementById('b');
    if(!box) return;
    box.innerHTML='';
    if(!activeChat||!chats[activeChat]) return;
    var msgs=chats[activeChat].messages||[];
    var times=chats[activeChat].timestamps||[];
    if(msgs.length===0){
        box.innerHTML='<div class="m s" style="max-width:60%">&#x1F981; Hello! Ask me anything or attach a file!</div>';
    }
    msgs.forEach(function(m,i){
        var wrapper=document.createElement('div');
        if(m.startsWith('U:')){
            wrapper.className='m-wrapper user';
            var msgDiv=document.createElement('div');
            msgDiv.className='m u';
            msgDiv.textContent=m.substring(2);
            wrapper.appendChild(msgDiv);
            var actions=document.createElement('div');
            actions.className='msg-actions';
            var editBtn=document.createElement('button');
            editBtn.className='msg-action-btn btn-edit';
            editBtn.innerHTML='&#9998;';
            editBtn.title='Edit message';
            editBtn.onclick=function(){editMessage(i,msgDiv);};
            actions.appendChild(editBtn);
            wrapper.appendChild(actions);
        }else if(m.startsWith('S:')){
            wrapper.className='m-wrapper bot';
            var msgDiv2=document.createElement('div');
            msgDiv2.className='m s';
            msgDiv2.textContent=m.substring(2);
            wrapper.appendChild(msgDiv2);
            var actions2=document.createElement('div');
            actions2.className='msg-actions';
            var copyBtn=document.createElement('button');
            copyBtn.className='msg-action-btn btn-copy';
            copyBtn.innerHTML='&#128203;';
            copyBtn.title='Copy message';
            copyBtn.onclick=function(){copyMessage(m.substring(2),copyBtn);};
            actions2.appendChild(copyBtn);
            wrapper.appendChild(actions2);
        }
        if(times[i]){
            var timeStamp=document.createElement('div');
            timeStamp.className='time-stamp';
            timeStamp.textContent=new Date(times[i]).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
            if(m.startsWith('U:')){
                wrapper.insertBefore(timeStamp,wrapper.firstChild);
            }else{
                wrapper.appendChild(timeStamp);
            }
        }
        box.appendChild(wrapper);
    });
    box.scrollTop=box.scrollHeight;
}

function setProcessing(state){
    isProcessing=state;
    var btn=document.getElementById('askBtn');
    var typing=document.getElementById('typing');
    if(state){
        btn.disabled=true;
        btn.textContent='Thinking...';
        typing.classList.add('show');
    }else{
        btn.disabled=false;
        btn.textContent='Ask';
        typing.classList.remove('show');
    }
}

async function ask(){
    if(isProcessing) return;
    var input = document.getElementById('q');
    var question = input.value.trim();
    if(!question && !pendingFile) return;
    if(question.length > 1000){
        showToast('Message too long.');
        return;
    }
    if(!activeChat || !chats[activeChat]) newChat();
    if(!chats[activeChat].messages) chats[activeChat].messages = [];
    if(!chats[activeChat].timestamps) chats[activeChat].timestamps = [];

    var displayText = question;
    if(pendingFile){
        displayText = question ? question + ' [Attached: ' + pendingFile.name + ']' : '[Attached: ' + pendingFile.name + ']';
    }
    chats[activeChat].messages.push('U:' + displayText);
    chats[activeChat].timestamps.push(Date.now());
    saveChats();
    renderTabs();
    renderMessages();
    input.value = '';
    var preview = document.getElementById('filePreview');
    preview.style.display = 'none';
    setProcessing(true);

    try{
        if(pendingFile){
            var uploadForm = new FormData();
            uploadForm.append('session', activeChat);
            uploadForm.append('file', pendingFile);
            if(question) uploadForm.append('question', question);
            var uploadResp = await fetch('/upload', {method:'POST', body:uploadForm});
            var uploadData = await uploadResp.json();
            if(uploadData.status === 'success'){
                var askForm = new FormData();
                askForm.append('session', activeChat);
                askForm.append('question', question || 'Please analyze the attached file');
                var askResp = await fetch('/ask', {method:'POST', body:askForm});
                var askData = await askResp.json();
                chats[activeChat].messages.push('S:' + askData.response);
                chats[activeChat].timestamps.push(Date.now());
            } else {
                chats[activeChat].messages.push('S:' + (uploadData.message || 'Upload failed.'));
                chats[activeChat].timestamps.push(Date.now());
            }
            pendingFile = null;
        } else {
            var form = new FormData();
            form.append('question', question);
            form.append('session', activeChat);
            var r = await fetch('/ask', {method:'POST', body:form});
            var d = await r.json();
            chats[activeChat].messages.push('S:' + d.response);
            chats[activeChat].timestamps.push(Date.now());
        }
    } catch(e){
        chats[activeChat].messages.push('S:Connection error. Please try again.');
        chats[activeChat].timestamps.push(Date.now());
    }
    saveChats();
    renderMessages();
    setProcessing(false);
}

let pendingFile = null;

function selectFile(input){
    var file = input.files[0];
    if(!file) return;
    if(file.size > 2 * 1024 * 1024){
        showToast('File too large. Maximum 2MB.');
        input.value='';
        return;
    }
    pendingFile = file;
    var preview = document.getElementById('filePreview');
    document.getElementById('fileName').textContent = file.name;
    preview.style.display = 'flex';
    showToast('File ready. Type your question and press Ask.');
}

function clearAttachment(){
    pendingFile = null;
    var input = document.getElementById('fileInput');
    input.value = '';
    var preview = document.getElementById('filePreview');
    preview.style.display = 'none';
    showToast('Attachment removed.');
}

document.addEventListener('DOMContentLoaded', function(){
    updateUserUI();
    loadChats();
    if(!activeChat){
        var chatIds = Object.keys(chats);
        var existingEmpty = null;
        for(var i=0;i<chatIds.length;i++){
            var c = chats[chatIds[i]];
            if(!c.messages || c.messages.length===0){
                existingEmpty = chatIds[i];
                break;
            }
        }
        if(existingEmpty){
            activeChat = existingEmpty;
        } else {
            activeChat = 'chat_' + Date.now();
            chats[activeChat] = {name:'New Chat',messages:[],timestamps:[]};
            saveChats();
        }
    }
    renderTabs();
    renderMessages();
});
</script></body></html>"""


# ============================================
# MANIFEST
# ============================================

@app.get("/manifest.json")
async def manifest():
    return {
        "name": "Safari AI Lite",
        "short_name": "SafariLite",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#fffaf5",
        "theme_color": "#d2691e",
        "description": "Explore Beyond Limits"
    }


# ============================================
# APP ENTRY POINT
# ============================================

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Safari AI Lite server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
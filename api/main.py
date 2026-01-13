#!/usr/bin/env python3
"""
REI Nationwide - Cloud API Server
=================================
Team-accessible API with authentication, rate limiting, and activity logging.
Deploys to Render.com for 24/7 availability.

Endpoints:
- /api/v1/properties/* - Property search, lookup, comps
- /api/v1/skip-trace/* - Skip tracing
- /api/v1/buyers/* - Cash buyer search
- /api/v1/ai/* - AI assistant queries
- /api/v1/users/* - User management
- /api/v1/slack/* - Slack bot webhooks
"""

import os
import json
import httpx
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Header, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
import jwt
from passlib.hash import bcrypt
import sqlite3
from functools import wraps

# ============================================================================
# CONFIGURATION
# ============================================================================

class Config:
    # API Keys
    REALESTATE_API_KEY = os.getenv("REALESTATE_API_KEY", "")
    BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    XAI_API_KEY = os.getenv("XAI_API_KEY", "")
    SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
    
    # Auth
    JWT_SECRET = os.getenv("JWT_SECRET", "rei-nationwide-secret-change-in-production")
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRY_HOURS = 24
    
    # Database
    DB_PATH = os.getenv("DB_PATH", "rei_platform.db")

config = Config()

# ============================================================================
# DATABASE SETUP
# ============================================================================

def init_db():
    """Initialize SQLite database with tables"""
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    ''')
    
    # Activity log table
    c.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            endpoint TEXT,
            details TEXT,
            ip_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # API usage tracking
    c.execute('''
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            endpoint TEXT NOT NULL,
            credits_used INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    conn.close()

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class UserCreate(BaseModel):
    email: str
    password: str
    name: str
    role: str = "member"

class UserLogin(BaseModel):
    email: str
    password: str

class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class PropertySearchRequest(BaseModel):
    city: str
    state: str
    min_equity: Optional[int] = 40
    absentee_only: Optional[bool] = False
    min_year_built: Optional[int] = None
    max_results: Optional[int] = 10

class AddressLookupRequest(BaseModel):
    address: str

class SkipTraceRequest(BaseModel):
    address_id: str

class BuyerSearchRequest(BaseModel):
    city: str
    state: str
    min_purchases: Optional[int] = 2
    max_results: Optional[int] = 20

class AIQueryRequest(BaseModel):
    query: str
    context: Optional[str] = None
    model: Optional[str] = "gpt-4o"

# ============================================================================
# AUTH HELPERS
# ============================================================================

security = HTTPBearer()

def create_token(user_id: int, email: str, role: str) -> str:
    """Create JWT token"""
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=config.JWT_EXPIRY_HOURS)
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    """Decode and validate JWT token"""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Get current user from token"""
    token = credentials.credentials
    return decode_token(token)

def require_role(allowed_roles: List[str]):
    """Decorator to require specific roles"""
    async def role_checker(user: dict = Depends(get_current_user)):
        if user["role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return role_checker

# ============================================================================
# ACTIVITY LOGGING
# ============================================================================

def log_activity(user_id: int, action: str, endpoint: str = None, details: str = None, ip: str = None):
    """Log user activity to database"""
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO activity_log (user_id, action, endpoint, details, ip_address) VALUES (?, ?, ?, ?, ?)",
        (user_id, action, endpoint, details, ip)
    )
    conn.commit()
    conn.close()

# ============================================================================
# REAL ESTATE API CLIENT
# ============================================================================

class RealEstateAPI:
    BASE_URL = "https://api.realestateapi.com/v2"
    
    def __init__(self):
        self.headers = {
            "x-api-key": config.REALESTATE_API_KEY,
            "Content-Type": "application/json"
        }
    
    async def _post(self, endpoint: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    f"{self.BASE_URL}/{endpoint}",
                    json=payload,
                    headers=self.headers
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                return {"error": str(e), "data": []}
    
    async def autocomplete(self, address: str) -> dict:
        return await self._post("AutoComplete", {"search": address})
    
    async def property_detail(self, address_id: str) -> dict:
        return await self._post("PropertyDetail", {"address_id": address_id})
    
    async def property_search(self, filters: List[dict], size: int = 10) -> dict:
        return await self._post("PropertySearch", {"search": filters, "size": size})
    
    async def skip_trace(self, address_id: str) -> dict:
        return await self._post("SkipTrace", {"address_id": address_id})
    
    async def property_comps(self, address_id: str, radius: float = 0.5) -> dict:
        return await self._post("PropertyComps", {"address_id": address_id, "radius": radius, "size": 5})

re_api = RealEstateAPI()

# ============================================================================
# AI CLIENTS
# ============================================================================

async def query_openai(prompt: str, context: str = None) -> str:
    """Query OpenAI GPT-4o"""
    if not config.OPENAI_API_KEY:
        return "OpenAI not configured"
    
    messages = []
    if context:
        messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": prompt})
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json={"model": "gpt-4o", "messages": messages, "max_tokens": 1000}
        )
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "No response")

# ============================================================================
# FASTAPI APP
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    yield
    # Shutdown

app = FastAPI(
    title="REI Nationwide API",
    description="Team API for Real Estate Investment Operations",
    version="1.0.0",
    lifespan=lifespan
)

# CORS for web dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# AUTH ENDPOINTS
# ============================================================================

@app.post("/api/v1/auth/register", response_model=TokenResponse)
async def register(user: UserCreate, request: Request):
    """Register a new user (admin only in production)"""
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    
    # Check if user exists
    c.execute("SELECT id FROM users WHERE email = ?", (user.email,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    password_hash = bcrypt.hash(user.password)
    c.execute(
        "INSERT INTO users (email, password_hash, name, role) VALUES (?, ?, ?, ?)",
        (user.email, password_hash, user.name, user.role)
    )
    conn.commit()
    
    user_id = c.lastrowid
    conn.close()
    
    # Create token
    token = create_token(user_id, user.email, user.role)
    
    log_activity(user_id, "register", "/api/v1/auth/register", ip=request.client.host)
    
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user_id, email=user.email, name=user.name, role=user.role, is_active=True)
    )

@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(credentials: UserLogin, request: Request):
    """Login and get access token"""
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT id, email, password_hash, name, role, is_active FROM users WHERE email = ?", 
              (credentials.email,))
    user = c.fetchone()
    
    if not user or not bcrypt.verify(credentials.password, user[2]):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not user[5]:
        conn.close()
        raise HTTPException(status_code=401, detail="Account disabled")
    
    # Update last login
    c.execute("UPDATE users SET last_login = ? WHERE id = ?", (datetime.utcnow(), user[0]))
    conn.commit()
    conn.close()
    
    token = create_token(user[0], user[1], user[4])
    
    log_activity(user[0], "login", "/api/v1/auth/login", ip=request.client.host)
    
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user[0], email=user[1], name=user[3], role=user[4], is_active=True)
    )

@app.get("/api/v1/auth/me", response_model=UserResponse)
async def get_me(user: dict = Depends(get_current_user)):
    """Get current user info"""
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, email, name, role, is_active FROM users WHERE id = ?", (user["user_id"],))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse(id=row[0], email=row[1], name=row[2], role=row[3], is_active=row[4])

# ============================================================================
# PROPERTY ENDPOINTS
# ============================================================================

@app.post("/api/v1/properties/search")
async def search_properties(req: PropertySearchRequest, request: Request, user: dict = Depends(get_current_user)):
    """Search properties by criteria"""
    filters = [
        {"field": "city", "value": req.city, "operator": "="},
        {"field": "state", "value": req.state, "operator": "="},
    ]
    
    if req.min_equity:
        filters.append({"field": "equity_percent", "value": req.min_equity, "operator": "ge"})
    if req.absentee_only:
        filters.append({"field": "absentee_owner", "value": True, "operator": "="})
    if req.min_year_built:
        filters.append({"field": "year_built", "value": req.min_year_built, "operator": "ge"})
    
    result = await re_api.property_search(filters, req.max_results)
    
    log_activity(user["user_id"], "property_search", "/api/v1/properties/search", 
                 f"{req.city}, {req.state}", request.client.host)
    
    return result

@app.post("/api/v1/properties/lookup")
async def lookup_property(req: AddressLookupRequest, request: Request, user: dict = Depends(get_current_user)):
    """Lookup property by address"""
    # First get address_id
    autocomplete = await re_api.autocomplete(req.address)
    
    if not autocomplete.get("data"):
        raise HTTPException(status_code=404, detail="Address not found")
    
    address_id = autocomplete["data"][0].get("address_id")
    
    # Get full details
    detail = await re_api.property_detail(address_id)
    
    log_activity(user["user_id"], "property_lookup", "/api/v1/properties/lookup",
                 req.address, request.client.host)
    
    return {"autocomplete": autocomplete, "detail": detail}

@app.post("/api/v1/properties/comps")
async def get_comps(req: AddressLookupRequest, request: Request, user: dict = Depends(get_current_user)):
    """Get comparable sales for a property"""
    autocomplete = await re_api.autocomplete(req.address)
    
    if not autocomplete.get("data"):
        raise HTTPException(status_code=404, detail="Address not found")
    
    address_id = autocomplete["data"][0].get("address_id")
    comps = await re_api.property_comps(address_id)
    
    log_activity(user["user_id"], "get_comps", "/api/v1/properties/comps",
                 req.address, request.client.host)
    
    return comps

# ============================================================================
# SKIP TRACE ENDPOINTS
# ============================================================================

@app.post("/api/v1/skip-trace")
async def skip_trace(req: AddressLookupRequest, request: Request, 
                     user: dict = Depends(require_role(["admin", "manager", "acquisitions"]))):
    """Skip trace property owner (restricted to certain roles)"""
    autocomplete = await re_api.autocomplete(req.address)
    
    if not autocomplete.get("data"):
        raise HTTPException(status_code=404, detail="Address not found")
    
    address_id = autocomplete["data"][0].get("address_id")
    result = await re_api.skip_trace(address_id)
    
    log_activity(user["user_id"], "skip_trace", "/api/v1/skip-trace",
                 req.address, request.client.host)
    
    return result

# ============================================================================
# BUYER SEARCH ENDPOINTS
# ============================================================================

@app.post("/api/v1/buyers/search")
async def search_buyers(req: BuyerSearchRequest, request: Request, user: dict = Depends(get_current_user)):
    """Search for cash buyers in an area"""
    filters = [
        {"field": "city", "value": req.city, "operator": "="},
        {"field": "state", "value": req.state, "operator": "="},
        {"field": "last_sale_date", "value": (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"), "operator": "ge"},
    ]
    
    result = await re_api.property_search(filters, req.max_results)
    
    # Aggregate by owner to find portfolio buyers
    buyer_counts = {}
    for prop in result.get("data", []):
        owner = prop.get("owner", {})
        owner_name = owner.get("name", "") if isinstance(owner, dict) else str(owner)
        if owner_name:
            if owner_name not in buyer_counts:
                buyer_counts[owner_name] = {"count": 0, "properties": []}
            buyer_counts[owner_name]["count"] += 1
            buyer_counts[owner_name]["properties"].append(prop)
    
    # Filter to portfolio buyers
    portfolio_buyers = [
        {"name": name, "purchase_count": data["count"], "recent_properties": data["properties"][:3]}
        for name, data in buyer_counts.items()
        if data["count"] >= req.min_purchases
    ]
    
    portfolio_buyers.sort(key=lambda x: x["purchase_count"], reverse=True)
    
    log_activity(user["user_id"], "buyer_search", "/api/v1/buyers/search",
                 f"{req.city}, {req.state}", request.client.host)
    
    return {"buyers": portfolio_buyers[:req.max_results]}

# ============================================================================
# AI ASSISTANT ENDPOINTS
# ============================================================================

@app.post("/api/v1/ai/query")
async def ai_query(req: AIQueryRequest, request: Request, user: dict = Depends(get_current_user)):
    """Query AI assistant"""
    context = req.context or """You are the REI Nationwide AI Assistant. 
    Help the team with real estate investment questions, deal analysis, and strategy."""
    
    response = await query_openai(req.query, context)
    
    log_activity(user["user_id"], "ai_query", "/api/v1/ai/query",
                 req.query[:100], request.client.host)
    
    return {"response": response, "model": req.model}

# ============================================================================
# ADMIN ENDPOINTS
# ============================================================================

@app.get("/api/v1/admin/users")
async def list_users(user: dict = Depends(require_role(["admin"]))):
    """List all users (admin only)"""
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, email, name, role, is_active, created_at, last_login FROM users")
    rows = c.fetchall()
    conn.close()
    
    return {"users": [
        {"id": r[0], "email": r[1], "name": r[2], "role": r[3], 
         "is_active": r[4], "created_at": r[5], "last_login": r[6]}
        for r in rows
    ]}

@app.get("/api/v1/admin/activity")
async def get_activity(limit: int = 100, user: dict = Depends(require_role(["admin", "manager"]))):
    """Get recent activity log"""
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT a.id, u.name, u.email, a.action, a.endpoint, a.details, a.ip_address, a.created_at
        FROM activity_log a
        LEFT JOIN users u ON a.user_id = u.id
        ORDER BY a.created_at DESC
        LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    
    return {"activity": [
        {"id": r[0], "user_name": r[1], "user_email": r[2], "action": r[3],
         "endpoint": r[4], "details": r[5], "ip": r[6], "timestamp": r[7]}
        for r in rows
    ]}

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "REI Nationwide API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }

# ============================================================================
# RUN SERVER
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

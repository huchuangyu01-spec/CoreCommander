import os
import sqlite3
import json
import rsa
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, Depends, Cookie, Response
from pydantic import BaseModel
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "licenses.db")

app = FastAPI(title="Core Commander License Server")

# RSA Private Key for signing responses (Default fallback key for dev environment)
PRIVATE_KEY_PEM_DEFAULT = b"""-----BEGIN RSA PRIVATE KEY-----
MIIBPQIBAAJBAKDA4LlrjrYFYQY9SF53k20EmJ3eMGz7/qotsUYqwynVRmIpdoR+
UE8QxLi/1BgRC3L7tEIBhBqLw6rMvE1D20UCAwEAAQJAJA6fWXfSrulN9gRQ8z+H
BfD9+osX+ZocaTeOh9qXb3Be0neyN6z12Dm8jW417Qr1ECkYSPf4SLeNcL2gnBuQ
gQIjAOqV2FaitEcbH1CMQ9q195LhZFhwu6MLrZHTT1LwpQ3PIWkCHwCvbZ0vIQV9
qcWt2hhcr54XvvnugH3+6aUyyzf/030CIiUEvHz/c/98kjZ9y/9pk8YD93fVYmba
YuuOMwhdnU5Oj3ECHwCjai+G7HLJ+XEMnuIczPcu1ZbKRmWYJRvfMhDrPZ0CIwCf
Pb9J8Oe+0b/wTe07nAHXHbrYyq96jjeOXCOQcu991t2Z
-----END RSA PRIVATE KEY-----"""

env_key = os.environ.get("LICENSE_PRIVATE_KEY")
is_production = os.environ.get("APP_ENV", "").lower() == "production" or \
                os.environ.get("ENV", "").lower() == "production" or \
                os.environ.get("PRODUCTION", "").lower() in ("true", "1")

if env_key and env_key.strip():
    try:
        # Supports both raw PEM strings and base64 encoded strings
        PRIVATE_KEY_PEM = base64.b64decode(env_key) if not env_key.strip().startswith("-----") else env_key.encode('utf-8')
    except Exception:
        PRIVATE_KEY_PEM = env_key.encode('utf-8')
else:
    PRIVATE_KEY_PEM = PRIVATE_KEY_PEM_DEFAULT
    if is_production:
        raise ValueError("Fatal Startup Exception: LICENSE_PRIVATE_KEY environment variable is not defined in production mode!")
    print("[WARNING] Running with default development private key. Ensure to set LICENSE_PRIVATE_KEY in production!")

if is_production:
    try:
        loaded_key = rsa.PrivateKey.load_pkcs1(PRIVATE_KEY_PEM)
        default_key = rsa.PrivateKey.load_pkcs1(PRIVATE_KEY_PEM_DEFAULT)
        if loaded_key.n == default_key.n and loaded_key.e == default_key.e:
            raise ValueError("Fatal Startup Exception: LICENSE_PRIVATE_KEY is fallback-default in production mode!")
    except Exception as e:
        if isinstance(e, ValueError) and "Fatal Startup Exception" in str(e):
            raise
        pass

PRIVATE_KEY = rsa.PrivateKey.load_pkcs1(PRIVATE_KEY_PEM)

# Password hashing utilities
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000).hex()
    return f"pbkdf2_sha256$100000${salt}${pwd_hash}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        parts = hashed.split('$')
        if len(parts) != 4 or parts[0] != 'pbkdf2_sha256':
            return False
        iterations = int(parts[1])
        salt = parts[2]
        stored_hash = parts[3]
        pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), iterations).hex()
        return secrets.compare_digest(stored_hash, pwd_hash)
    except Exception:
        return False

# In-memory sessions store (Token -> User Info)
SESSIONS: Dict[str, dict] = {}
SESSION_EXPIRY_HOURS = 24

def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        with conn:
            cursor = conn.cursor()
            
            # Create users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,        -- 'admin' or 'agent'
                    name TEXT,                 -- agent display name
                    status TEXT NOT NULL,      -- 'active', 'disabled'
                    created_at DATETIME
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);')
            
            # Create licenses table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS licenses (
                    key TEXT PRIMARY KEY,
                    type TEXT NOT NULL,       -- 'trial' or 'permanent'
                    hwid TEXT,                -- Bound hardware ID
                    status TEXT NOT NULL,     -- 'unused', 'active', 'banned'
                    expire_time DATETIME      -- Null for permanent, or expiration date
                )
            ''')
            # Add indexes to speed up lookup/updates
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_licenses_hwid ON licenses(hwid);')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses(status);')
            
            # Create hwid_components table for multi-component signature verification
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS hwid_components (
                    key TEXT PRIMARY KEY,
                    bios_hash TEXT,
                    disk_hash TEXT,
                    uuid_hash TEXT,
                    cpu_hash TEXT,
                    FOREIGN KEY(key) REFERENCES licenses(key)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_components_bios ON hwid_components(bios_hash);')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_components_disk ON hwid_components(disk_hash);')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_components_uuid ON hwid_components(uuid_hash);')
            
            # Run schema updates (migrations) dynamically
            for col, col_type in [("agent_username", "TEXT"), ("created_at", "DATETIME"), ("activated_at", "DATETIME")]:
                try:
                    cursor.execute(f"ALTER TABLE licenses ADD COLUMN {col} {col_type};")
                except sqlite3.OperationalError:
                    pass
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_licenses_agent ON licenses(agent_username);')
            
            # Initialize default administrator user 'kireto' / '2217965124k'
            cursor.execute("SELECT 1 FROM users WHERE username=?", ("kireto",))
            if not cursor.fetchone():
                admin_pwd_hash = hash_password("2217965124k")
                cursor.execute('''
                    INSERT INTO users (username, password_hash, role, name, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', ("kireto", admin_pwd_hash, "admin", "管理员", "active", datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()

init_db()

class VerifyRequest(BaseModel):
    key: str
    hwid: str
    nonce: str = ""
    bios_hash: Optional[str] = None
    disk_hash: Optional[str] = None
    uuid_hash: Optional[str] = None
    cpu_hash: Optional[str] = None

def sign_response(data: dict, nonce: str) -> dict:
    data['nonce'] = nonce # 包含客户端的随机数防重放
    data['timestamp'] = int(datetime.now(timezone.utc).timestamp()) # 添加服务端当前时间戳防止历史报文重放
    payload_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
    signature = rsa.sign(payload_str.encode('utf-8'), PRIVATE_KEY, 'SHA-256')
    data['signature'] = base64.b64encode(signature).decode('utf-8')
    return data

@app.post("/api/verify")
def verify_license(req: VerifyRequest):
    key = req.key.strip().upper()
    hwid = req.hwid.strip()
    nonce = req.nonce.strip()
    
    if not key or not hwid:
        raise HTTPException(status_code=400, detail="Missing key or hwid")

    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            cursor = conn.cursor()
            cursor.execute("SELECT type, hwid, status, expire_time FROM licenses WHERE key=?", (key,))
            row = cursor.fetchone()
            
            if not row:
                return JSONResponse(sign_response({"success": False, "msg": "卡密无效或不存在"}, nonce))
                
            l_type, l_hwid, l_status, l_expire = row
            
            if l_status == "banned":
                return JSONResponse(sign_response({"success": False, "msg": "该卡密因违规已被封禁"}, nonce))

            now = datetime.now(timezone.utc)

            # 1. 首次激活绑定 (Fix TOCTOU with atomic update)
            if l_status == "unused":
                expire_time = None
                if l_type == "trial":
                    # Check if this HWID has already activated any other trial card
                    cursor.execute("SELECT 1 FROM licenses WHERE hwid=? AND type='trial'", (hwid,))
                    if cursor.fetchone():
                        return JSONResponse(sign_response({"success": False, "msg": "激活失败：本设备已激活过其他测试卡，一台机器只能使用一张测试卡"}, nonce))
                    
                    # Check if any individual hardware components match another activated trial card
                    if req.bios_hash or req.disk_hash or req.uuid_hash:
                        query = """
                            SELECT 1 FROM hwid_components hc
                            JOIN licenses l ON hc.key = l.key
                            WHERE l.type = 'trial' AND (
                                (hc.bios_hash = ? AND hc.bios_hash IS NOT NULL AND hc.bios_hash != '') OR
                                (hc.disk_hash = ? AND hc.disk_hash IS NOT NULL AND hc.disk_hash != '') OR
                                (hc.uuid_hash = ? AND hc.uuid_hash IS NOT NULL AND hc.uuid_hash != '')
                            )
                        """
                        cursor.execute(query, (req.bios_hash, req.disk_hash, req.uuid_hash))
                        if cursor.fetchone():
                            return JSONResponse(sign_response({"success": False, "msg": "激活失败：本设备已激活过其他测试卡，一台机器只能使用一张测试卡"}, nonce))

                    expire_time = now + timedelta(days=14)
                    
                cursor.execute('''
                    UPDATE licenses 
                    SET status='active', hwid=?, expire_time=?, activated_at=?
                    WHERE key=? AND status='unused'
                ''', (hwid, expire_time, now.isoformat(), key))
                
                if cursor.rowcount == 0:
                    return JSONResponse(sign_response({"success": False, "msg": "激活失败：卡密已被其他人抢占激活"}, nonce))
                    
                # Insert or replace multi-component hashes for strict mapping
                cursor.execute('''
                    INSERT OR REPLACE INTO hwid_components (key, bios_hash, disk_hash, uuid_hash, cpu_hash)
                    VALUES (?, ?, ?, ?, ?)
                ''', (key, req.bios_hash, req.disk_hash, req.uuid_hash, req.cpu_hash))

                return JSONResponse(sign_response({
                    "success": True, 
                    "msg": f"激活成功！已绑定本设备。类型: {'14天测试卡' if l_type == 'trial' else '永久正式卡'}",
                    "type": l_type,
                    "expire_timestamp": expire_time.timestamp() if expire_time else 0,
                    "hwid": hwid
                }, nonce))
                
            # 2. 已激活卡密，检查机器码
            if l_status == "active":
                if l_hwid != hwid:
                    return JSONResponse(sign_response({"success": False, "msg": "此卡密已绑定其他电脑，无法在本机使用"}, nonce))
                    
                # 检查是否过期
                if l_type == "trial" and l_expire:
                    # The expire_time in DB is string like "2026-06-30 05:40:26.123456+00:00"
                    expire_date = datetime.fromisoformat(l_expire) if "+" in l_expire else datetime.strptime(l_expire.split(".")[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if now > expire_date:
                        return JSONResponse(sign_response({"success": False, "msg": "测试卡已过期"}, nonce))
                    else:
                        return JSONResponse(sign_response({
                            "success": True, 
                            "msg": "验证成功",
                            "type": l_type,
                            "expire_timestamp": expire_date.timestamp(),
                            "hwid": hwid
                        }, nonce))
                        
                # 永久卡直接放行
                return JSONResponse(sign_response({
                    "success": True, 
                    "msg": "验证成功",
                    "type": l_type,
                    "expire_timestamp": 0,
                    "hwid": hwid
                }, nonce))
    finally:
        conn.close()

# Helper functions for auth dependencies
def get_current_user(session_token: Optional[str] = Cookie(None)) -> dict:
    if not session_token or session_token not in SESSIONS:
        raise HTTPException(status_code=401, detail="Unauthorized")
    session = SESSIONS[session_token]
    if datetime.now(timezone.utc) > session["expire_time"]:
        SESSIONS.pop(session_token, None)
        raise HTTPException(status_code=401, detail="Unauthorized")
    return session["user"]

def require_role(allowed_roles: List[str]):
    def dependency(user: dict = Depends(get_current_user)):
        if user["role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return dependency

# Authentication APIs
class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/login")
def login(req: LoginRequest, response: Response):
    username = req.username.strip()
    password = req.password
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash, role, name, status FROM users WHERE username=?", (username,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="用户名或密码错误")
            
        password_hash, role, name, status = row
        if status != "active":
            raise HTTPException(status_code=400, detail="该账号已被禁用")
            
        if not verify_password(password, password_hash):
            raise HTTPException(status_code=400, detail="用户名或密码错误")
            
        # Generate session
        session_token = secrets.token_urlsafe(32)
        expire_time = datetime.now(timezone.utc) + timedelta(hours=SESSION_EXPIRY_HOURS)
        
        SESSIONS[session_token] = {
            "user": {
                "username": username,
                "role": role,
                "name": name or username
            },
            "expire_time": expire_time
        }
        
        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            max_age=SESSION_EXPIRY_HOURS * 3600,
            samesite="lax"
        )
        return {"success": True, "role": role}
    finally:
        conn.close()

@app.post("/api/logout")
def logout(response: Response, session_token: Optional[str] = Cookie(None)):
    if session_token:
        SESSIONS.pop(session_token, None)
    response.delete_cookie("session_token")
    return {"success": True}

@app.get("/api/me")
def get_me(user: dict = Depends(get_current_user)):
    return user

# Admin APIs
@app.get("/api/admin/metrics")
def admin_metrics(user: dict = Depends(require_role(["admin"]))):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(1) FROM users WHERE role='agent'")
        agent_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT status, COUNT(1) FROM licenses GROUP BY status")
        status_counts = dict(cursor.fetchall())
        
        cursor.execute("SELECT type, COUNT(1) FROM licenses GROUP BY type")
        type_counts = dict(cursor.fetchall())
        
        recent_activations = []
        now = datetime.now(timezone.utc)
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            cursor.execute("SELECT COUNT(1) FROM licenses WHERE date(activated_at) = date(?)", (day,))
            count = cursor.fetchone()[0]
            recent_activations.append({"date": day, "count": count})
            
        return {
            "agent_count": agent_count,
            "status_counts": {
                "unused": status_counts.get("unused", 0),
                "active": status_counts.get("active", 0),
                "banned": status_counts.get("banned", 0)
            },
            "type_counts": {
                "trial": type_counts.get("trial", 0),
                "permanent": type_counts.get("permanent", 0)
            },
            "recent_activations": recent_activations
        }
    finally:
        conn.close()

@app.get("/api/admin/agents")
def admin_list_agents(user: dict = Depends(require_role(["admin"]))):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT username, name, status, created_at FROM users WHERE role='agent'")
        agents = []
        for row in cursor.fetchall():
            username, name, status, created_at = row
            cursor.execute('''
                SELECT 
                    COUNT(1) as total,
                    SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN status='unused' THEN 1 ELSE 0 END) as unused
                FROM licenses WHERE agent_username=?
            ''', (username,))
            total, active, unused = cursor.fetchone()
            agents.append({
                "username": username,
                "name": name,
                "status": status,
                "created_at": created_at,
                "assigned_count": total or 0,
                "activated_count": active or 0,
                "unused_count": unused or 0
            })
        return agents
    finally:
        conn.close()

class CreateAgentRequest(BaseModel):
    username: str
    password: str
    name: str

@app.post("/api/admin/agents")
def admin_create_agent(req: CreateAgentRequest, user: dict = Depends(require_role(["admin"]))):
    username = req.username.strip().lower()
    password = req.password
    name = req.name.strip()
    
    if not username or not password or not name:
        raise HTTPException(status_code=400, detail="所有字段均为必填项")
        
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE username=?", (username,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="代理商账号已存在")
            
        pwd_hash = hash_password(password)
        cursor.execute('''
            INSERT INTO users (username, password_hash, role, name, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (username, pwd_hash, "agent", name, "active", datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

@app.delete("/api/admin/agents/{username}")
def admin_delete_agent(username: str, user: dict = Depends(require_role(["admin"]))):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status='disabled' WHERE username=? AND role='agent'", (username,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

class ChangePasswordRequest(BaseModel):
    password: str

@app.post("/api/admin/agents/{username}/password")
def admin_change_agent_password(username: str, req: ChangePasswordRequest, user: dict = Depends(require_role(["admin"]))):
    if not req.password:
        raise HTTPException(status_code=400, detail="密码不能为空")
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        pwd_hash = hash_password(req.password)
        cursor.execute("UPDATE users SET password_hash=? WHERE username=? AND role='agent'", (pwd_hash, username))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

@app.get("/api/admin/licenses")
def admin_list_licenses(
    page: int = 1,
    limit: int = 50,
    search: str = "",
    status: str = "",
    type: str = "",
    agent: str = "",
    user: dict = Depends(require_role(["admin"]))
):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        
        query = "SELECT key, type, hwid, status, expire_time, agent_username, created_at, activated_at FROM licenses WHERE 1=1"
        params = []
        
        if search:
            query += " AND key LIKE ?"
            params.append(f"%{search.strip().upper()}%")
        if status:
            query += " AND status = ?"
            params.append(status)
        if type:
            query += " AND type = ?"
            params.append(type)
        if agent:
            if agent == "unassigned":
                query += " AND (agent_username IS NULL OR agent_username = '')"
            else:
                query += " AND agent_username = ?"
                params.append(agent)
                
        count_query = query.replace("key, type, hwid, status, expire_time, agent_username, created_at, activated_at", "COUNT(1)")
        cursor.execute(count_query, params)
        total = cursor.fetchone()[0]
        
        query += " ORDER BY created_at DESC, key ASC LIMIT ? OFFSET ?"
        params.extend([limit, (page - 1) * limit])
        
        cursor.execute(query, params)
        licenses = []
        for row in cursor.fetchall():
            licenses.append({
                "key": row[0],
                "type": row[1],
                "hwid": row[2],
                "status": row[3],
                "expire_time": row[4],
                "agent_username": row[5],
                "created_at": row[6],
                "activated_at": row[7]
            })
            
        return {"total": total, "licenses": licenses}
    finally:
        conn.close()

class AssignLicensesRequest(BaseModel):
    keys: List[str]
    agent_username: str

@app.post("/api/admin/licenses/assign")
def admin_assign_licenses(req: AssignLicensesRequest, user: dict = Depends(require_role(["admin"]))):
    agent = req.agent_username.strip()
    if not req.keys:
        raise HTTPException(status_code=400, detail="卡密列表不能为空")
    if len(req.keys) > 500:
        raise HTTPException(status_code=400, detail="单次批量分配卡密数量不能超过 500 张")
        
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        if agent != "":
            cursor.execute("SELECT 1 FROM users WHERE username=? AND role='agent'", (agent,))
            if not cursor.fetchone():
                raise HTTPException(status_code=400, detail="指定的代理商不存在")
                
        placeholders = ",".join(["?"] * len(req.keys))
        update_agent = agent if agent != "" else None
        now_str = datetime.now(timezone.utc).isoformat()
        
        cursor.execute(f'''
            UPDATE licenses 
            SET agent_username=?, created_at=COALESCE(created_at, ?)
            WHERE key IN ({placeholders})
        ''', [update_agent, now_str] + [k.strip().upper() for k in req.keys])
        conn.commit()
        return {"success": True, "count": cursor.rowcount}
    finally:
        conn.close()

class BanLicenseRequest(BaseModel):
    key: str
    ban: bool

@app.post("/api/admin/licenses/ban")
def admin_ban_license(req: BanLicenseRequest, user: dict = Depends(require_role(["admin"]))):
    status = "banned" if req.ban else "active"
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        if not req.ban:
            cursor.execute("SELECT hwid FROM licenses WHERE key=?", (req.key.upper(),))
            row = cursor.fetchone()
            if row and row[0]:
                status = "active"
            else:
                status = "unused"
                
        cursor.execute("UPDATE licenses SET status=? WHERE key=?", (status, req.key.upper()))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

# Agent APIs
@app.get("/api/agent/metrics")
def agent_metrics(user: dict = Depends(require_role(["agent"]))):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                COUNT(1) as total,
                SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN status='unused' THEN 1 ELSE 0 END) as unused
            FROM licenses WHERE agent_username=?
        ''', (user["username"],))
        total, active, unused = cursor.fetchone()
        return {
            "assigned_count": total or 0,
            "activated_count": active or 0,
            "unused_count": unused or 0
        }
    finally:
        conn.close()

@app.get("/api/agent/licenses")
def agent_list_licenses(
    page: int = 1,
    limit: int = 50,
    search: str = "",
    status: str = "",
    user: dict = Depends(require_role(["agent"]))
):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        query = "SELECT key, type, hwid, status, expire_time, created_at, activated_at FROM licenses WHERE agent_username = ?"
        params = [user["username"]]
        
        if search:
            query += " AND key LIKE ?"
            params.append(f"%{search.strip().upper()}%")
        if status:
            query += " AND status = ?"
            params.append(status)
            
        count_query = query.replace("key, type, hwid, status, expire_time, created_at, activated_at", "COUNT(1)")
        cursor.execute(count_query, params)
        total = cursor.fetchone()[0]
        
        query += " ORDER BY created_at DESC, key ASC LIMIT ? OFFSET ?"
        params.extend([limit, (page - 1) * limit])
        
        cursor.execute(query, params)
        licenses = []
        for row in cursor.fetchall():
            licenses.append({
                "key": row[0],
                "type": row[1],
                "hwid": row[2],
                "status": row[3],
                "expire_time": row[4],
                "created_at": row[5],
                "activated_at": row[6]
            })
        return {"total": total, "licenses": licenses}
    finally:
        conn.close()

@app.get("/api/agent/licenses/export")
def agent_export_unused(
    type: str = "",
    user: dict = Depends(require_role(["agent"]))
):
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        query = "SELECT key FROM licenses WHERE agent_username = ? AND status = 'unused'"
        params = [user["username"]]
        if type:
            query += " AND type = ?"
            params.append(type)
        query += " ORDER BY key ASC"
        cursor.execute(query, params)
        keys = [row[0] for row in cursor.fetchall()]
        return {"keys": keys}
    finally:
        conn.close()

# HTML Pages Routes
@app.get("/", response_class=HTMLResponse)
def index_page(session_token: Optional[str] = Cookie(None)):
    if session_token and session_token in SESSIONS:
        user = SESSIONS[session_token]["user"]
        if user["role"] == "admin":
            return RedirectResponse(url="/admin", status_code=303)
        else:
            return RedirectResponse(url="/agent", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
def login_page():
    login_html_path = os.path.join(BASE_DIR, "static", "login.html")
    if os.path.exists(login_html_path):
        with open(login_html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Login Page Not Found</h1><p>Please build the static frontend files.</p>")

@app.get("/admin", response_class=HTMLResponse)
def admin_page(session_token: Optional[str] = Cookie(None)):
    try:
        user = get_current_user(session_token)
        if user["role"] != "admin":
            return RedirectResponse(url="/login", status_code=303)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
        
    admin_html_path = os.path.join(BASE_DIR, "static", "admin.html")
    if os.path.exists(admin_html_path):
        with open(admin_html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Admin Dashboard Not Found</h1>")

@app.get("/agent", response_class=HTMLResponse)
def agent_page(session_token: Optional[str] = Cookie(None)):
    try:
        user = get_current_user(session_token)
        if user["role"] != "agent":
            return RedirectResponse(url="/login", status_code=303)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
        
    agent_html_path = os.path.join(BASE_DIR, "static", "agent.html")
    if os.path.exists(agent_html_path):
        with open(agent_html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Agent Dashboard Not Found</h1>")

# Mount Static Files
try:
    os.makedirs(os.path.join(BASE_DIR, "static"), exist_ok=True)
except Exception:
    pass
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

if __name__ == "__main__":
    import uvicorn
    # uvicorn.run(app, host="0.0.0.0", port=8000)
    print("服务已配置就绪，可使用命令启动: uvicorn 云端鉴权服务端:app --host 0.0.0.0 --port 8000")


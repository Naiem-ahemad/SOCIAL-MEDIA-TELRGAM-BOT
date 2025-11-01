from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import Optional
from core.utils import Database
import os
from fastapi.middleware.cors import CORSMiddleware

# ---- CONFIG ---- #
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey123")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# ---- FASTAPI SETUP ---- #
app = FastAPI(title="Bot Admin API", version="1.1", description="Secured API for bot admin control")
db = Database()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

# --- Allow CORS (temporary for dev) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ✅ allow all origins (for now)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- JWT UTILS ---- #
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("sub") != ADMIN_USERNAME:
            raise HTTPException(status_code=403, detail="Not authorized")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload

# ---- MODELS ---- #
class BanRequest(BaseModel):
    user_id: int
    reason: Optional[str] = ""
    duration: Optional[int] = None

class UnbanRequest(BaseModel):
    user_id: int

# ---- LOGIN ---- #
@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username == ADMIN_USERNAME and form_data.password == ADMIN_PASSWORD:
        token = create_access_token({"sub": form_data.username})
        return {"access_token": token, "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="Invalid credentials")

# ---- ROUTES ---- #
@app.get("/")
async def root():
    return {"status": "ok", "message": "Bot API Running Securely", "time": datetime.now().isoformat()}

@app.get("/users", dependencies=[Depends(verify_token)])
async def get_all_users():
    return JSONResponse(db.get_all_users())

@app.get("/user/{user_id}", dependencies=[Depends(verify_token)])
async def get_user(user_id: int):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(404, detail="User not found")
    return user

@app.get("/stats", dependencies=[Depends(verify_token)])
async def get_stats():
    return {
        "total_users": db.get_total_users(),
        "total_downloads": db.get_total_downloads(),
        "top_users": db.get_top_users()
    }

@app.get("/media", dependencies=[Depends(verify_token)])
async def all_media():
    return db.get_all_media()

@app.post("/ban", dependencies=[Depends(verify_token)])
async def ban_user(data: BanRequest):
    db.ban_user(data.user_id, data.reason, data.duration)
    return {"status": "banned", "user_id": data.user_id}

@app.post("/unban", dependencies=[Depends(verify_token)])
async def unban_user(data: UnbanRequest):
    db.unban_user(data.user_id)
    return {"status": "unbanned", "user_id": data.user_id}

@app.get("/downloads/{user_id}", dependencies=[Depends(verify_token)])
async def user_downloads(user_id: int, limit: int = 10):
    return db.get_user_downloads(user_id, limit)

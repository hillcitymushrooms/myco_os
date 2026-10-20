"""Myco OS Cloud · © 2026 Hill City Mushrooms · MIT License.
Cloud-ready multi-tenant farm API. Team memberships are free across all plans.
"""
from __future__ import annotations
import hashlib, json, os, re, secrets, uuid, time, threading, urllib.request, urllib.error, csv, io, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import (create_engine, Column, String, Integer, Float, DateTime, ForeignKey, Text, inspect, text,
                        Boolean, UniqueConstraint, select, func, or_)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import IntegrityError
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
try:
    from redis import Redis
except ImportError:
    Redis = None

BASE = Path(__file__).parent
ENV = os.getenv('APP_ENV', 'development')
PROD = ENV == 'production'
log = logging.getLogger("myco.auth")
PUBLIC_URL = os.getenv("PUBLIC_APP_URL", "http://localhost:8000").rstrip("/")
if PROD:
    if not PUBLIC_URL.startswith("https://"):
        raise RuntimeError("Production requires HTTPS PUBLIC_APP_URL")
    if not os.getenv("REDIS_URL") or Redis is None:
        raise RuntimeError("Production requires REDIS_URL for distributed auth rate limiting")
    if not os.getenv("RESEND_API_KEY") or not os.getenv("MAIL_FROM"):
        raise RuntimeError("Production requires RESEND_API_KEY and MAIL_FROM for account verification and password recovery")
URL = os.getenv('DATABASE_URL') or f'sqlite:///{BASE / "myco-cloud.db"}'
if URL.startswith('postgres://'):
    URL = URL.replace('postgres://', 'postgresql+psycopg://', 1)
if URL.startswith('postgresql://'):
    URL = URL.replace('postgresql://', 'postgresql+psycopg://', 1)
engine = create_engine(URL, pool_pre_ping=True, connect_args={'check_same_thread': False} if URL.startswith('sqlite:') else {})
DB = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()
ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
NOW = lambda: datetime.now(timezone.utc)

def ident(): return str(uuid.uuid4())
def digest(s: str) -> str: return hashlib.sha256(s.encode()).hexdigest()

def stamp(d): return d.isoformat() if d else None

class User(Base):
    __tablename__ = 'users'
    id = Column(String(36), primary_key=True, default=ident)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    email_verified = Column(Boolean, nullable=False, default=False)
    display_name = Column(String(120), nullable=False)
    created_at = Column(DateTime(timezone=True), default=NOW)

class Farm(Base):
    __tablename__ = 'farms'
    id = Column(String(36), primary_key=True, default=ident)
    name = Column(String(140), nullable=False)
    slug = Column(String(140), nullable=False, unique=True)
    owner_id = Column(String(36), ForeignKey('users.id'), nullable=False)
    plan = Column(String(20), default='free', nullable=False)
    billing_status = Column(String(25), default='inactive', nullable=False)
    stripe_customer_id = Column(String(128), nullable=True)
    stripe_subscription_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=NOW)

class Membership(Base):
    __tablename__ = 'memberships'
    id = Column(String(36), primary_key=True, default=ident)
    farm_id = Column(String(36), ForeignKey('farms.id'), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False, index=True)
    role = Column(String(20), nullable=False, default='viewer')
    created_at = Column(DateTime(timezone=True), default=NOW)
    __table_args__ = (UniqueConstraint('farm_id','user_id'),)

class SessionToken(Base):
    __tablename__ = 'sessions'
    id = Column(String(36), primary_key=True, default=ident)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    csrf_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

class Invite(Base):
    __tablename__ = 'invites'
    id = Column(String(36), primary_key=True, default=ident)
    farm_id = Column(String(36), ForeignKey('farms.id'), nullable=False)
    email = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True))

class Record(Base):
    __tablename__ = 'farm_records'
    id = Column(String(36), primary_key=True, default=ident)
    farm_id = Column(String(36), ForeignKey('farms.id'), nullable=False, index=True)
    kind = Column(String(30), nullable=False, index=True)
    data = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(String(36), ForeignKey('users.id'), nullable=False)
    updated_by = Column(String(36), ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), default=NOW)
    updated_at = Column(DateTime(timezone=True), default=NOW)

class Harvest(Base):
    __tablename__ = 'harvests'
    id = Column(String(36), primary_key=True, default=ident)
    farm_id = Column(String(36), ForeignKey('farms.id'), nullable=False, index=True)
    batch_id = Column(String(36), nullable=False)
    species = Column(String(120), nullable=False)
    weight_lb = Column(Float, nullable=False)
    flush = Column(Integer, nullable=False, default=1)
    harvested_at = Column(DateTime(timezone=True), default=NOW)
    created_by = Column(String(36), ForeignKey('users.id'), nullable=False)
    note = Column(Text, default='')
    lot_id = Column(String(36), nullable=False)

class StockLot(Base):
    __tablename__ = 'stock_lots'
    id = Column(String(36), primary_key=True, default=ident)
    farm_id = Column(String(36), ForeignKey('farms.id'), nullable=False, index=True)
    species = Column(String(120), nullable=False)
    source_harvest_id = Column(String(36), ForeignKey('harvests.id'))
    quantity_lb = Column(Float, nullable=False, default=0)
    harvested_at = Column(DateTime(timezone=True), default=NOW)

class StockMovement(Base):
    __tablename__ = 'stock_movements'
    id = Column(String(36), primary_key=True, default=ident)
    farm_id = Column(String(36), ForeignKey('farms.id'), nullable=False, index=True)
    lot_id = Column(String(36), ForeignKey('stock_lots.id'), nullable=False)
    delta_lb = Column(Float, nullable=False)
    reason = Column(String(30), nullable=False)
    reference_id = Column(String(36), nullable=False)
    created_by = Column(String(36), ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), default=NOW)
    __table_args__ = (UniqueConstraint('lot_id','reason','reference_id'),)

class Order(Base):
    __tablename__ = 'orders'
    id = Column(String(36), primary_key=True, default=ident)
    farm_id = Column(String(36), ForeignKey('farms.id'), nullable=False, index=True)
    customer = Column(String(160), nullable=False)
    items_json = Column(Text, nullable=False)
    status = Column(String(20), default='draft')
    total_cents = Column(Integer, default=0)
    created_by = Column(String(36), ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), default=NOW)
    fulfilled_at = Column(DateTime(timezone=True))

class Audit(Base):
    __tablename__ = 'audit_events'
    id = Column(String(36), primary_key=True, default=ident)
    farm_id = Column(String(36), ForeignKey('farms.id'), nullable=False, index=True)
    actor_id = Column(String(36), ForeignKey('users.id'))
    action = Column(String(70), nullable=False)
    entity_type = Column(String(30), nullable=False)
    entity_id = Column(String(36), nullable=False)
    detail = Column(Text, default='')
    created_at = Column(DateTime(timezone=True), default=NOW)

class OneTimeToken(Base):
    __tablename__ = 'one_time_tokens'
    id = Column(String(36), primary_key=True, default=ident)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False, index=True)
    purpose = Column(String(25), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True))

class StripeEvent(Base):
    __tablename__ = 'stripe_events'
    id = Column(String(200), primary_key=True)
    received_at = Column(DateTime(timezone=True), default=NOW)

# Additive upgrade for existing SQLite/PostgreSQL installations. Back up first.
Base.metadata.create_all(bind=engine)
with engine.begin() as conn:
    cols = {c['name'] for c in inspect(conn).get_columns('users')}
    if 'email_verified' not in cols:
        # Existing users are grandfathered in to avoid locking legitimate farms out.
        conn.execute(text('ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT TRUE'))

app = FastAPI(title='Myco OS Cloud API', docs_url='/api/docs' if not PROD else None,
              redoc_url=None, openapi_url='/api/openapi.json' if not PROD else None)

_limits = {}
_limit_lock = threading.Lock()
redis_client = Redis.from_url(os.getenv('REDIS_URL'), decode_responses=True, socket_timeout=2) if (Redis is not None and os.getenv('REDIS_URL')) else None

def rate_limit(request: Request, scope: str, maximum: int, seconds: int, account: str=''):
    # Use the direct peer IP, never untrusted X-Forwarded-For supplied by clients.
    ip = request.client.host if request.client else 'unknown'
    ident_key = digest(account.lower().strip())[:20] if account else 'ip'
    key = f'myco:limit:{scope}:{digest(ip)[:20]}:{ident_key}'
    if redis_client is not None:
        try:
            # Atomic fixed-window limits shared across processes and machines.
            pipe = redis_client.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, seconds, nx=True)
            count, _ = pipe.execute()
        except Exception:
            if PROD: fail(503,'Authentication temporarily unavailable')
            count = 1
    else:
        if PROD: fail(503,'Authentication rate limiting is unavailable')
        now = time.monotonic()
        with _limit_lock:
            count, expiry = _limits.get(key, (0, now+seconds))
            if now >= expiry: count, expiry = 0, now+seconds
            count += 1
            _limits[key] = (count, expiry)
            if len(_limits)>5000:
                for k,(_,t) in list(_limits.items())[:1500]:
                    if t<now: _limits.pop(k,None)
    if count > maximum:
        fail(429,'Too many requests. Try again later.')

def mail_user(email: str, subject: str, body: str):
    if not PROD and not os.getenv('RESEND_API_KEY'):
        # Development only: printed to the local server terminal, never returned over API.
        print(f'\n[MYCO DEV EMAIL to {email}] {subject}\n{body}\n', flush=True)
        return
    key = os.getenv('RESEND_API_KEY')
    if not key: fail(503,'Email service is not configured')
    data = json.dumps({'from':os.getenv('MAIL_FROM','Myco OS <noreply@example.invalid>'), 'to':[email], 'subject':subject, 'text':body}).encode()
    req = urllib.request.Request('https://api.resend.com/emails', data=data,
        headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status not in (200,201,202): raise RuntimeError('Delivery rejected')
    except Exception:
        log.exception('Email delivery unsuccessful')
        fail(503,'Email service unavailable. Please try again later.')

def issue_token(db: Session, user_id: str, purpose: str, minutes: int=30):
    for old in db.scalars(select(OneTimeToken).where(OneTimeToken.user_id==user_id,OneTimeToken.purpose==purpose,OneTimeToken.used_at.is_(None))).all():
        old.used_at = NOW()
    token = secrets.token_urlsafe(48)
    db.add(OneTimeToken(user_id=user_id,purpose=purpose,token_hash=digest(token),expires_at=NOW()+timedelta(minutes=minutes)))
    db.commit()
    return token

def consume_token(db: Session, token: str, purpose: str):
    if len(token)>300 or len(token)<32: fail(400,'Invalid or expired link')
    entry=db.scalar(select(OneTimeToken).where(OneTimeToken.token_hash==digest(token),OneTimeToken.purpose==purpose,OneTimeToken.used_at.is_(None)).with_for_update())
    if not entry or entry.expires_at.replace(tzinfo=timezone.utc)<NOW(): fail(400,'Invalid or expired link')
    return entry

@app.middleware('http')
async def protect(request: Request, call_next):
    # JSON write requests are protected by session-bound X-CSRF-Token; login/signup use same-origin check.
    if request.method in ('POST','PUT','PATCH','DELETE') and request.url.path.startswith('/api/'):
        origin = request.headers.get('origin')
        if origin:
            from urllib.parse import urlsplit
            u = urlsplit(origin)
            if u.netloc != request.url.netloc and origin.rstrip('/') != os.getenv('PUBLIC_APP_URL','').rstrip('/'):
                return JSONResponse({'detail':'Cross-origin request refused'}, status_code=403)
    if request.url.path.startswith('/api/') and request.method in ('POST','PUT','PATCH'):
        declared=request.headers.get('content-length')
        if declared and declared.isdigit() and int(declared)>3_200_000:
            return JSONResponse({'detail':'Request too large'},status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Permissions-Policy']='camera=(self), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if PROD: response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    if request.url.path.startswith('/api/auth/') or request.url.path.startswith('/api/invites/'):
        response.headers['Cache-Control']='no-store'
    return response

@app.exception_handler(IntegrityError)
async def integrity_handler(request, exc):
    return JSONResponse({'detail':'Record conflicts with an existing entry'}, status_code=409)

class Signup(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=10, max_length=256)
    farm_name: str = Field(min_length=2, max_length=140)
class Login(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=256)
class EmailOnly(BaseModel):
    email: str = Field(max_length=255)
class TokenPassword(BaseModel):
    token: str = Field(min_length=32, max_length=300)
    password: str = Field(min_length=12, max_length=256)
class TokenOnly(BaseModel):
    token: str = Field(min_length=32, max_length=300)
class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12,max_length=256)
class NewFarm(BaseModel):
    name: str = Field(min_length=2, max_length=140)
class MemberRoleChange(BaseModel):
    role: str
class RecordIn(BaseModel):
    kind: str
    data: dict[str, Any]
class RecordEdit(BaseModel):
    data: dict[str, Any]
    revision: int
class HarvestIn(BaseModel):
    batch_id: str
    weight_lb: float = Field(gt=0, le=100000)
    flush: int = Field(ge=1, le=20, default=1)
    note: str = Field(default='', max_length=2000)
class OrderLine(BaseModel):
    lot_id: str
    quantity_lb: float = Field(gt=0, le=100000)
    unit_price_cents: int = Field(ge=0, le=10000000)
class OrderIn(BaseModel):
    customer: str = Field(min_length=1, max_length=160)
    lines: list[OrderLine] = Field(min_length=1, max_length=100)
class InviteIn(BaseModel):
    email: str
    role: str = 'technician'
class AcceptIn(BaseModel):
    token: str

# Team membership and employee accounts are unlimited and free on ALL tiers.
LIMITS = {'free': None, 'pro': None, 'growth': None}
DUMMY_HASH = ph.hash('not-a-real-password')
def strong_password(password):
    if len(password)<12 or len(password)>256 or password.lower() in {'passwordpassword','password12345','123456789012','qwertyqwerty'}:
        fail(400,'Use a unique password of at least 12 characters')
KINDS = {'batch','culture','recipe','processing','room','equipment','task','customer','expense','reading','experiment','incident','plan','sop','note'}
ROLE_KINDS = {
    'owner': KINDS, 'manager': KINDS,
    'technician': {'batch','culture','recipe','processing','room','task','reading','incident','note','experiment'},
    'sales': {'task','customer','note'}, 'viewer': set()
}

def fail(status: int, message: str): raise HTTPException(status, message)

def normalize_email(email: str):
    email=email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): fail(400,'Enter a valid email address')
    return email

def slugify(name: str):
    slug=re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')[:80]
    return slug or 'farm'

def add_audit(db, farm_id, actor_id, action, typ, entity_id, detail=''):
    db.add(Audit(farm_id=farm_id,actor_id=actor_id,action=action,entity_type=typ,entity_id=entity_id,detail=detail[:1000]))

def getdb():
    with DB() as db: yield db

def auth(request: Request, db: Session = Depends(getdb)):
    token=request.cookies.get('myco_session','')
    s=db.scalar(select(SessionToken).where(SessionToken.token_hash==digest(token))) if token else None
    if not s or s.expires_at.replace(tzinfo=timezone.utc)<NOW(): fail(401,'Sign in to continue')
    user=db.get(User,s.user_id)
    if not user: fail(401,'Session no longer valid')
    if request.method in ('POST','PUT','PATCH','DELETE'):
        csrf=request.headers.get('x-csrf-token','')
        if not csrf or not secrets.compare_digest(digest(csrf),s.csrf_hash): fail(403,'Missing or invalid security token. Refresh the page.')
    return (user,s)

def membership(farm_id, db, user_id):
    m=db.scalar(select(Membership).where(Membership.farm_id==farm_id,Membership.user_id==user_id))
    if not m: fail(404,'Farm not found')
    return m

def context(farm_id: str, db: Session, user_and_session):
    user,_=user_and_session
    m=membership(farm_id,db,user.id)
    farm=db.get(Farm,farm_id)
    if not farm: fail(404,'Farm not found')
    return farm,m,user

def require_write(m, kind):
    if kind not in ROLE_KINDS.get(m.role,set()): fail(403,'Your role does not allow this action')

def require_role(m, roles):
    if m.role not in roles: fail(403,'Owner or manager access required')

def serialize_record(r):
    return {'id':r.id,'kind':r.kind,'data':json.loads(r.data),'revision':r.revision,'created_at':stamp(r.created_at),'updated_at':stamp(r.updated_at),'updated_by':r.updated_by}

def record_input(kind, data):
    if kind not in KINDS: fail(400,'Unsupported record type')
    encoded=json.dumps(data,ensure_ascii=False,allow_nan=False)
    if len(encoded)>50000: fail(400,'Record too large')
    if kind=='batch':
        if not str(data.get('species','')).strip(): fail(400,'Species is required')
        if not str(data.get('name','')).strip(): fail(400,'Batch name is required')
        count=data.get('blocks',0)
        if not isinstance(count,(int,float)) or count<1 or count>100000: fail(400,'Valid block count required')
    if kind=='task' and not str(data.get('name','')).strip(): fail(400,'Task name is required')
    return encoded

def set_cookie(response, token):
    response.set_cookie('myco_session',token,httponly=True,secure=PROD,samesite='lax',max_age=60*60*24*14,path='/')

def start_session(db, user, response):
    token=secrets.token_urlsafe(48); csrf=secrets.token_urlsafe(32)
    db.add(SessionToken(user_id=user.id,token_hash=digest(token),csrf_hash=digest(csrf),expires_at=NOW()+timedelta(days=14)))
    db.commit(); set_cookie(response,token)
    return {'user':{'id':user.id,'email':user.email,'name':user.display_name},'csrf':csrf}

@app.get('/api/health')
def health(): return {'ok':True,'service':'myco-os-cloud'}

@app.post('/api/auth/signup')
def signup(payload: Signup,request:Request,response: Response,db: Session=Depends(getdb)):
    email=normalize_email(payload.email)
    rate_limit(request,'signup-ip',6 if PROD else 100,3600)
    rate_limit(request,'signup-email',3,3600,email)
    strong_password(payload.password)
    if db.scalar(select(User).where(User.email==email)): fail(409,'Email already registered')
    user=User(id=ident(),email=email,display_name=payload.name.strip(),password_hash=ph.hash(payload.password),email_verified=not PROD)
    farm=Farm(id=ident(),name=payload.farm_name.strip(),slug=slugify(payload.farm_name)+'-'+secrets.token_hex(3),owner_id=user.id)
    db.add_all([user,farm,Membership(farm_id=farm.id,user_id=user.id,role='owner')])
    db.commit()
    if PROD:
        token=issue_token(db,user.id,'verify',60*24)
        mail_user(email,'Verify your Myco OS account',f'Please verify your address to activate your farm: {PUBLIC_URL}/?verify={token}\n\nThis link expires in 24 hours.')
        return {'verification_required':True,'message':'Check your email for your verification link.'}
    out=start_session(db,user,response)
    out['farm_id']=farm.id
    return out

@app.post('/api/auth/login')
def login(payload: Login,request:Request,response:Response,db:Session=Depends(getdb)):
    email=normalize_email(payload.email)
    rate_limit(request,'login-ip',25,900)
    rate_limit(request,'login-account',8,900,email)
    user=db.scalar(select(User).where(User.email==email))
    try:
        ph.verify(user.password_hash if user else DUMMY_HASH,payload.password)
    except (VerifyMismatchError,VerificationError): fail(401,'Invalid credentials')
    if not user:fail(401,'Invalid credentials')
    if not user.email_verified:fail(403,'Please verify your email address before signing in')
    return start_session(db,user,response)

@app.post('/api/auth/password/forgot')
def forgot(payload:EmailOnly,request:Request,db:Session=Depends(getdb)):
    email=normalize_email(payload.email)
    rate_limit(request,'reset-ip',10,3600)
    rate_limit(request,'reset-account',3,3600,email)
    user=db.scalar(select(User).where(User.email==email))
    if user:
        token=issue_token(db,user.id,'reset',30)
        mail_user(email,'Reset your Myco OS password',f'Use this one-time link to reset your password: {PUBLIC_URL}/?reset={token}\n\nExpires in 30 minutes. If you did not request this, ignore this email.')
    return {'ok':True,'message':'If this account exists, a password reset link has been sent.'}

@app.post('/api/auth/password/reset')
def reset_password(payload:TokenPassword,request:Request,db:Session=Depends(getdb)):
    rate_limit(request,'reset-attempt',12,3600)
    strong_password(payload.password)
    entry=consume_token(db,payload.token,'reset')
    user=db.get(User,entry.user_id)
    if not user: fail(400,'Invalid or expired link')
    user.password_hash=ph.hash(payload.password)
    entry.used_at=NOW()
    # Invalidate all browser/device sessions on password reset.
    for session in db.scalars(select(SessionToken).where(SessionToken.user_id==user.id)).all():db.delete(session)
    db.commit()
    return {'ok':True,'message':'Password updated. Sign in using your new password.'}

@app.post('/api/auth/verify')
def verify_email(payload:TokenOnly,request:Request,db:Session=Depends(getdb)):
    rate_limit(request,'verify-attempt',20,3600)
    entry=consume_token(db,payload.token,'verify')
    user=db.get(User,entry.user_id)
    if not user: fail(400,'Invalid or expired link')
    user.email_verified=True;entry.used_at=NOW()
    db.commit()
    return {'ok':True}

@app.post('/api/auth/verify/resend')
def resend_verify(payload:EmailOnly,request:Request,db:Session=Depends(getdb)):
    email=normalize_email(payload.email)
    rate_limit(request,'verify-resend-ip',8,3600)
    rate_limit(request,'verify-resend-account',3,3600,email)
    user=db.scalar(select(User).where(User.email==email))
    if user and not user.email_verified:
        token=issue_token(db,user.id,'verify',60*24)
        mail_user(email,'Verify your Myco OS account',f'Verify your farm account: {PUBLIC_URL}/?verify={token}\n\nThis link expires in 24 hours.')
    return {'ok':True,'message':'If verification is needed, an email has been sent.'}

@app.post('/api/auth/password/change')
def change_password(payload:PasswordChange,request:Request,db:Session=Depends(getdb),au=Depends(auth)):
    rate_limit(request,'change-password',8,3600)
    user,active=au
    try: ph.verify(user.password_hash,payload.current_password)
    except (VerifyMismatchError,VerificationError):fail(403,'Current password is incorrect')
    strong_password(payload.new_password)
    user.password_hash=ph.hash(payload.new_password)
    for session in db.scalars(select(SessionToken).where(SessionToken.user_id==user.id,SessionToken.id!=active.id)).all():db.delete(session)
    db.commit()
    return {'ok':True,'message':'Password changed. Other devices signed out.'}

@app.get('/api/auth/me')
def me(db:Session=Depends(getdb),au=Depends(auth)):
    user,s=au
    farms=[]
    for farm, m in db.execute(select(Farm,Membership).join(Membership,Membership.farm_id==Farm.id).where(Membership.user_id==user.id)).all():
        farms.append({'id':farm.id,'name':farm.name,'plan':farm.plan,'role':m.role,'slug':farm.slug})
    # csrf is only stored hashed: issue a fresh CSRF token for this session
    csrf=secrets.token_urlsafe(32);s.csrf_hash=digest(csrf);db.commit()
    return {'user':{'id':user.id,'email':user.email,'name':user.display_name},'farms':farms,'csrf':csrf}

@app.post('/api/auth/logout')
def logout(response:Response,db:Session=Depends(getdb),au=Depends(auth)):
    db.delete(au[1]);db.commit();response.delete_cookie('myco_session',path='/')
    return {'ok':True}

@app.get('/api/farms/{fid}/summary')
def summary(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au)
    recs=db.scalars(select(Record).where(Record.farm_id==fid)).all()
    counts={k:0 for k in KINDS}
    for r in recs: counts[r.kind]+=1
    h=db.scalars(select(Harvest).where(Harvest.farm_id==fid)).all()
    stock=db.scalars(select(StockLot).where(StockLot.farm_id==fid)).all()
    orders=db.scalars(select(Order).where(Order.farm_id==fid)).all()
    current_week=NOW().date()-timedelta(days=NOW().weekday())
    week_lb=sum(v.weight_lb for v in h if v.harvested_at.date()>=current_week)
    return {'farm':{'id':fid,'name':farm.name,'plan':farm.plan,'billing_status':farm.billing_status,'role':m.role},'counts':counts,'harvest_total_lb':round(sum(v.weight_lb for v in h),2),'harvest_week_lb':round(week_lb,2),'stock_lb':round(sum(v.quantity_lb for v in stock),2),'revenue_cents':sum(o.total_cents for o in orders if o.status=='fulfilled'),'active_orders':sum(o.status=='draft' for o in orders),'members':db.scalar(select(func.count()).select_from(Membership).where(Membership.farm_id==fid)),'member_limit':None,'team_access_included':True}

@app.get('/api/farms/{fid}/records')
def records(fid:str,kind:str='',db:Session=Depends(getdb),au=Depends(auth)):
    context(fid,db,au)
    q=select(Record).where(Record.farm_id==fid)
    if kind: q=q.where(Record.kind==kind)
    return [serialize_record(r) for r in db.scalars(q.order_by(Record.created_at.desc())).all()]

@app.post('/api/farms/{fid}/records')
def create_record(fid:str,payload:RecordIn,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_write(m,payload.kind)
    data=record_input(payload.kind,payload.data)
    r=Record(id=ident(),farm_id=fid,kind=payload.kind,data=data,created_by=user.id,updated_by=user.id)
    db.add(r);add_audit(db,fid,user.id,'created',payload.kind,r.id)
    db.commit();return serialize_record(r)

@app.put('/api/farms/{fid}/records/{rid}')
def edit_record(fid:str,rid:str,payload:RecordEdit,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au)
    r=db.scalar(select(Record).where(Record.farm_id==fid,Record.id==rid))
    if not r: fail(404,'Record not found')
    require_write(m,r.kind)
    if r.revision!=payload.revision: fail(409,'Another team member has updated this record. Refresh and review their changes before saving.')
    r.data=record_input(r.kind,payload.data);r.revision+=1;r.updated_by=user.id;r.updated_at=NOW()
    add_audit(db,fid,user.id,'updated',r.kind,r.id)
    db.commit();return serialize_record(r)

@app.delete('/api/farms/{fid}/records/{rid}')
def delete_record(fid:str,rid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);r=db.scalar(select(Record).where(Record.farm_id==fid,Record.id==rid))
    if not r: fail(404,'Record not found')
    require_role(m,{'owner','manager'})
    if r.kind=='batch' and db.scalar(select(func.count()).select_from(Harvest).where(Harvest.farm_id==fid,Harvest.batch_id==rid)):
        fail(409,'This batch has harvest history. Archive it instead of deleting it.')
    add_audit(db,fid,user.id,'deleted',r.kind,r.id)
    db.delete(r);db.commit();return {'ok':True}

@app.get('/api/farms/{fid}/harvests')
def harvests(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    context(fid,db,au)
    return [{'id':h.id,'batch_id':h.batch_id,'species':h.species,'weight_lb':h.weight_lb,'flush':h.flush,'harvested_at':stamp(h.harvested_at),'note':h.note,'lot_id':h.lot_id} for h in db.scalars(select(Harvest).where(Harvest.farm_id==fid).order_by(Harvest.harvested_at.desc())).all()]

@app.post('/api/farms/{fid}/harvests')
def create_harvest(fid:str,payload:HarvestIn,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au)
    require_write(m,'batch')
    b=db.scalar(select(Record).where(Record.id==payload.batch_id,Record.farm_id==fid,Record.kind=='batch'))
    if not b: fail(404,'Batch not found in your farm')
    species=json.loads(b.data).get('species','Unknown')
    lot=StockLot(id=ident(),farm_id=fid,species=species,quantity_lb=payload.weight_lb)
    h=Harvest(id=ident(),farm_id=fid,batch_id=b.id,species=species,weight_lb=payload.weight_lb,flush=payload.flush,note=payload.note,created_by=user.id,lot_id=lot.id)
    lot.source_harvest_id=h.id
    db.add_all([h,lot,StockMovement(farm_id=fid,lot_id=lot.id,delta_lb=payload.weight_lb,reason='harvest',reference_id=h.id,created_by=user.id)])
    add_audit(db,fid,user.id,'harvested','batch',b.id,f'{payload.weight_lb} lb; flush {payload.flush}')
    db.commit();return {'id':h.id,'lot_id':lot.id,'species':species,'weight_lb':h.weight_lb}

@app.get('/api/farms/{fid}/stock')
def stock(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    context(fid,db,au)
    return [{'id':s.id,'species':s.species,'quantity_lb':round(s.quantity_lb,3),'harvested_at':stamp(s.harvested_at),'source_harvest_id':s.source_harvest_id} for s in db.scalars(select(StockLot).where(StockLot.farm_id==fid).order_by(StockLot.harvested_at.desc())).all()]

@app.get('/api/farms/{fid}/orders')
def orders(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    context(fid,db,au)
    return [{'id':o.id,'customer':o.customer,'lines':json.loads(o.items_json),'status':o.status,'total_cents':o.total_cents,'created_at':stamp(o.created_at)} for o in db.scalars(select(Order).where(Order.farm_id==fid).order_by(Order.created_at.desc())).all()]

@app.post('/api/farms/{fid}/orders')
def create_order(fid:str,payload:OrderIn,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au)
    if m.role not in ('owner','manager','sales'):fail(403,'Sales permission required')
    for line in payload.lines:
        lot=db.scalar(select(StockLot).where(StockLot.farm_id==fid,StockLot.id==line.lot_id))
        if not lot:fail(400,'A selected lot does not belong to your farm')
    lines=[v.model_dump() for v in payload.lines]
    total=sum(round(v.quantity_lb*v.unit_price_cents) for v in payload.lines)
    o=Order(id=ident(),farm_id=fid,customer=payload.customer.strip(),items_json=json.dumps(lines),total_cents=total,created_by=user.id)
    db.add(o);add_audit(db,fid,user.id,'created','order',o.id)
    db.commit();return {'id':o.id,'total_cents':o.total_cents,'status':o.status}

@app.post('/api/farms/{fid}/orders/{oid}/fulfill')
def fulfill(fid:str,oid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au)
    if m.role not in ('owner','manager','sales'):fail(403,'Sales permission required')
    order=db.scalar(select(Order).where(Order.id==oid,Order.farm_id==fid).with_for_update())
    if not order:fail(404,'Order not found')
    if order.status=='fulfilled':fail(409,'Already fulfilled')
    lines=json.loads(order.items_json)
    totals={}
    for line in lines:totals[line['lot_id']]=totals.get(line['lot_id'],0)+line['quantity_lb']
    # Lock lots on PostgreSQL and perform atomic decrements. Recheck after concurrent edits.
    lots={v.id:v for v in db.scalars(select(StockLot).where(StockLot.farm_id==fid,StockLot.id.in_(list(totals))).with_for_update()).all()}
    for lid,qty in totals.items():
        if lid not in lots or lots[lid].quantity_lb+1e-8<qty: fail(409,'Insufficient available inventory. Refresh stock and adjust order.')
    for lid,qty in totals.items():
        lots[lid].quantity_lb=round(lots[lid].quantity_lb-qty,4)
        db.add(StockMovement(farm_id=fid,lot_id=lid,delta_lb=-qty,reason='sale',reference_id=oid,created_by=user.id))
    order.status='fulfilled';order.fulfilled_at=NOW()
    add_audit(db,fid,user.id,'fulfilled','order',oid)
    db.commit();return {'ok':True,'status':'fulfilled'}

@app.get('/api/farms/{fid}/members')
def members(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au)
    rows=db.execute(select(User,Membership).join(Membership,Membership.user_id==User.id).where(Membership.farm_id==fid)).all()
    return [{'id':u.id,'name':u.display_name,'email':u.email,'role':member.role} for u,member in rows]

@app.post('/api/farms/{fid}/invites')
def invite(fid:str,payload:InviteIn,request:Request,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner','manager'})
    if payload.role not in {'manager','technician','sales','viewer'}:fail(400,'Invalid role')
    email=normalize_email(payload.email)
    rate_limit(request,'invite-ip',50,3600)
    rate_limit(request,'invite-farm',60,3600,fid)
    token=secrets.token_urlsafe(32)
    db.add(Invite(farm_id=fid,email=email,role=payload.role,token_hash=digest(token),expires_at=NOW()+timedelta(days=7)))
    add_audit(db,fid,user.id,'invited','member',fid,email)
    db.commit()
    base=os.getenv('PUBLIC_APP_URL','http://localhost:8000').rstrip('/')
    return {'invite_url':f'{base}/?invite={token}','expires_in_days':7,'note':'Copy and send this private link. Email delivery is not configured.'}

@app.post('/api/invites/accept')
def accept_invite(payload:AcceptIn,db:Session=Depends(getdb),au=Depends(auth)):
    user,_=au
    inv=db.scalar(select(Invite).where(Invite.token_hash==digest(payload.token),Invite.accepted_at.is_(None)))
    if not inv or inv.expires_at.replace(tzinfo=timezone.utc)<NOW():fail(404,'Invite not found or expired')
    if user.email!=inv.email:fail(403,'Sign in using the email address that was invited')
    farm=db.get(Farm,inv.farm_id)
    if db.scalar(select(Membership).where(Membership.farm_id==farm.id,Membership.user_id==user.id)): fail(409,'Already a member')
    db.add(Membership(farm_id=farm.id,user_id=user.id,role=inv.role));inv.accepted_at=NOW()
    add_audit(db,farm.id,user.id,'joined','member',user.id)
    db.commit();return {'ok':True,'farm_id':farm.id}

@app.post('/api/farms')
def create_farm(payload:NewFarm,request:Request,db:Session=Depends(getdb),au=Depends(auth)):
    rate_limit(request,'create-farm',10,3600)
    user,_=au
    farm=Farm(id=ident(),name=payload.name.strip(),slug=slugify(payload.name)+'-'+secrets.token_hex(3),owner_id=user.id)
    db.add_all([farm,Membership(farm_id=farm.id,user_id=user.id,role='owner')]);db.commit()
    return {'id':farm.id,'name':farm.name,'plan':farm.plan}

@app.patch('/api/farms/{fid}/members/{uid}')
def update_member(fid:str,uid:str,payload:MemberRoleChange,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner'})
    if uid==farm.owner_id:fail(400,'Farm owner role cannot be changed')
    if payload.role not in ('manager','technician','sales','viewer'):fail(400,'Invalid role')
    member=db.scalar(select(Membership).where(Membership.farm_id==fid,Membership.user_id==uid))
    if not member:fail(404,'Member not found')
    member.role=payload.role;add_audit(db,fid,user.id,'changed role','member',uid,payload.role);db.commit()
    return {'ok':True,'role':member.role}

@app.delete('/api/farms/{fid}/members/{uid}')
def remove_member(fid:str,uid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner'})
    if uid==farm.owner_id:fail(400,'Owner cannot be removed')
    member=db.scalar(select(Membership).where(Membership.farm_id==fid,Membership.user_id==uid))
    if not member:fail(404,'Member not found')
    db.delete(member)
    add_audit(db,fid,user.id,'removed','member',uid)
    db.commit();return {'ok':True}

@app.get('/api/farms/{fid}/reports/performance.csv')
def premium_report(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner','manager'})
    if farm.plan not in ('pro','growth'):fail(403,'Advanced report downloads are part of the Pro and Growth plans')
    output=io.StringIO();w=csv.writer(output)
    # Spreadsheet exports must not execute attacker-controlled formula cells.
    safe_cell=lambda x: '\''+str(x) if str(x).lstrip().startswith(('=','+','-','@','\t','\r')) else str(x)
    w.writerow(['Myco OS · Hill City Mushrooms · Premium farm report'])
    w.writerow(['Farm',safe_cell(farm.name),'Generated',stamp(NOW())])
    w.writerow([]);w.writerow(['Species','Total harvested (lb)','Harvest events','Current stock (lb)','Fulfilled revenue (USD)'])
    agg={}
    for h in db.scalars(select(Harvest).where(Harvest.farm_id==fid)):
        item=agg.setdefault(h.species,{'harvest':0,'events':0,'stock':0,'revenue':0})
        item['harvest']+=h.weight_lb;item['events']+=1
    for lot in db.scalars(select(StockLot).where(StockLot.farm_id==fid)):
        agg.setdefault(lot.species,{'harvest':0,'events':0,'stock':0,'revenue':0})['stock']+=lot.quantity_lb
    for order in db.scalars(select(Order).where(Order.farm_id==fid,Order.status=='fulfilled')):
        for line in json.loads(order.items_json):
            lot=db.get(StockLot,line['lot_id'])
            if lot and lot.farm_id==fid:
                agg.setdefault(lot.species,{'harvest':0,'events':0,'stock':0,'revenue':0})['revenue']+=round(line['quantity_lb']*line['unit_price_cents'])
    for species, vals in sorted(agg.items()):w.writerow([safe_cell(species),round(vals['harvest'],2),vals['events'],round(vals['stock'],2),f"{vals['revenue']/100:.2f}"])
    return Response(content=output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="myco-performance.csv"','Cache-Control':'no-store'})

@app.get('/api/farms/{fid}/audit')
def audit(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner','manager'})
    return [{'action':a.action,'entity_type':a.entity_type,'detail':a.detail,'time':stamp(a.created_at),'actor':a.actor_id} for a in db.scalars(select(Audit).where(Audit.farm_id==fid).order_by(Audit.created_at.desc()).limit(100)).all()]

@app.get('/api/farms/{fid}/export')
def export(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner','manager'})
    return {'format':'myco-os-cloud','version':1,'exported_at':stamp(NOW()),'farm':{'name':farm.name,'id':farm.id},
            'records':[serialize_record(r) for r in db.scalars(select(Record).where(Record.farm_id==fid)).all()],
            'harvests':harvests(fid,db,au),'stock':stock(fid,db,au),'orders':orders(fid,db,au)}

@app.post('/api/farms/{fid}/import-legacy')
async def import_legacy(fid:str,request:Request,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner'})
    if int(request.headers.get('content-length','0'))>3000000:fail(413,'Import file exceeds 3 MB')
    payload=await request.json()
    if not isinstance(payload,dict) or 'meta' not in payload:fail(400,'Not a recognizable Myco OS standalone backup')
    mapper={'batches':'batch','cultures':'culture','recipes':'recipe','runs':'processing','rooms':'room','equipment':'equipment','tasks':'task','customers':'customer','expenses':'expense','readings':'reading','experiments':'experiment','incidents':'incident','plans':'plan','sops':'sop'}
    count=0
    # Import only standalone metadata records; avoid fabricating stock movements, financial sales or harvests.
    for arr,kind in mapper.items():
        items=payload.get(arr,[])
        if not isinstance(items,list) or len(items)>5000:fail(400,'Invalid import collection')
        for item in items:
            if not isinstance(item,dict):continue
            data=dict(item);data.pop('id',None)
            if kind=='batch':
                data['name']=str(data.get('name') or 'Imported batch')
                data['species']=str(data.get('species') or 'Unknown')
                data['blocks']=max(1,int(float(data.get('blocks') or 1)))
            if kind=='task':data['name']=str(data.get('name') or 'Imported task')
            try:encoded=record_input(kind,data)
            except (ValueError,TypeError,HTTPException):continue
            db.add(Record(farm_id=fid,kind=kind,data=encoded,created_by=user.id,updated_by=user.id));count+=1
    add_audit(db,fid,user.id,'imported','records',fid,f'{count} legacy records (not harvest, orders, inventory)')
    db.commit();return {'imported':count,'excluded':'Legacy harvests, order fulfillment, and inventory excluded to prevent invalid financial/stock balances'}

@app.post('/api/farms/{fid}/billing/checkout')
def billing_checkout(fid:str,payload:dict,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner'})
    plan=payload.get('plan')
    if plan not in ('pro','growth'):fail(400,'Unknown subscription plan')
    price=os.getenv(f'STRIPE_PRICE_{plan.upper()}')
    key=os.getenv('STRIPE_SECRET_KEY')
    if not price or not key:fail(503,'Billing is not configured on this deployment yet')
    import stripe
    stripe.api_key=key
    base=os.getenv('PUBLIC_APP_URL','http://localhost:8000').rstrip('/')
    try:
        session=stripe.checkout.Session.create(mode='subscription',customer_email=None if farm.stripe_customer_id else user.email,
            customer=farm.stripe_customer_id or None,line_items=[{'price':price,'quantity':1}],
            metadata={'farm_id':fid,'plan':plan},subscription_data={'metadata':{'farm_id':fid,'plan':plan}},
            success_url=base+'/?billing=success',cancel_url=base+'/?billing=cancel')
    except Exception:fail(502,'Stripe checkout unavailable')
    return {'url':session.url}

@app.post('/api/farms/{fid}/billing/portal')
def billing_portal(fid:str,db:Session=Depends(getdb),au=Depends(auth)):
    farm,m,user=context(fid,db,au);require_role(m,{'owner'})
    if not farm.stripe_customer_id or not os.getenv('STRIPE_SECRET_KEY'):fail(400,'No active billing account')
    import stripe
    stripe.api_key=os.environ['STRIPE_SECRET_KEY']
    base=os.getenv('PUBLIC_APP_URL','http://localhost:8000').rstrip('/')
    try:s=stripe.billing_portal.Session.create(customer=farm.stripe_customer_id,return_url=base)
    except Exception:fail(502,'Billing portal unavailable')
    return {'url':s.url}

@app.post('/api/billing/webhook')
async def billing_webhook(request:Request,db:Session=Depends(getdb)):
    secret=os.getenv('STRIPE_WEBHOOK_SECRET');key=os.getenv('STRIPE_SECRET_KEY')
    if not secret or not key:fail(503,'Billing is not configured')
    import stripe
    stripe.api_key=key
    body=await request.body();signature=request.headers.get('stripe-signature','')
    try:event=stripe.Webhook.construct_event(body,signature,secret)
    except Exception:fail(400,'Invalid webhook signature')
    if db.get(StripeEvent,event['id']):return {'ok':True}
    obj=event['data']['object'];kind=event['type']
    if kind=='checkout.session.completed' and obj.get('mode')=='subscription':
        fid=(obj.get('metadata') or {}).get('farm_id')
        farm=db.get(Farm,fid) if fid else None
        if farm:
            farm.stripe_customer_id=obj.get('customer')
            farm.stripe_subscription_id=obj.get('subscription')
            # Only authenticated subscription lifecycle webhook determines entitlement.
            # checkout completion alone never activates a paid plan.
    elif kind in ('customer.subscription.created','customer.subscription.updated','customer.subscription.deleted'):
        meta=obj.get('metadata') or {}
        fid=meta.get('farm_id')
        farm=db.get(Farm,fid) if fid else None
        if farm and (not farm.stripe_subscription_id or farm.stripe_subscription_id==obj.get('id')):
            farm.stripe_subscription_id=obj['id'];farm.stripe_customer_id=obj.get('customer')
            active=obj.get('status') in ('active','trialing') and kind!='customer.subscription.deleted'
            farm.billing_status=obj.get('status','inactive')
            price_id=None
            items=(obj.get('items') or {}).get('data',[])
            if items:price_id=(items[0].get('price') or {}).get('id')
            if active and price_id==os.getenv('STRIPE_PRICE_GROWTH'):farm.plan='growth'
            elif active and price_id==os.getenv('STRIPE_PRICE_PRO'):farm.plan='pro'
            else:farm.plan='free'
    db.add(StripeEvent(id=event['id']));db.commit()
    return {'ok':True}

app.mount('/assets',StaticFiles(directory=BASE/'static'),name='assets')
@app.get('/')
def frontend():return FileResponse(BASE/'static'/'index.html')

"""Individual identities. Tokens are opaque, short-lived and stored only as hashes."""
import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid
from .core import DomainError

PERMISSIONS={
 'admin':{'read','write','costs','estimate','team','settings'},
 'coordinator':{'read','write','costs','estimate'},
 'estimator':{'read','costs','estimate'},
 'foreman':{'read','write'},
 'worker':{'read'},
}

def owner():
    return {'id':'local-owner','username':'local-owner','name':'Propietario','role':'admin','projects':[],'active':True}

def require(user,action,project_id=None):
    if not user or not user.get('active') or action not in PERMISSIONS.get(user.get('role'),set()):
        raise DomainError('Permiso insuficiente')
    if user['role']!='admin' and (project_id is None or project_id not in user.get('projects',[])):
        raise DomainError('Proyecto no autorizado')
    return user

def _password(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32).hex()

def _validate_password(password):
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise DomainError('La contraseña requiere entre 12 y 256 caracteres')

def _public(row):
    result=dict(row)
    for key in ('password_hash','salt'):result.pop(key,None)
    result['projects']=json.loads(result['projects']);result['active']=bool(result['active'])
    return result

class Auth:
    def __init__(self,store):
        self.store=store
        with store.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS erp_users(
                id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL,
                projects TEXT NOT NULL,active INTEGER NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS erp_sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS erp_login_limits(username TEXT PRIMARY KEY,failures INTEGER NOT NULL,last_at INTEGER NOT NULL);''')
    def create_user(self,actor,username,password,role,name='',projects=None):
        require(actor,'team');_validate_password(password)
        username=username.strip().lower()
        if not re.fullmatch(r'[a-z0-9_.@-]{3,80}',username) or role not in PERMISSIONS:raise DomainError('Usuario o rol inválido')
        projects=self._projects(projects or [])
        uid=uuid.uuid4().hex;salt=secrets.token_hex(16)
        try:
            with self.store.connect() as c:c.execute('INSERT INTO erp_users VALUES(?,?,?,?,?,1,?,?)',(uid,username,str(name)[:150],role,json.dumps(projects),salt,_password(password,salt)))
        except sqlite3.IntegrityError:raise DomainError('El usuario ya existe') from None
        return self.get_user(uid)
    @staticmethod
    def _projects(projects):
        if not isinstance(projects,list) or any(not isinstance(p,str) or not p or len(p)>150 for p in projects):raise DomainError('Proyectos inválidos')
        return list(dict.fromkeys(projects))
    def get_user(self,uid):
        with self.store.connect() as c:row=c.execute('SELECT * FROM erp_users WHERE id=?',(uid,)).fetchone()
        if not row:raise DomainError('Usuario no encontrado')
        return _public(row)
    def list_users(self,actor):
        require(actor,'team')
        with self.store.connect() as c:return [_public(r) for r in c.execute('SELECT * FROM erp_users ORDER BY username')]
    def update_user(self,actor,uid,**fields):
        require(actor,'team');user=self.get_user(uid)
        if set(fields)-{'role','name','projects','active','password'}:raise DomainError('Campo de usuario inválido')
        user.update({k:v for k,v in fields.items() if k!='password'})
        if user['role'] not in PERMISSIONS or type(user['active']) is not bool:raise DomainError('Rol o estado inválido')
        projects=self._projects(user['projects'])
        if 'password' in fields:_validate_password(fields['password'])
        with self.store.connect() as c:
            c.execute('UPDATE erp_users SET name=?,role=?,projects=?,active=? WHERE id=?',(str(user['name'])[:150],user['role'],json.dumps(projects),int(user['active']),uid))
            if 'password' in fields:
                salt=secrets.token_hex(16);c.execute('UPDATE erp_users SET salt=?,password_hash=? WHERE id=?',(salt,_password(fields['password'],salt),uid))
            c.execute('DELETE FROM erp_sessions WHERE user_id=?',(uid,))
        return self.get_user(uid)
    def login(self,username,password):
        username=str(username).strip().lower()[:80];now=int(time.time())
        with self.store.connect() as c:
            limit=c.execute('SELECT * FROM erp_login_limits WHERE username=?',(username,)).fetchone()
            if limit and limit['failures']>=5 and now-limit['last_at']<300:raise DomainError('Demasiados intentos. Espere cinco minutos')
            row=c.execute('SELECT * FROM erp_users WHERE username=?',(username,)).fetchone()
        salt=row['salt'] if row else '0'*32
        valid=isinstance(password,str) and len(password)<=256 and secrets.compare_digest(_password(password,salt),row['password_hash'] if row else '0'*64)
        if not valid or not row or not row['active']:
            with self.store.connect() as c:c.execute('INSERT INTO erp_login_limits VALUES(?,1,?) ON CONFLICT(username) DO UPDATE SET failures=CASE WHEN ?-last_at>=300 THEN 1 ELSE failures+1 END,last_at=?',(username,now,now,now))
            raise DomainError('Credenciales inválidas')
        token=secrets.token_urlsafe(48);expires=now+8*3600
        with self.store.connect() as c:
            c.execute('DELETE FROM erp_login_limits WHERE username=?',(username,));c.execute('DELETE FROM erp_sessions WHERE expires_at<=?',(now,))
            c.execute('INSERT INTO erp_sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],expires))
        return {'token':token,'user':_public(row),'expires_at':expires}
    def ensure_email_user(self,email,role='worker'):
        """Usuario identificado por correo (acceso con código). Sin contraseña utilizable. Rol solo se fija al crearlo."""
        if role not in PERMISSIONS:raise DomainError('Rol inválido')
        with self.store.connect() as c:row=c.execute('SELECT * FROM erp_users WHERE username=?',(email,)).fetchone()
        if row:
            user=_public(row)
            if role=='admin' and user['role']!='admin':  # el correo del administrador siempre entra como administrador
                with self.store.connect() as c:c.execute("UPDATE erp_users SET role='admin' WHERE id=?",(user['id'],))
                user=self.get_user(user['id'])
            return user
        uid=uuid.uuid4().hex;salt=secrets.token_hex(16)
        with self.store.connect() as c:c.execute('INSERT INTO erp_users VALUES(?,?,?,?,?,1,?,?)',(uid,email,email.split('@')[0][:150],role,'[]',salt,secrets.token_hex(32)))
        return self.get_user(uid)
    def issue_session(self,uid,hours=8):
        token=secrets.token_urlsafe(48);now=int(time.time());expires=now+hours*3600
        with self.store.connect() as c:
            c.execute('DELETE FROM erp_sessions WHERE expires_at<=?',(now,))
            c.execute('INSERT INTO erp_sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,expires))
        return {'token':token,'user':self.get_user(uid),'expires_at':expires}
    def authenticate(self,token):
        if not isinstance(token,str) or len(token)>256:raise DomainError('Sesión inválida')
        with self.store.connect() as c:row=c.execute('SELECT u.* FROM erp_users u JOIN erp_sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        if not row:raise DomainError('Sesión inválida o vencida')
        return _public(row)
    def logout(self,token):
        with self.store.connect() as c:c.execute('DELETE FROM erp_sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

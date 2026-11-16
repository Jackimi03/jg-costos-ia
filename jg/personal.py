"""Loopback-only personal panel. Access remotely through an SSH tunnel only."""
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Request,HTTPException
from fastapi.responses import HTMLResponse,JSONResponse,Response
from pydantic import BaseModel,Field
from .api import create_app
from .workspace import setup,OWNER,create_project,projects,sheet_link
from . import assistant
from .lifecycle import trash_project,restore_project,list_trash,prepare_undo

class NewProject(BaseModel):
    name:str=Field(min_length=1,max_length=150)
class AiRequest(BaseModel):
    prompt:str=Field(min_length=1,max_length=6000)
class AiResult(BaseModel):
    plan:dict
class AiFailure(BaseModel):
    message:str=Field(max_length=1000)

def create_personal_app(path):
    store=setup(path);token=secrets.token_urlsafe(48)
    def verify(value):
        if not secrets.compare_digest(value,token):raise ValueError('Sesión inválida')
        return OWNER
    app=create_app(store,verify)
    @app.middleware('http')
    async def local_only(request:Request,call_next):
        if os.getenv('JG_TEAM_MODE') and not (request.url.path.startswith('/erp/') or request.url.path in ('/','/erp.js','/erp.css','/health')):
            return JSONResponse({'detail':'Inicio de sesión individual requerido'},status_code=403)
        host=request.url.hostname
        if host not in ('127.0.0.1','localhost','::1'):
            # Acceso remoto (red privada Tailscale): solo hosts autorizados y solo el ERP con sesión por correo.
            remote={h.strip().lower() for h in os.getenv('JG_ALLOWED_HOSTS','').split(',') if h.strip()}
            path=request.url.path
            if (host or '').lower() not in remote:
                return JSONResponse({'detail':'Solo acceso local o túnel SSH'},status_code=403)
            if not (path in ('/','/erp.js','/erp.css','/health') or path.startswith('/erp/')):
                return JSONResponse({'detail':'Acceso remoto: entre con su correo'},status_code=403)
            if request.headers.get('authorization','')=='Bearer '+token:
                return JSONResponse({'detail':'Sesión local no válida en acceso remoto'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Origen no autorizado'},status_code=403)
        result=await call_next(request)
        result.headers['Cache-Control']='no-store'
        result.headers['X-Content-Type-Options']='nosniff'
        result.headers['X-Frame-Options']='SAMEORIGIN'
        result.headers['Referrer-Policy']='no-referrer'
        return result
    def authorized(request):
        if request.headers.get('authorization')!='Bearer '+token:raise HTTPException(401,'Sesión requerida')
    @app.get('/',response_class=HTMLResponse)
    def home():return (Path(__file__).parent/'erp.html').read_text()
    @app.get('/legacy',response_class=HTMLResponse)
    def legacy():return (Path(__file__).parent/'web.html').read_text()
    @app.get('/erp.js')
    def erp_js():return Response((Path(__file__).parent/'erp.js').read_text(),media_type='text/javascript')
    @app.get('/erp.css')
    def erp_css():return Response((Path(__file__).parent/'erp.css').read_text(),media_type='text/css')
    @app.get('/math.js')
    def math_script():return Response((Path(__file__).parent/'web_math.js').read_text(),media_type='text/javascript')
    @app.get('/session')
    def session():return {'token':token,'mode':'personal-local'}
    @app.get('/workspace')
    def workspace(request:Request):
        authorized(request)
        return {'projects':projects(store),'trash':list_trash(store),'sheet_url':sheet_link(store),'owner':'Propietario','mode':'Cuenta personal · acceso privado'}
    @app.post('/workspace/projects')
    def new_project(body:NewProject,request:Request):
        authorized(request);return create_project(store,body.name)
    @app.post('/workspace/projects/{project}/trash')
    def trash(project:str,request:Request):
        authorized(request);return trash_project(store,project)
    @app.post('/workspace/projects/{project}/restore')
    def restore(project:str,request:Request):
        authorized(request);return restore_project(store,project)
    @app.post('/projects/{project}/undo-preview')
    def undo(project:str,request:Request):
        authorized(request);return prepare_undo(store,project)
    @app.get('/ai/status')
    def ai_connection(request:Request):
        authorized(request);return assistant.ai_status(store)
    @app.post('/projects/{project}/ai')
    def ask_ai(project:str,body:AiRequest,request:Request):
        authorized(request);return assistant.enqueue(store,project,body.prompt)
    @app.get('/projects/{project}/ai-result')
    def latest_ai(project:str,request:Request):
        authorized(request);return assistant.latest_result(store,project)
    @app.get('/projects/{project}/ai-active')
    def current_ai(project:str,request:Request):
        authorized(request);return assistant.active_job(store,project)
    @app.get('/ai/jobs/{key}')
    def job(key:str,request:Request):
        authorized(request);return assistant.get_job(store,key)
    @app.post('/ai/jobs/{key}/cancel')
    def stop_job(key:str,request:Request):
        authorized(request);return assistant.cancel(store,key)
    @app.post('/ai/worker/heartbeat')
    def worker_heartbeat(request:Request):
        authorized(request);assistant.heartbeat(store);return {'ok':True}
    @app.post('/ai/worker/claim')
    def worker_claim(request:Request):
        authorized(request);return assistant.claim(store)
    @app.post('/ai/worker/{key}/finish')
    def worker_finish(key:str,body:AiResult,request:Request):
        authorized(request);return assistant.finish(store,key,body.plan)
    @app.post('/ai/worker/{key}/fail')
    def worker_fail(key:str,body:AiFailure,request:Request):
        authorized(request);assistant.fail(store,key,body.message);return {'ok':True}
    @app.get('/projects/{project}/history')
    def history(project:str,request:Request):
        authorized(request)
        with store.connect() as c:
            store.permission(c,OWNER,project)
            return [dict(r) for r in c.execute('SELECT at,action FROM audit WHERE project=? ORDER BY id DESC LIMIT 50',(project,)).fetchall()]
    from .erp_routes import install
    install(app,store,token)
    return app

def app():
    return create_personal_app(os.getenv('JG_DATABASE',str(Path(__file__).parent.parent/'data/personal.db')))

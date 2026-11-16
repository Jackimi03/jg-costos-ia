"""ERP HTTP adapter; every operation enforces identity and scope."""
import json,os,sqlite3,time,uuid
from pathlib import Path
from fastapi import Request,HTTPException,UploadFile,File,Form
from fastapi.responses import FileResponse,Response
from .erp import ERP
from .erp_auth import Auth,owner,require
from .erp_files import DocumentStore,FILE_KINDS
from .erp_access import Access
ATTACHABLE={'clients','contracts','works','evidence','incidents','logs','expenses','materials','changes'}
from . import erp_integrations
from .core import DomainError

COST_KINDS={'quotes','documents','contracts','expenses','income','catalog','timesheets','materials','workers','changes'}
GLOBAL_KINDS={'clients','contacts','catalog','workers'}

def install(app,store,owner_token):
 erp=ERP(store);auth=Auth(store);files=DocumentStore(store);erp_integrations.configure(Path(store.path).parent/'secrets');access=Access(store,Path(store.path).parent/'secrets',auth)
 def render_quote(doc_or_quote,watermark=None):
  try:from .erp_pdf import quote_pdf
  except ImportError:raise DomainError('Instale reportlab para exportar PDF') from None
  if doc_or_quote.get('snapshot'):  # documento emitido: solo instantáneas guardadas
   d=doc_or_quote;client=d.get('client_snapshot') or erp.get('clients',d['client_id']);company=d.get('company_snapshot') or erp.company_profile()
   return quote_pdf(d['snapshot'],client,company,d['number'],d.get('issued_at'),d.get('work_name',''),watermark)
  q=doc_or_quote;work=erp.get('works',q['work_id'])['name'] if q.get('work_id') else ''
  return quote_pdf(q,erp.get('clients',q['client_id']),erp.company_profile(),None,None,work,watermark)
 def stored_quote_pdf(u,doc):
  """PDF inmutable del documento emitido: se genera una vez y se reutiliza."""
  existing=[f for f in files.list(u,record_id=doc['id']) if f['kind']=='estimate']
  if existing:return existing[-1]
  return files.save(u,doc['number']+'.pdf',render_quote(doc),project_id=doc.get('work_id'),kind='estimate',snapshot={'document_id':doc['id'],'number':doc['number'],'total':doc['total']},record_kind='documents',record_id=doc['id'])
 def identity(request):
  token=request.headers.get('authorization','').removeprefix('Bearer ')
  if not os.getenv('JG_TEAM_MODE') and token==owner_token:return owner()
  return auth.authenticate(token)
 def allowed(user,kind,record=None,write=False):
  if user['role']=='admin':return
  if kind in ('settings','users'):require(user,'settings');return
  action='estimate' if write and kind=='quotes' else 'write' if write else 'costs' if kind in COST_KINDS else 'read'
  scope=(record or {}).get('work_id') or ((record or {}).get('id') if kind=='works' else None)
  if kind in GLOBAL_KINDS or (kind=='quotes' and not scope):
   if user['role'] not in ('coordinator','estimator'):raise DomainError('Permiso insuficiente')
   if write and kind!='quotes' and user['role']!='coordinator':raise DomainError('Permiso insuficiente')
   return
  require(user,action,scope)
 def visible(user,kind,records):
  result=[]
  for r in records:
   try:allowed(user,kind,r);result.append(r)
   except DomainError:pass
  return result
 @app.get('/erp/auth/me')
 def me(request:Request):return identity(request)
 @app.post('/erp/auth/login')
 def login(body:dict):return auth.login(body.get('username',''),body.get('password',''))
 @app.post('/erp/auth/request')
 def access_request(body:dict,request:Request):return access.request(body.get('email'),request.client.host if request.client else '')
 @app.post('/erp/auth/verify')
 def access_verify(body:dict,request:Request):return access.verify(body.get('email'),body.get('code'))
 @app.get('/erp/mail/config')
 def mail_get(request:Request):require(identity(request),'settings');return access.mail_status()
 @app.put('/erp/mail/config')
 def mail_put(body:dict,request:Request):require(identity(request),'settings');return access.save_mail(body.get('admin_email'),body.get('sender'),body.get('app_password') or None,body.get('sender_name'))
 @app.post('/erp/mail/test')
 def mail_test(request:Request):require(identity(request),'settings');return access.test_mail()
 @app.get('/erp/installer/config')
 def installer_get(request:Request):require(identity(request),'settings');return access.tailscale_status()
 @app.put('/erp/installer/config')
 def installer_put(body:dict,request:Request):require(identity(request),'settings');return access.save_tailscale(body.get('auth_key') or None,body.get('server_address'),body.get('port'))
 @app.get('/erp/installer/download/{system}')
 def installer_download(system:str,request:Request):
  require(identity(request),'settings');name,text,media=access.installer(system)
  return Response(text.encode('utf-8'),media_type=media,headers={'Content-Disposition':f'attachment; filename="{name}"'})
 @app.post('/erp/auth/logout')
 def logout(request:Request):auth.logout(request.headers.get('authorization','').removeprefix('Bearer '));return {'ok':True}
 @app.get('/erp/users')
 def users(request:Request):return auth.list_users(identity(request))
 @app.post('/erp/users')
 def add_user(body:dict,request:Request):return auth.create_user(identity(request),**body)
 @app.put('/erp/users/{uid}')
 def edit_user(uid:str,body:dict,request:Request):return auth.update_user(identity(request),uid,**body)
 @app.get('/erp/records/{kind}')
 def records(kind:str,request:Request):return visible(identity(request),kind,erp.list(kind))
 @app.get('/erp/records/{kind}/{rid}')
 def record(kind:str,rid:str,request:Request):
  r=erp.get(kind,rid);allowed(identity(request),kind,r);return r
 @app.post('/erp/records/{kind}')
 def create(kind:str,body:dict,request:Request):
  u=identity(request);allowed(u,kind,body,True);return erp.save(kind,body,actor=u['id'])
 @app.put('/erp/records/{kind}/{rid}')
 def update(kind:str,rid:str,body:dict,request:Request):
  u=identity(request);old=erp.get(kind,rid);allowed(u,kind,old,True);allowed(u,kind,{**old,**body},True);return erp.save(kind,body,rid,body.get('version'),u['id'])
 @app.delete('/erp/records/{kind}/{rid}')
 def delete(kind:str,rid:str,request:Request,version:int):
  u=identity(request);allowed(u,kind,erp.get(kind,rid),True);return erp.delete(kind,rid,version,u['id'])
 @app.get('/erp/history/{kind}/{rid}')
 def history(kind:str,rid:str,request:Request):allowed(identity(request),kind,erp.get(kind,rid));return erp.history(kind,rid)
 @app.get('/erp/quotes/defaults')
 def quote_defaults(request:Request):allowed(identity(request),'quotes',{},True);return erp.pricing_defaults()
 @app.post('/erp/quotes/preview')
 def preview(body:dict,request:Request):
  allowed(identity(request),'quotes',body,True)
  client=erp.get('clients',body['client_id']) if body.get('client_id') else None
  return erp.quote_preview(body,client,erp.pricing_defaults()['margin_rule'] if body.get('margin_mode')=='auto' else None)
 @app.post('/erp/quotes')
 def quote_create(body:dict,request:Request):
  u=identity(request);allowed(u,'quotes',body,True);return erp.quote_save(body,actor=u['id'])
 @app.put('/erp/quotes/{rid}')
 def quote_update(rid:str,body:dict,request:Request):
  u=identity(request);old=erp.get('quotes',rid);allowed(u,'quotes',old,True);allowed(u,'quotes',{**old,**body},True);return erp.quote_save(body,rid,body.get('version'),u['id'])
 @app.post('/erp/quotes/{rid}/{action}')
 def quote_action(rid:str,action:str,body:dict,request:Request):
  u=identity(request);q=erp.get('quotes',rid);allowed(u,'quotes',q,True)
  if action=='duplicate':
   copy={k:v for k,v in q.items() if k not in ('id','version','status','approved_at','approved_by','created_at','updated_at')};copy['name']+=' (copia)';return erp.quote_save(copy,actor=u['id'])
  if u['role']!='admin':raise DomainError('el propietario o un administrador debe aprobar, emitir y crear la obra')
  if action=='approve':return erp.quote_approve(rid,body.get('version'),u['id'])
  if action=='issue':
   doc=erp.quote_issue(rid,body.get('version'),u['id']);stored_quote_pdf(u,doc);return doc
  if action=='work':return erp.work_from_quote(rid,body.get('version'),u['id'])
  raise DomainError('Acción desconocida')
 @app.get('/erp/dashboard')
 def dashboard(request:Request):
  u=identity(request)
  if u['role']=='admin':return erp.dashboard()
  return {'works':visible(u,'works',erp.list('works')),'quotes':len(visible(u,'quotes',erp.list('quotes'))),'notice':'Vista limitada a sus permisos'}
 @app.post('/erp/changes/{rid}/decide')
 def change_decide(rid:str,body:dict,request:Request):
  u=identity(request);change=erp.get('changes',rid);allowed(u,'changes',change)
  if u['role']!='admin':raise DomainError('Solo un administrador aprueba o rechaza órdenes de cambio')
  return erp.decide_change(rid,body.get('version'),body.get('decision'),body.get('comment',''),u['id'])
 @app.get('/erp/works/{rid}/overview')
 def work_overview(rid:str,request:Request):
  u=identity(request);work=erp.get('works',rid);allowed(u,'works',work)
  try:require(u,'costs',rid);financial=True
  except DomainError:financial=False  # roles sin permiso de costos ven avance, no importes
  return erp.work_overview(rid,financial=financial)
 @app.get('/erp/reports')
 def reports(request:Request,work_id:str|None=None):
  u=identity(request);require(u,'costs',work_id);return erp.report(work_id)
 @app.get('/erp/reports/pdf')
 def reports_pdf(request:Request,work_id:str|None=None):
  u=identity(request);require(u,'costs',work_id);m=files.pdf(u,'Informe de obra',erp.report(work_id),project_id=work_id);meta,path=files.get(u,m['id']);return FileResponse(path,media_type='application/pdf',filename=meta['filename'])
 @app.get('/erp/documents/{rid}/pdf')
 def document_pdf(rid:str,request:Request):
  u=identity(request);doc=erp.get('documents',rid);allowed(u,'documents',doc);m=stored_quote_pdf(u,doc);meta,path=files.get(u,m['id']);return FileResponse(path,media_type='application/pdf',filename=meta['filename'])
 @app.get('/erp/quotes/{rid}/pdf')
 def quote_preview_pdf(rid:str,request:Request):
  u=identity(request);q=erp.get('quotes',rid);allowed(u,'quotes',q)
  data=render_quote(q,'BORRADOR' if q.get('status')=='draft' else 'SIN EMITIR')
  return Response(data,media_type='application/pdf',headers={'Content-Disposition':'inline; filename="vista-previa-cotizacion.pdf"'})
 @app.get('/erp/files')
 def list_files(request:Request,work_id:str|None=None,record_id:str|None=None):return files.list(identity(request),project_id=work_id,record_id=record_id)
 @app.post('/erp/files')
 async def upload(request:Request,file:UploadFile=File(...),work_id:str|None=Form(None),kind:str=Form('attachment'),record_kind:str|None=Form(None),record_id:str|None=Form(None)):
  u=identity(request)
  if record_kind or record_id:
   # El archivo se vincula a un registro existente; obra y tipo se derivan del registro, no del cliente.
   if record_kind not in ATTACHABLE or not record_id:raise DomainError('Este módulo no admite archivos adjuntos')
   record=erp.get(record_kind,record_id);allowed(u,record_kind,record,True)
   work_id=record.get('work_id') or (record['id'] if record_kind=='works' else None)
   kind={'contracts':'contract','evidence':'evidence'}.get(record_kind,'attachment')
  elif kind not in FILE_KINDS:raise DomainError('Tipo de archivo no permitido')
  require(u,'write',work_id);data=await file.read(10_000_001)
  if len(data)>10_000_000:raise DomainError('Máximo 10 MB por archivo')
  return files.save(u,file.filename,data,project_id=work_id,kind=kind,record_kind=record_kind,record_id=record_id)
 @app.get('/erp/files/{rid}/download')
 def download(rid:str,request:Request):
  meta,path=files.get(identity(request),rid);return FileResponse(path,filename=meta['filename'])
 @app.get('/erp/catalog/seed')
 def catalog_seed_preview(request:Request):require(identity(request),'settings');return erp.seed_preview()
 @app.post('/erp/catalog/seed')
 def catalog_seed(request:Request):
  u=identity(request);require(u,'settings');added=erp.seed_catalog(u['id']);return {'added':len(added),'items':added}
 @app.get('/erp/legacy-budgets')
 def legacy_list(request:Request):allowed(identity(request),'quotes',{},True);return erp.legacy_list()
 @app.post('/erp/legacy-budgets/{project}/import')
 def legacy_import(project:str,body:dict,request:Request):u=identity(request);allowed(u,'quotes',{},True);return erp.import_legacy(project,body.get('client_id'),u['id'])
 @app.get('/erp/company')
 def company_get(request:Request):require(identity(request),'settings');return erp.company_view()
 @app.put('/erp/company')
 def company_put(body:dict,request:Request):u=identity(request);require(u,'settings');return erp.save_company(body,u['id'])
 @app.get('/erp/integrations')
 def integrations(request:Request):require(identity(request),'settings');return erp_integrations.status()
 @app.get('/erp/ai/config')
 def ai_config(request:Request):require(identity(request),'settings');return erp_integrations.status()['openai']
 @app.put('/erp/ai/config')
 def ai_config_save(body:dict,request:Request):require(identity(request),'settings');return erp_integrations.save_config(body.get('api_key') or None,body.get('model'),body.get('enabled'))
 @app.delete('/erp/ai/config')
 def ai_config_remove(request:Request):require(identity(request),'settings');return erp_integrations.remove_config()
 @app.post('/erp/ai/test')
 def ai_test(body:dict,request:Request):require(identity(request),'settings');return erp_integrations.list_models(body.get('api_key') or None)
 @app.post('/erp/ai/apu')
 def ai_apu(body:dict,request:Request):
  allowed(identity(request),'quotes',body,True)
  result=erp_integrations.apu(body,[c for c in erp.list('catalog') if c.get('active') is not False])
  return {**result,'generated_at':time.time(),'model':erp_integrations.openai_config()['model']}
 @app.post('/erp/ai/suggest')
 def suggest(body:dict,request:Request):allowed(identity(request),'quotes',body,True);return erp_integrations.suggest(body.get('prompt'),erp.list('catalog'))
 @app.post('/erp/backup')
 def backup(request:Request):
  u=identity(request);require(u,'settings');folder=Path(store.path).parent/'backups';folder.mkdir(exist_ok=True);target=folder/('erp-'+str(time.time_ns())+'.db')
  with store.connect() as src:
   with sqlite3.connect(target) as dest:src.backup(dest)
  return {'filename':target.name,'created':True}
 @app.get('/erp/audit')
 def audit(request:Request):
  require(identity(request),'settings')
  with store.connect() as c:return [dict(r) for r in c.execute('SELECT id,at,subject,project,action FROM audit ORDER BY id DESC LIMIT 200')]
 @app.post('/erp/export/sheets')
 def sheets(body:dict,request:Request):
  require(identity(request),'settings');records=erp.list(body['kind']);keys=sorted({k for r in records for k in r});rows=[keys]+[[json.dumps(r.get(k,''),ensure_ascii=False) if isinstance(r.get(k), (dict,list)) else r.get(k,'') for k in keys] for r in records]
  return erp_integrations.export_sheet(body['spreadsheet_id'],body['sheet_name'],rows)

"""Queued, review-first assistant. Model output never supplies service prices or writes budgets."""
import json
import time
import uuid
import re
from jsonschema import validate,ValidationError
from .core import DomainError,canonical,amount
from .workspace import OWNER
from .takeoff import calculate_takeoff,_UNITS

AI_SCHEMA={
 'type':'object','additionalProperties':False,
 'properties':{
  'message':{'type':'string','maxLength':6000},
  'lines':{'type':'array','maxItems':100,'items':{'type':'object','additionalProperties':False,'properties':{'activity':{'type':'string','minLength':1,'maxLength':150},'row':{'type':['integer','null']},'quantity':{'type':'string','maxLength':30}},'required':['activity','row','quantity']}},
  'resources':{'type':'array','maxItems':100,'items':{'type':'object','additionalProperties':False,'properties':{
    'activity':{'type':'string','minLength':1,'maxLength':150},'row':{'type':['integer','null']},'name':{'type':'string','maxLength':150},'category':{'type':'string','enum':['material','labor','equipment']},'unit':{'type':'string','enum':sorted(_UNITS)},'coefficient_per_unit':{'type':'string','maxLength':30},'waste_percent':{'type':'string','maxLength':30},'package_size':{'type':['string','null'],'maxLength':30},'unit_price':{'type':'null'},'source':{'type':'string','maxLength':500}},'required':['activity','row','name','category','unit','coefficient_per_unit','waste_percent','package_size','unit_price','source']}},
  'assumptions':{'type':'array','maxItems':30,'items':{'type':'string','maxLength':1000}},
  'questions':{'type':'array','maxItems':15,'items':{'type':'string','maxLength':500}}
 },'required':['message','lines','resources','assumptions','questions']}

def init(store):
    with store.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS ai_jobs(id TEXT PRIMARY KEY,project TEXT,version INTEGER,prompt TEXT,state TEXT,context TEXT,result TEXT,created REAL,lease REAL)''')

def heartbeat(store):
    init(store)
    with store.connect() as c:c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ai_heartbeat',str(time.time())))

def ai_status(store):
    with store.connect() as c:r=c.execute('SELECT value FROM settings WHERE key=?',('ai_heartbeat',)).fetchone()
    return {'provider':'Codex','connected':bool(r and time.time()-float(r[0])<40),'notice':'Usa la cuenta Codex del propietario y sus límites de uso. No usa una clave OpenAI en el navegador.'}

def enqueue(store,project,prompt):
    init(store)
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>6000:raise DomainError('Escriba una solicitud de hasta 6000 caracteres')
    before=store.budget(OWNER,project);catalog=store.catalog(OWNER,project)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE');store.permission(c,OWNER,project,write=True)
        c.execute("UPDATE ai_jobs SET state='error',result=? WHERE state='running' AND lease<?",(canonical({'error':'La ejecución se interrumpió. Vuelva a enviar la solicitud.'}),time.time()))
        if c.execute("SELECT 1 FROM ai_jobs WHERE project=? AND state IN ('queued','running')",(project,)).fetchone():raise DomainError('Ya hay una solicitud en proceso en este presupuesto')
        old=c.execute("SELECT prompt,result FROM ai_jobs WHERE project=? AND state='done' ORDER BY created DESC LIMIT 1",(project,)).fetchone()
        context={'budget':before,'catalog':catalog,'previous':dict(old) if old else None}
        key=str(uuid.uuid4());c.execute('INSERT INTO ai_jobs VALUES(?,?,?,?,?,?,?,?,?)',(key,project,before['version'],prompt,'queued',canonical(context),None,time.time(),None))
        store.log(c,OWNER,project,'ai_request',{'id':key,'prompt':prompt})
    return get_job(store,key)

def get_job(store,key):
    init(store)
    with store.connect() as c:
        r=c.execute('SELECT * FROM ai_jobs WHERE id=?',(key,)).fetchone()
        if not r:raise DomainError('Solicitud no encontrada')
        store.permission(c,OWNER,r['project'])
        return {'id':r['id'],'project':r['project'],'state':r['state'],'prompt':r['prompt'],'version':r['version'],'result':json.loads(r['result']) if r['result'] else None}

def claim(store):
    init(store)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM ai_jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
        if not row:return None
        try:store.permission(c,OWNER,row['project'],write=True)
        except DomainError:
            c.execute("UPDATE ai_jobs SET state='cancelled' WHERE id=?",(row['id'],));return None
        c.execute("UPDATE ai_jobs SET state='running',lease=? WHERE id=?",(time.time()+180,row['id']))
        return {'id':row['id'],'prompt':row['prompt'],'context':json.loads(row['context'])}

def cancel(store,key):
    job=get_job(store,key)
    with store.connect() as c:c.execute("UPDATE ai_jobs SET state='cancelled' WHERE id=? AND state IN ('queued','running')",(key,))
    return get_job(store,key)

def fail(store,key,message):
    with store.connect() as c:c.execute("UPDATE ai_jobs SET state='error',result=? WHERE id=? AND state='running'",(canonical({'error':str(message)[:1000]}),key))

def finish(store,key,plan):
    try:validate(plan,AI_SCHEMA)
    except ValidationError:raise DomainError('La IA devolvió un formato no permitido') from None
    job=get_job(store,key)
    if job['state']!='running':raise DomainError('La solicitud ya no está en ejecución')
    project=job['project'];before=store.budget(OWNER,project)
    if before['version']!=job['version']:raise DomainError('El borrador cambió durante la consulta. Vuelva a solicitar la propuesta.')
    catalog={r['row']:r for r in store.catalog(OWNER,project)}
    existing={r['row']:r for r in before['lines']};lines=[];takeoffs=[];seen=set();activities={};warnings=[]
    for line in plan['lines']:
        row=line['row']
        activity=line['activity']
        if activity in activities or (row is not None and row in seen):raise DomainError('Actividad duplicada en la propuesta de IA')
        activities[activity]=row
        if not re.fullmatch(r'\d{1,7}(?:\.\d{1,6})?',line['quantity']):raise DomainError('Use cantidades decimales de hasta seis decimales, sin exponentes')
        q=amount(line['quantity'],True)
        if q>1000000:raise DomainError('Cantidad de actividad fuera de rango')
        seen.add(row);quantity=format(q,'f')
        if row is not None and (row not in catalog or not catalog[row]['active']):raise DomainError('La IA propuso un servicio no ofrecido')
        ref=catalog.get(row)
        if ref and ref['min'] is not None:
            price=existing[row]['unit_price'] if row in existing else ref['min']
            lines.append({'row':row,'quantity':quantity,'unit_price':price})
            warnings.append(ref['name']+': '+('rango; mínimo propuesto' if ref['max'] else 'precio desde')+' USD '+price+' de mano de obra; confirmar alcance.')
        else:warnings.append('Actividad sin tarifa JG: cantidades calculables, precio pendiente. No se inventó un costo de mano de obra.')
        resources=[{k:v for k,v in r.items() if k not in ('row','activity')} for r in plan['resources'] if r['activity']==activity and r['row']==row]
        if resources:
            for r in resources:r['source']='Estimado IA / datos de la solicitud; revisión técnica pendiente'
            try:computed=calculate_takeoff(quantity,resources)
            except ValueError as error:raise DomainError('Revise los coeficientes y unidades del APU: '+str(error)) from None
            takeoffs.append({'row':row,'activity':activity,'service':ref['name'] if ref else activity+' (sin tarifa JG)','area':quantity,'result':computed})
    if any(r['activity'] not in activities or activities[r['activity']]!=r['row'] for r in plan['resources']):raise DomainError('Materiales sin actividad asociada')
    # Existing lines cannot disappear through an ambiguous model answer.
    for row,line in existing.items():
        if row not in seen:lines.append({k:line[k] for k in ('row','quantity','unit_price')})
    proposal=store.preview(OWNER,project,before['version'],lines) if plan['lines'] and lines else None
    result={'message':plan['message'],'proposal':proposal,'takeoffs':takeoffs,'assumptions':plan['assumptions'],'questions':plan['questions'],'warnings':warnings,'plan':plan}
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE');store.permission(c,OWNER,project,write=True)
        current=c.execute('SELECT state FROM ai_jobs WHERE id=?',(key,)).fetchone()
        if not current or current[0]!='running':raise DomainError('Solicitud cancelada durante el cálculo')
        if store._budget(c,project)['version']!=job['version']:raise DomainError('El borrador cambió durante el cálculo')
        c.execute("UPDATE ai_jobs SET state='done',result=? WHERE id=?",(canonical(result),key))
        store.log(c,OWNER,project,'ai_result',{'id':key,'assumptions':plan['assumptions']})
    return get_job(store,key)

def latest_result(store,project):
    init(store);budget=store.budget(OWNER,project)
    with store.connect() as c:rows=c.execute("SELECT result FROM ai_jobs WHERE project=? AND state='done' ORDER BY created DESC LIMIT 50",(project,)).fetchall()
    if not rows:return None
    result=json.loads(rows[0][0]);proposal=result.get('proposal')
    result['applied']=bool(proposal and proposal['after']['lines']==budget['lines'] and budget['version']>=proposal['after']['version'])
    if not result['takeoffs']:
        for row in rows[1:]:
            previous=json.loads(row[0]);p=previous.get('proposal')
            if previous['takeoffs'] and p and p['after']['lines']==budget['lines'] and budget['version']>=p['after']['version']:
                result['takeoffs']=previous['takeoffs'];result['takeoff_assumptions']=previous['assumptions'];result['applied']=True;break
    return result

def active_job(store,project):
    init(store);store.budget(OWNER,project)
    with store.connect() as c:r=c.execute("SELECT id FROM ai_jobs WHERE project=? AND state IN ('queued','running') ORDER BY created DESC LIMIT 1",(project,)).fetchone()
    return get_job(store,r[0]) if r else None

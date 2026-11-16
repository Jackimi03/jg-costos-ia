"""SQLite ERP ledger. Monetary snapshots use Decimal; no integration writes occur here."""
import json
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from .core import DomainError, amount, money, canonical

KINDS = set('clients contacts contracts works stages tasks workers assignments timesheets materials expenses logs evidence progress incidents income reports settings catalog quotes documents changes'.split())
RELATIONS = {'client_id':'clients','work_id':'works','stage_id':'stages','task_id':'tasks','worker_id':'workers','quote_id':'quotes','contract_id':'contracts','assignment_id':'assignments'}
REQUIRED = {'clients':['name'],'contacts':['name','client_id'],'contracts':['name','client_id'],'works':['name'],'stages':['name','work_id'],'tasks':['name','work_id'],'workers':['name'],'assignments':['work_id','worker_id'],'timesheets':['work_id','worker_id','date'],'materials':['name','work_id'],'expenses':['name','work_id','amount'],'logs':['work_id','date','notes'],'evidence':['work_id','name'],'progress':['work_id','percent'],'incidents':['work_id','name'],'income':['work_id','amount'],'catalog':['name','unit','unit_price'],'settings':['name'],'reports':['name'],'changes':['name','work_id']}
# Estados válidos por módulo. erp.js debe ofrecer solo estos valores (lo comprueba tests/test_erp.py).
STATUSES={'works':{'planned','active','paused','closed','cancelled'},'tasks':{'pending','in_progress','completed','cancelled'},'incidents':{'open','resolved','closed'},'contracts':{'draft','active','completed','expired','cancelled'},'workers':{'active','inactive'},'changes':{'draft','pending','approved','rejected'}}

# Condiciones comerciales de JG (2026-11-16, indicadas por el propietario)
QUOTE_VALID_DAYS=15  # días desde la entrega de la cotización
ZONES={'panama_oeste':{'label':'Panamá Oeste','fee':Decimal('20.00')},'ciudad_panama':{'label':'Ciudad de Panamá','fee':Decimal('30.00')},'interior':{'label':'Interior del país','fee':Decimal('50.00')}}
ADVANCE_THRESHOLD=Decimal('200.00')  # total < 200 → 25 %; total ≥ 200 → 30 %
ADVANCE_LOW,ADVANCE_HIGH=Decimal('25'),Decimal('30')
_ZONE_WORDS=[
 ('panama_oeste','panama oeste|arraijan|chorrera|capira|chame|san carlos|vista alegre|veracruz|burunga|nuevo emperador|playa leona|puerto caimito|guadalupe|barrio colon|el coco|coronado|cerro silvestre|juan demostenes|nuevo chorrillo|santa clara de arraijan'),
 ('interior','cocle|penonome|aguadulce|anton|el valle|herrera|chitre|los santos|las tablas|pedasi|tonosi|veraguas|santiago|sona|chiriqui|david|boquete|volcan|bugaba|bocas del toro|changuinola|almirante|colon|portobelo|sabanitas|darien|meteti|yaviza|guna yala|ngabe|interior'),
 ('ciudad_panama','ciudad de panama|panama city|san francisco|bella vista|betania|bethania|calidonia|ancon|juan diaz|parque lefevre|pueblo nuevo|rio abajo|costa del este|obarrio|el cangrejo|punta pacifica|paitilla|albrook|clayton|condado del rey|tocumen|pedregal|las cumbres|chilibre|pacora|san miguelito|don bosco|brisas del golf|villa lucre|calle 50|via espana|transistmica|el dorado|marbella|casco viejo|curundu|chanis|las mananitas|24 de diciembre|ernesto cordoba|alcalde diaz|altos de panama|amador|via argentina|el carmen|via porras|panama$|, panama'),
]

def parse_location(text):
    """Coordenadas desde '8.98, -79.52', '8.98 -79.52' o un enlace de Google Maps (@lat,lng / q=lat,lng / !3dlat!4dlng)."""
    import re
    t=str(text or '')
    for pattern in (r'!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)',r'@(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)',r'[?&](?:q|query|ll|center)=(-?\d{1,2}\.\d+)(?:,|%2C)\s*(-?\d{1,3}\.\d+)',r'^\s*(-?\d{1,2}\.\d+)\s*[, ]\s*(-?\d{1,3}\.\d+)\s*$'):
        m=re.search(pattern,t,re.I)
        if m:
            lat,lng=float(m.group(1)),float(m.group(2))
            if -90<=lat<=90 and -180<=lng<=180:return {'lat':round(lat,6),'lng':round(lng,6)}
    return None

def zone_from_coords(lat,lng):
    """Aproximación por coordenadas (el Canal separa Panamá Oeste de la ciudad). Fuera de Panamá: None."""
    if not (7.0<=lat<=9.7 and -83.1<=lng<=-77.1):return None
    if 8.4<=lat<=9.15 and -80.25<=lng<-79.60:return 'panama_oeste'
    if 8.85<=lat<=9.25 and -79.60<=lng<=-79.25:return 'ciudad_panama'
    return 'interior'

def detect_zone(text):
    """Zona del trabajo a partir de una dirección libre. None si no se reconoce (el usuario la elige)."""
    import re,unicodedata
    location=parse_location(text)
    if location:return zone_from_coords(location['lat'],location['lng'])
    plain=''.join(ch for ch in unicodedata.normalize('NFD',str(text or '').lower()) if unicodedata.category(ch)!='Mn').strip()
    if not plain:return None
    for zone,words in _ZONE_WORDS:
        if re.search(r'(?:^|\b)(?:'+words+r')(?:\b|$)',plain):return zone
    return None

def payment_terms(total,zone,zone_source=None):
    total=Decimal(total);pct=ADVANCE_LOW if total<ADVANCE_THRESHOLD else ADVANCE_HIGH
    advance=Decimal(money(total*pct/100));fee=ZONES[zone]['fee'] if zone in ZONES else None
    balance=max(Decimal(0),total-advance-(fee or 0))
    return {'zone':zone,'zone_label':ZONES[zone]['label'] if zone in ZONES else None,'zone_source':zone_source,'visit_fee':money(fee) if fee is not None else None,
        'advance_percent':str(pct),'advance':money(advance),'balance':money(balance),'valid_days':QUOTE_VALID_DAYS,'threshold':money(ADVANCE_THRESHOLD)}

class ERP:
    def __init__(self, store):
        self.store=store
        with store.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS erp_records(kind TEXT NOT NULL,id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS erp_kind ON erp_records(kind,deleted);
            CREATE TABLE IF NOT EXISTS erp_versions(id TEXT,version INTEGER,payload TEXT NOT NULL,at REAL NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(id,version));''')
    def _kind(self,kind):
        if kind not in KINDS: raise DomainError('Módulo desconocido')
    def _row(self,c,kind,id):
        self._kind(kind)
        r=c.execute('SELECT * FROM erp_records WHERE kind=? AND id=? AND deleted=0',(kind,id)).fetchone()
        if not r: raise DomainError('Registro no encontrado')
        return json.loads(r['payload'])
    def get(self,kind,id):
        with self.store.connect() as c:return self._row(c,kind,id)
    def list(self,kind,**filters):
        self._kind(kind)
        with self.store.connect() as c:
            rows=[json.loads(r[0]) for r in c.execute('SELECT payload FROM erp_records WHERE kind=? AND deleted=0 ORDER BY updated_at DESC',(kind,))]
        return [r for r in rows if all(str(r.get(k,''))==str(v) for k,v in filters.items() if v is not None and v!='')]
    def history(self,kind,id):
        self.get(kind,id)
        with self.store.connect() as c:return [json.loads(r[0]) for r in c.execute('SELECT payload FROM erp_versions WHERE id=? ORDER BY version DESC',(id,))]
    def _validate(self,c,kind,data):
        for field in REQUIRED.get(kind,[]):
            if data.get(field) is None or str(data[field]).strip()=='':raise DomainError('Campo obligatorio: '+field)
        for field,target in RELATIONS.items():
            if data.get(field):self._row(c,target,data[field])
        for field in ('amount','quantity','unit_price','hourly_rate','hours','budget','percent'):
            # Los formularios envían "" para campos numéricos vacíos: se tratan como ausentes
            # (p. ej. jornada sin tarifa usa la del trabajador; obra sin presupuesto no rompe informes).
            if field in data and (data[field] is None or str(data[field]).strip()==''):data.pop(field);continue
            if field in data:
                value=amount(data[field]);data[field]=str(value)
                if field=='percent' and value>100:raise DomainError('Avance debe estar entre 0 y 100')
        for field in ('date','start_date','end_date','due_date'):
            if data.get(field):
                try:date.fromisoformat(data[field])
                except (ValueError,TypeError):raise DomainError('Fecha inválida: '+field) from None
        if data.get('start_date') and data.get('end_date') and data['end_date']<data['start_date']:raise DomainError('La fecha final precede a la inicial')
        if data.get('stage_id') and data.get('work_id') and self._row(c,'stages',data['stage_id']).get('work_id')!=data['work_id']:raise DomainError('Etapa de otra obra')
        if data.get('task_id') and data.get('work_id') and self._row(c,'tasks',data['task_id']).get('work_id')!=data['work_id']:raise DomainError('Tarea de otra obra')
        if data.get('assignment_id'):
            assignment=self._row(c,'assignments',data['assignment_id'])
            if assignment.get('work_id')!=data.get('work_id') or assignment.get('worker_id')!=data.get('worker_id'):raise DomainError('Asignación no corresponde a la obra y trabajador')
        if kind=='changes':
            # Órdenes de cambio: importe y días con signo (un cambio puede reducir costo o plazo).
            for field in ('cost_delta','days_delta'):
                if data.get(field) in ('',None):data.pop(field,None);continue
                try:value=Decimal(str(data[field]).strip())
                except (InvalidOperation,ValueError):raise DomainError('Número inválido: '+field) from None
                if not value.is_finite() or abs(value)>Decimal('1000000000'):raise DomainError('Valor fuera de rango: '+field)
                if field=='days_delta':
                    if value!=value.to_integral_value() or abs(value)>3650:raise DomainError('Los días deben ser un entero entre -3650 y 3650')
                    data[field]=str(int(value))
                else:data[field]=money(value)
        if kind in STATUSES and data.get('status') and data['status'] not in STATUSES[kind]:raise DomainError('Estado inválido')
        if kind=='works' and data.get('status')=='closed' and data.get('id'):
            for row in c.execute("SELECT kind,payload FROM erp_records WHERE kind IN ('tasks','incidents') AND deleted=0"):
                child=json.loads(row['payload'])
                if child.get('work_id')==data['id'] and child.get('status') not in ('completed','cancelled','resolved','closed'):raise DomainError('Complete las tareas y resuelva las incidencias antes del cierre')
        if kind=='timesheets':
            if data.get('entry') and data.get('exit'):
                try:
                    entry=datetime.fromisoformat(data['date']+'T'+data['entry']);end=datetime.fromisoformat(data['date']+'T'+data['exit'])
                    hours=Decimal(str((end-entry).total_seconds()))/Decimal(3600)
                    if hours<=0:raise ValueError()
                    data['hours']=str(hours)
                except (ValueError,TypeError):raise DomainError('Horario inválido; salida debe ser posterior a entrada') from None
            hours=amount(data.get('hours',0),True)
            if hours>24:raise DomainError('Jornada superior a 24 horas')
            worker=self._row(c,'workers',data['worker_id'])
            data['hourly_rate']=str(amount(data.get('hourly_rate',worker.get('hourly_rate',0))))
            data['cost']=money(hours*amount(data['hourly_rate']))
        if kind=='materials':data['cost']=money(amount(data.get('quantity',0))*amount(data.get('unit_price',0)))
    def _save(self,c,kind,data,id,version,actor,internal=False):
        self._kind(kind)
        if kind in ('quotes','documents') and not internal:raise DomainError('Use el flujo de cotizaciones y emisión')
        old=self._row(c,kind,id) if id else None
        if old and (version is None or version!=old['version']):raise DomainError('Versión cambió; recargue antes de guardar')
        if kind=='changes' and not internal:
            if old and old.get('status') in ('approved','rejected'):raise DomainError('Orden de cambio decidida: no se modifica; registre una nueva')
            if data.get('status') in ('approved','rejected'):raise DomainError('Use Aprobar o Rechazar para decidir la orden de cambio')
        data=dict(data)
        for k in ('id','version','created_at','updated_at','deleted'):data.pop(k,None)
        if old:data={**old,**data}
        self._validate(c,kind,data)
        now=time.time();id=id or uuid.uuid4().hex
        data['created_by']=old.get('created_by',actor) if old else actor
        data.update(id=id,version=(old['version']+1 if old else 1),created_at=old['created_at'] if old else now,updated_at=now)
        payload=canonical(data)
        c.execute('INSERT INTO erp_records VALUES(?,?,?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,payload=excluded.payload,updated_at=excluded.updated_at',(kind,id,data['version'],payload,data['created_at'],now))
        c.execute('INSERT INTO erp_versions VALUES(?,?,?,?,?)',(id,data['version'],payload,now,actor))
        self.store.log(c,actor,data.get('work_id',id),'erp.'+kind+'.save',data)
        return data
    def save(self,kind,data,id=None,version=None,actor='local-owner'):
        if not isinstance(data,dict):raise DomainError('Datos inválidos')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');return self._save(c,kind,data,id,version,actor)
    def delete(self,kind,id,version=None,actor='local-owner'):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');old=self._row(c,kind,id)
            if version!=old['version']:raise DomainError('Versión cambió; recargue antes de eliminar')
            if kind=='documents' or (kind=='quotes' and old.get('status')!='draft'):raise DomainError('Documento aprobado o emitido no se elimina')
            if kind=='changes' and old.get('status') in ('approved','rejected'):raise DomainError('Orden de cambio decidida: no se elimina')
            for row in c.execute('SELECT payload FROM erp_records WHERE deleted=0 AND id<>?',(id,)):
                record=json.loads(row[0])
                if any(record.get(f)==id for f in RELATIONS):raise DomainError('Registro relacionado; archive mediante su estado')
            c.execute('UPDATE erp_records SET deleted=1,version=version+1,updated_at=? WHERE id=?',(time.time(),id))
            self.store.log(c,actor,id,'erp.'+kind+'.delete',old)
            return {'id':id,'deleted':True}
    def quote_preview(self,data,client=None,margin_rule=None):
        if data.get('margin_mode')=='auto':
            # Utilidad por tramos: se calcula con el % alto; si el total queda por debajo del umbral, con el % bajo.
            rule=margin_rule or data.get('margin_rule')
            if not rule:raise DomainError('Configure la utilidad por tramos en Configuración o use utilidad manual')
            result=self.quote_preview({**data,'margin_mode':'manual','margin_percent':rule['high']},client)
            if Decimal(result['total'])<Decimal(rule['threshold']):result=self.quote_preview({**data,'margin_mode':'manual','margin_percent':rule['low']},client)
            return {**result,'margin_mode':'auto','margin_rule':rule}
        if data.get('margin_mode') not in (None,'','manual','auto'):raise DomainError('Modo de utilidad inválido')
        lines=data.get('lines',[])
        if not isinstance(lines,list) or not 1<=len(lines)<=500:raise DomainError('Incluya de 1 a 500 partidas')
        result=[];totals={k:Decimal(0) for k in ('materials','labor','equipment','transport')}
        for raw in lines:
            if not isinstance(raw,dict) or not str(raw.get('name','')).strip():raise DomainError('Partida sin descripción')
            line=dict(raw);q=amount(line.get('quantity'),True);line['quantity']=str(q)
            if q>1000000:raise DomainError('Cantidad excesiva')
            costs={}
            if line.get('apu') not in (None,'',{}):line['apu']=self._apu_costs(line)  # APU manda: fija materiales, mano de obra y equipos por unidad
            if not any(k in line for k in totals):
                if 'unit_price' not in line:raise DomainError('Indique el precio verificado de cada partida')
                line['labor']=line['unit_price']
            for k in totals:
                costs[k]=Decimal(money(amount(line.get(k,0))));line[k]=money(costs[k]);totals[k]+=Decimal(money(q*costs[k]))
            line['unit_price']=money(sum(costs.values()));line['subtotal']=money(sum(Decimal(money(q*v)) for v in costs.values()));result.append(line)
        direct=sum(totals.values())
        data={**data,'valid_days':str(QUOTE_VALID_DAYS)}  # política fija de JG
        rates={}
        for key in ('indirect_percent','margin_percent','discount_percent','tax_percent'):
            rates[key]=amount(data.get(key,0))
            if rates[key]>100:raise DomainError('Porcentaje fuera de rango: '+key)
        # Precio de venta por partida: costo directo × (1+gastos generales) × (1+utilidad), redondeado al centavo.
        # Así el cliente ve precios unitarios completos y subtotal = Σ cantidad × precio unitario de venta.
        factor=(1+rates['indirect_percent']/100)*(1+rates['margin_percent']/100)
        for line in result:
            line['sale_unit_price']=money(Decimal(line['unit_price'])*factor);line['sale_subtotal']=money(Decimal(line['quantity'])*Decimal(line['sale_unit_price']))
        subtotal=sum((Decimal(l['sale_subtotal']) for l in result),Decimal(0))
        indirect=Decimal(money(direct*rates['indirect_percent']/100));margin=subtotal-direct-indirect  # la utilidad absorbe el redondeo
        discount=Decimal(money(subtotal*rates['discount_percent']/100));tax=Decimal(money((subtotal-discount)*rates['tax_percent']/100));total=subtotal-discount+tax
        if total>1000000000:raise DomainError('Cotización excede el límite permitido')
        zone=data.get('zone') or None;source='manual'
        if zone and zone not in ZONES:raise DomainError('Zona inválida')
        if not zone:
            zone=detect_zone(data.get('site_address')) or detect_zone((client or {}).get('address'));source='address' if zone else None
        location=parse_location(data.get('site_address')) or (None if data.get('site_address') else parse_location((client or {}).get('address')))
        data={**data,'payment_terms':payment_terms(total,zone,source),'site_location':location}
        return {**data,'lines':result,**{k:str(v) for k,v in rates.items()},'totals':{**{k:money(v) for k,v in totals.items()},'direct':money(direct),'indirect':money(indirect),'margin':money(margin),'subtotal':money(subtotal),'discount':money(discount),'tax':money(tax),'total':money(total)},'total':money(total),'calculation':'precio de venta = costo directo × (1+indirectos) × (1+utilidad), redondeado por partida'}
    @staticmethod
    def _apu_costs(line):
        apu=line['apu']
        if not isinstance(apu,dict) or not isinstance(apu.get('resources'),list):raise DomainError('APU inválido')
        if len(apu['resources'])>60:raise DomainError('Máximo 60 recursos por APU')
        sums={'material':Decimal(0),'labor':Decimal(0),'equipment':Decimal(0)};clean=[]
        for r in apu['resources']:
            if not isinstance(r,dict) or r.get('category') not in sums:raise DomainError('Recurso de APU sin tipo válido')
            name=str(r.get('name','')).strip()
            if not name:raise DomainError('Recurso de APU sin descripción')
            qty=amount(r.get('quantity_per_unit') or 0);waste=amount(r.get('waste_percent') or 0);price=amount(r.get('unit_price') or 0)
            if waste>100:raise DomainError('Desperdicio fuera de rango')
            cost=qty*(1+waste/100)*price;sums[r['category']]+=cost
            clean.append({**{k:r.get(k) for k in ('category','unit','source','catalog_id') if r.get(k) is not None},'name':name[:150],'quantity_per_unit':str(qty),'waste_percent':str(waste),'unit_price':money(price),'cost_per_unit':money(cost)})
        line['materials'],line['labor'],line['equipment']=money(sums['material']),money(sums['labor']),money(sums['equipment'])
        return {**{k:apu.get(k) for k in ('message','assumptions','questions','generated_at','model') if apu.get(k) is not None},'resources':clean,'cost_per_unit':money(sum(sums.values()))}
    def quote_save(self,data,id=None,version=None,actor='local-owner'):
        defaults=self.pricing_defaults()
        if not id:  # cotización nueva: porcentajes y modo de utilidad automáticos si no se indicaron
            data={**data,**{k:defaults[k] for k in ('indirect_percent','margin_percent','margin_mode') if data.get(k) in (None,'')}}
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=self._row(c,'quotes',id) if id else None
            if old and old.get('status')!='draft':raise DomainError('Cotización aprobada inmutable; duplique para corregir')
            merged={**(old or {}),**data};client=self._row(c,'clients',merged['client_id']) if merged.get('client_id') else None;preview=self.quote_preview(merged,client,defaults['margin_rule'] if merged.get('margin_mode')=='auto' else None)
            if not str(preview.get('name','')).strip():raise DomainError('Indique el nombre de la cotización')
            if not preview.get('client_id'):raise DomainError('Seleccione un cliente')
            preview['status']='draft'
            return self._save(c,'quotes',preview,id,version,actor,True)
    def quote_approve(self,id,version,actor='local-owner'):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');q=self._row(c,'quotes',id)
            if q['status']!='draft':raise DomainError('Solo se aprueban borradores')
            if 'payment_terms' in q and not q['payment_terms'].get('zone'):raise DomainError('Indique la zona o dirección del trabajo para calcular cotización, visita técnica y anticipo')
            q.update(status='approved',approved_at=time.time(),approved_by=actor)
            return self._save(c,'quotes',q,id,version,actor,True)
    COMPANY_KEYS={'empresa':'name','nombre de la empresa':'name','ruc':'ruc','direccion':'address','telefono':'phone','correo':'email','email':'email','condiciones':'conditions','condiciones de cotizacion':'conditions','validez':'valid_days','validez (dias)':'valid_days','gastos generales':'overhead_percent','gastos generales (%)':'overhead_percent','utilidad':'margin_percent','utilidad (%)':'margin_percent','gastos fijos mensuales':'monthly_fixed_costs','gastos fijos mensuales (usd)':'monthly_fixed_costs','utilidad trabajos de usd 200 o mas (%)':'margin_high','utilidad trabajos menores de usd 200 (%)':'margin_low','dv':'dv','digito verificador':'dv','condiciones adicionales':'conditions'}
    # Formulario de empresa: clave → nombre con que se guarda el parámetro en Configuración
    COMPANY_FIELDS=[('name','Nombre de la empresa'),('ruc','RUC'),('dv','DV'),('address','Dirección'),('phone','Teléfono'),('email','Correo'),('conditions','Condiciones adicionales'),('monthly_fixed_costs','Gastos fijos mensuales'),('margin_high','Utilidad trabajos de USD 200 o más (%)'),('margin_low','Utilidad trabajos menores de USD 200 (%)'),('overhead_percent','Gastos generales (%)')]
    @staticmethod
    def _plain(text):
        import unicodedata
        return ''.join(ch for ch in unicodedata.normalize('NFD',str(text).strip().lower()) if unicodedata.category(ch)!='Mn')
    def company_settings(self):
        """Clave canónica → registro de Configuración que la guarda (el más reciente si hay varios)."""
        found={}
        for row in self.list('settings'):
            key=self.COMPANY_KEYS.get(self._plain(row.get('name','')))
            if key and key not in found:found[key]=row
        return found
    def save_company(self,fields,actor='local-owner'):
        import re
        if not isinstance(fields,dict):raise DomainError('Datos inválidos')
        allowed={k for k,_ in self.COMPANY_FIELDS}
        unknown=set(fields)-allowed
        if unknown:raise DomainError('Campo de empresa desconocido: '+', '.join(sorted(unknown)))
        clean={}
        for key,value in fields.items():
            value='' if value is None else str(value).strip()
            if len(value)>(2000 if key=='conditions' else 200):raise DomainError('Texto demasiado largo: '+key)
            if value and key=='ruc' and not re.fullmatch(r'[0-9A-Za-z-]{1,30}',value):raise DomainError('RUC inválido: use números, letras y guiones')
            if value and key=='dv' and not re.fullmatch(r'[0-9]{1,2}',value):raise DomainError('DV inválido: uno o dos dígitos')
            if value and key=='email' and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',value):raise DomainError('Correo inválido')
            if value and key in ('monthly_fixed_costs','margin_high','margin_low','overhead_percent'):
                number=self._setting_number(value,None if key=='monthly_fixed_costs' else Decimal(100))
                if number is None:raise DomainError('Número inválido: '+dict(self.COMPANY_FIELDS)[key])
                value=str(number)
            clean[key]=value
        current=self.company_settings();labels=dict(self.COMPANY_FIELDS)
        for key,value in clean.items():
            row=current.get(key)
            if row:
                if str(row.get('value') or '')!=value:self.save('settings',{'value':value},row['id'],row['version'],actor)
            elif value:self.save('settings',{'name':labels[key],'value':value},actor=actor)
        return self.company_view()
    def company_view(self):
        profile=self.company_profile();rows=self.company_settings()
        return {'fields':[{'key':k,'label':l,'value':profile.get(k,'')} for k,l in self.COMPANY_FIELDS],'managed_setting_ids':[r['id'] for r in rows.values()]}
    def company_profile(self):
        """Datos de empresa para documentos, tomados de Configuración (nombre del parámetro → valor)."""
        import unicodedata
        plain=lambda t:''.join(ch for ch in unicodedata.normalize('NFD',str(t).strip().lower()) if unicodedata.category(ch)!='Mn')
        profile={}
        for row in self.list('settings'):
            key=self.COMPANY_KEYS.get(plain(row.get('name','')))
            if key and str(row.get('value') or row.get('notes') or '').strip():profile[key]=str(row.get('value') or row.get('notes')).strip()
        return profile
    @staticmethod
    def _setting_number(value,maximum=None):
        """Lee números escritos libremente en Configuración: '12 %', 'USD 1,250.50', '$600'."""
        if value in (None,''):return None
        text=str(value).upper().replace('USD','').replace('$','').replace('%','').replace(',','').strip()
        try:number=Decimal(text)
        except (InvalidOperation,ValueError):return None
        if not number.is_finite() or number<0 or (maximum is not None and number>maximum):return None
        return number
    def pricing_defaults(self,today=None,months=3):
        """Porcentajes iniciales de una cotización nueva.
        Gastos generales automáticos = gastos fijos mensuales ÷ costo directo mensual promedio de las obras
        (jornadas + materiales comprados + gastos) en los últimos `months` meses. Si no hay datos o falta el
        gasto fijo, se usa el porcentaje configurado. La utilidad siempre sale de Configuración."""
        today=date.fromisoformat(today) if today else date.today();start=(today-timedelta(days=30*months)).isoformat();end=today.isoformat()
        profile=self.company_profile()
        fixed=self._setting_number(profile.get('monthly_fixed_costs'))
        configured=self._setting_number(profile.get('overhead_percent'),Decimal(100))
        margin=self._setting_number(profile.get('margin_percent'),Decimal(100))
        high=self._setting_number(profile.get('margin_high'),Decimal(100));low=self._setting_number(profile.get('margin_low'),Decimal(100))
        rule={'high':money(high),'low':money(low if low is not None else high),'threshold':money(ADVANCE_THRESHOLD)} if high is not None else None
        inside=lambda r:bool(r.get('date')) and start<=r['date']<=end
        num=lambda v:amount(v if v not in ('',None) else 0)
        direct=sum((num(r.get('cost')) for r in self.list('timesheets') if inside(r)),Decimal(0))
        direct+=sum((num(r.get('cost')) for r in self.list('materials') if inside(r) and r.get('movement')!='consumption'),Decimal(0))
        direct+=sum((num(r.get('amount')) for r in self.list('expenses') if inside(r)),Decimal(0))
        monthly=direct/months
        basis={'monthly_fixed_costs':money(fixed) if fixed is not None else None,'monthly_direct_cost':money(monthly),'months':months,'from':start,'to':end}
        warning=None;limit=Decimal(50)  # por encima de esto el cálculo suele indicar pocos costos de obra registrados
        auto=Decimal(money(fixed/monthly*100)) if fixed is not None and monthly>0 else None
        if auto is not None and auto<=limit:overhead,source=auto,'auto'
        elif configured is not None:
            overhead,source=configured,'setting'
            if auto is not None:warning=f'El cálculo automático da {money(auto)} % (pocos costos de obra registrados); se usa el porcentaje configurado.'
        elif auto is not None:
            overhead,source=limit,'auto_capped'
            warning=f'El cálculo automático da {money(auto)} % por falta de costos de obra registrados; se limitó a 50 %. Revisa el porcentaje antes de enviar la cotización.'
        else:overhead,source=Decimal(0),'none'
        basis['auto_percent']=money(auto) if auto is not None else None
        return {'indirect_percent':money(overhead),'margin_percent':rule['high'] if rule else money(margin or Decimal(0)),'margin_mode':'auto' if rule else 'manual','margin_rule':rule,'overhead_source':source,'margin_source':'rule' if rule else 'setting' if margin is not None else 'none','basis':basis,'warning':warning}
    def quote_issue(self,id,version,actor='local-owner'):
        profile=self.company_profile()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');q=self._row(c,'quotes',id)
            if q['version']!=version:raise DomainError('Versión cambió; recargue')
            if q['status'] not in ('approved','issued'):raise DomainError('Apruebe la cotización antes de emitir')
            for row in c.execute("SELECT payload FROM erp_records WHERE kind='documents' AND deleted=0"):
                doc=json.loads(row[0])
                if doc['quote_id']==id:return doc
            n=c.execute("SELECT COUNT(*) FROM erp_records WHERE kind='documents'").fetchone()[0]+1
            client=self._row(c,'clients',q['client_id']);work=self._row(c,'works',q['work_id']) if q.get('work_id') else {}
            doc=self._save(c,'documents',{'name':q['name'],'quote_id':id,'client_id':q['client_id'],'work_id':q.get('work_id'),'work_name':work.get('name',''),'client_snapshot':{k:client.get(k) for k in ('name','identification','contact','address','phone','whatsapp','email')},'company_snapshot':profile,'number':f'COT-{date.today().year}-{n:06d}','document_type':'cotizacion_no_fiscal','status':'issued','total':q['total'],'snapshot':q,'issued_at':time.time()},None,None,actor,True)
            self._save(c,'quotes',{**q,'status':'issued'},id,version,actor,True)
            return doc
    def decide_change(self,id,version,decision,comment='',actor='local-owner'):
        if decision not in ('approved','rejected'):raise DomainError('Decisión inválida')
        if not isinstance(comment,str) or len(comment)>2000:raise DomainError('Comentario inválido')
        if decision=='rejected' and not comment.strip():raise DomainError('Indique el motivo del rechazo')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');change=self._row(c,'changes',id)
            if change.get('status')!='pending':raise DomainError('Solo se deciden órdenes en estado pendiente')
            return self._save(c,'changes',{'status':decision,'decided_by':actor,'decided_at':time.time(),'decision_comment':comment.strip()},id,version,actor,True)
    @staticmethod
    def change_impact(work,changes):
        """Presupuesto y entrega revisados: base + órdenes aprobadas. Las pendientes se informan aparte."""
        sums={s:{'cost':Decimal(0),'days':0,'count':0} for s in ('approved','pending')}
        for ch in changes:
            if ch.get('status') in sums:
                b=sums[ch['status']];b['cost']+=Decimal(ch.get('cost_delta') or 0);b['days']+=int(ch.get('days_delta') or 0);b['count']+=1
        base=Decimal(work.get('budget') or 0) if str(work.get('budget') or '').strip() else Decimal(0)
        end=work.get('end_date')
        shift=lambda days:(date.fromisoformat(end)+timedelta(days=days)).isoformat() if end else None
        return {'original_budget':money(base),'revised_budget':money(base+sums['approved']['cost']),'original_end':end,'revised_end':shift(sums['approved']['days']),
            'approved':{'count':sums['approved']['count'],'cost':money(sums['approved']['cost']),'days':sums['approved']['days']},
            'pending':{'count':sums['pending']['count'],'cost':money(sums['pending']['cost']),'days':sums['pending']['days'],'end_if_approved':shift(sums['approved']['days']+sums['pending']['days'])}}
    def work_from_quote(self,id,version,actor='local-owner'):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');q=self._row(c,'quotes',id)
            if q['version']!=version:raise DomainError('Versión cambió; recargue')
            if q['status'] not in ('approved','issued'):raise DomainError('Se requiere una cotización aprobada')
            for row in c.execute("SELECT payload FROM erp_records WHERE kind='works' AND deleted=0"):
                work=json.loads(row[0])
                if work.get('quote_id')==id:return work
            return self._save(c,'works',{'name':q['name'],'client_id':q['client_id'],'quote_id':id,'address':q.get('site_address') or '','budget':q['total'],'status':'active','quote_snapshot':q},None,None,actor)
    def report(self,work_id=None):
        works=self.list('works');works=[w for w in works if not work_id or w['id']==work_id]
        if work_id and not works:raise DomainError('Obra no encontrada')
        rows=[]
        # Registros antiguos pueden conservar "" en campos numéricos vacíos: se leen como 0.
        num=lambda value:amount(value if value not in ('',None) else 0)
        for w in works:
            selected=lambda kind:self.list(kind,work_id=w['id'])
            labor=sum((num(r.get('cost')) for r in selected('timesheets')),Decimal(0));materials=sum((num(r.get('cost')) for r in selected('materials')),Decimal(0));expenses=sum((num(r.get('amount')) for r in selected('expenses')),Decimal(0));income=sum((num(r.get('amount')) for r in selected('income')),Decimal(0));cost=labor+materials+expenses
            progress=selected('progress');latest={}
            for r in reversed(progress):latest[r.get('task_id') or 'work']=num(r.get('percent'))
            pct=sum(latest.values())/len(latest) if latest else Decimal(0)
            changes=self.change_impact(w,selected('changes'));budget=Decimal(changes['revised_budget'])
            rows.append({'id':w['id'],'name':w['name'],'status':w.get('status'),'budget':money(budget),'original_budget':changes['original_budget'],'approved_changes':changes['approved']['cost'],'labor':money(labor),'materials':money(materials),'expenses':money(expenses),'cost':money(cost),'income':money(income),'profit':money(income-cost),'remaining':money(budget-cost),'hours':str(sum((num(r.get('hours')) for r in selected('timesheets')),Decimal(0))),'progress':money(pct)})
        return {'works':rows,'totals':{k:money(sum((Decimal(r[k]) for r in rows),Decimal(0))) for k in ('budget','labor','materials','expenses','cost','income','profit','remaining','hours')}}
    def dashboard(self,today=None):
        """Resumen operativo. Solo lectura. `today` (AAAA-MM-DD) permite pruebas deterministas."""
        today=today or date.today().isoformat();horizon=(date.fromisoformat(today)+timedelta(days=30)).isoformat()
        report=self.report();quotes=self.list('quotes');contracts=self.list('contracts')
        names={w['id']:w['name'] for w in report['works']}
        with self.store.connect() as c:recent=[dict(r) for r in c.execute("SELECT at,subject,action,project FROM audit WHERE action LIKE 'erp.%' ORDER BY id DESC LIMIT 12")]
        alerts=[];upcoming=[]
        def alert(kind,severity,record,message,record_kind,work_id=None,when=None):
            alerts.append({'kind':kind,'severity':severity,'work_id':work_id or record.get('work_id'),'record_kind':record_kind,'record_id':record['id'],'name':record['name'],'message':message,'date':when,'context':names.get(work_id or record.get('work_id'),'')})
        def soon(when,record_kind,record,context):
            upcoming.append({'date':when,'record_kind':record_kind,'record_id':record['id'],'name':record['name'],'work_id':record.get('work_id') or (record['id'] if record_kind=='works' else None),'context':context})
        for r in report['works']:
            if Decimal(r['budget'])>0 and Decimal(r['remaining'])<0:alerts.append({'kind':'over_budget','severity':'high','work_id':r['id'],'record_kind':'works','record_id':r['id'],'name':r['name'],'amount':money(-Decimal(r['remaining'])),'message':'Supera el presupuesto en USD '+money(-Decimal(r['remaining']))})
        for i in self.list('incidents'):
            if i.get('status','open')=='open':alert('open_incident','high',i,'Incidencia abierta · '+names.get(i.get('work_id'),''),'incidents')
        for t in self.list('tasks'):
            due=t.get('due_date')
            if not due or t.get('status') in ('completed','cancelled'):continue
            if due<today:alert('overdue_task','medium',t,f'Actividad vencida el {due} · '+names.get(t.get('work_id'),''),'tasks',when=due)
            elif due<=horizon:soon(due,'tasks',t,'Actividad · '+names.get(t.get('work_id'),''))
        for k in contracts:
            end=k.get('due_date') or k.get('end_date')
            if not end or k.get('status') not in ('draft','active'):continue
            if end<today:alert('contract_overdue','medium',k,f'Contrato con vencimiento el {end}','contracts',when=end)
            elif end<=horizon:soon(end,'contracts',k,'Vencimiento de contrato')
        all_changes=self.list('changes')
        for ch in all_changes:
            if ch.get('status')=='pending':
                alerts.append({'kind':'pending_change','severity':'medium','work_id':ch.get('work_id'),'record_kind':'changes','record_id':ch['id'],'name':ch['name'],'amount':ch.get('cost_delta') or '0.00','days':int(ch.get('days_delta') or 0),'context':names.get(ch.get('work_id'),''),'message':'Orden de cambio pendiente de decisión'})
        for w in self.list('works'):
            end=self.change_impact(w,[c for c in all_changes if c.get('work_id')==w['id']])['revised_end']
            if not end or w.get('status') not in ('planned','active','paused'):continue
            if end<today:alert('work_delayed','medium',w,f'Fecha final superada ({end})','works',w['id'],end)
            elif end<=horizon:soon(end,'works',w,'Entrega de obra')
        alerts.sort(key=lambda a:{'high':0,'medium':1}.get(a['severity'],2));upcoming.sort(key=lambda u:u['date'])
        pipeline={}
        for status in ('draft','approved','issued'):
            selected=[q for q in quotes if q.get('status')==status]
            pipeline[status]={'count':len(selected),'total':money(sum((amount(q.get('total') or 0) for q in selected),Decimal(0)))}
        budget=Decimal(report['totals']['budget']);cost=Decimal(report['totals']['cost'])
        return {**report,'today':today,'active_works':sum(w.get('status')=='active' for w in report['works']),'quotes':len(quotes),'pending_quotes':sum(q.get('status')=='draft' for q in quotes),'approved_quotes':sum(q.get('status') in ('approved','issued') for q in quotes),'contracts':len(contracts),'clients':len(self.list('clients')),'recent':recent,'alerts':alerts,'upcoming':upcoming[:8],'pipeline':pipeline,'utilization':money(cost/budget*100) if budget>0 else None,'works_at_risk':len({a['work_id'] for a in alerts if a.get('work_id')})}

    def work_overview(self,work_id,today=None,financial=True):
        """Vista integral de una obra (solo lectura): cronograma, actividades, costos, incidencias, bitácora y horas."""
        today=today or date.today().isoformat()
        work=self.get('works',work_id)
        num=lambda value:amount(value if value not in ('',None) else 0)
        rows={k:self.list(k,work_id=work_id) for k in ('stages','tasks','assignments','timesheets','materials','expenses','income','logs','evidence','progress','incidents','changes')}
        impact=self.change_impact(work,rows['changes'])
        latest={}
        for p in sorted(rows['progress'],key=lambda r:(r.get('date') or '',r.get('updated_at',0))):
            latest[p.get('task_id') or p.get('stage_id') or 'work']=num(p.get('percent'))
        workers={w['id']:w['name'] for w in self.list('workers')}
        stage_names={s['id']:s['name'] for s in rows['stages']}
        def pct(record_id,status):return '100.00' if status=='completed' else money(latest.get(record_id,Decimal(0)))
        tasks=[]
        for t in rows['tasks']:
            done=t.get('status') in ('completed','cancelled')
            tasks.append({'id':t['id'],'name':t['name'],'status':t.get('status') or 'pending','stage_id':t.get('stage_id'),'stage':stage_names.get(t.get('stage_id'),''),'worker':workers.get(t.get('worker_id'),''),'start_date':t.get('start_date'),'due_date':t.get('due_date'),'progress':pct(t['id'],t.get('status')),'overdue':bool(t.get('due_date') and t['due_date']<today and not done)})
        tasks.sort(key=lambda t:(t['due_date'] or '9999',t['name']))
        def stage_pct(s):
            # Avance de etapa: registro explícito o completada; si no, promedio de sus actividades (sin canceladas).
            if s.get('status')=='completed' or s['id'] in latest:return pct(s['id'],s.get('status'))
            own=[Decimal(t['progress']) for t in tasks if t['stage_id']==s['id'] and t['status']!='cancelled']
            return money(sum(own)/len(own)) if own else '0.00'
        stages=[{'id':s['id'],'name':s['name'],'status':s.get('status') or 'pending','start_date':s.get('start_date'),'end_date':s.get('end_date'),'weight':s.get('weight'),'progress':stage_pct(s),'tasks':sum(t['stage_id']==s['id'] for t in tasks)} for s in rows['stages']]
        stages.sort(key=lambda s:(s['start_date'] or '9999',s['name']))
        dates=[d for d in [work.get('start_date'),work.get('end_date'),impact['revised_end'],impact['pending']['end_if_approved']]+[x for s in stages for x in (s['start_date'],s['end_date'])]+[x for t in tasks for x in (t['start_date'],t['due_date'])] if d]
        hours={}
        for r in rows['timesheets']:
            h=hours.setdefault(r['worker_id'],{'worker':workers.get(r['worker_id'],'Trabajador'),'hours':Decimal(0),'cost':Decimal(0),'days':set()})
            h['hours']+=num(r.get('hours'));h['cost']+=num(r.get('cost'));h['days'].add(r.get('date'))
        report=self.report(work_id)['works'][0]
        breakdown={'labor':report['labor'],'materials':report['materials']}
        for category in ('equipment','transport','other'):
            breakdown[category]=money(sum((num(e.get('amount')) for e in rows['expenses'] if (e.get('category') or 'other')==category),Decimal(0)))
        result={'work':work,'client':self.get('clients',work['client_id'])['name'] if work.get('client_id') else '','today':today,
            'range':{'start':min(dates),'end':max(dates)} if dates else None,
            'progress':report['progress'],'hours':report['hours'],'stages':stages,'tasks':tasks,
            'open_tasks':sum(t['status'] not in ('completed','cancelled') for t in tasks),'overdue_tasks':sum(t['overdue'] for t in tasks),
            'incidents':[{'id':i['id'],'name':i['name'],'date':i.get('date'),'status':i.get('status') or 'open'} for i in sorted(rows['incidents'],key=lambda r:r.get('date') or '',reverse=True)],
            'logs':[{'id':l['id'],'date':l.get('date'),'notes':l.get('notes','')} for l in sorted(rows['logs'],key=lambda r:r.get('date') or '',reverse=True)[:5]],
            'team':sorted([{'worker':h['worker'],'hours':str(h['hours']),'cost':money(h['cost']),'days':len(h['days'])} for h in hours.values()],key=lambda x:-Decimal(x['hours'])),
            'counts':{k:len(v) for k,v in rows.items()},'schedule':{k:impact[k] for k in ('original_end','revised_end')}|{'pending_days':impact['pending']['days'],'end_if_approved':impact['pending']['end_if_approved'],'approved_days':impact['approved']['days']},
            # Bandeja de órdenes: pendientes primero y, dentro de cada estado, por exposición económica.
            'changes':sorted([{k:c.get(k) for k in ('id','name','status','requested_by','reason','cost_delta','days_delta','decided_at','decision_comment','created_at')} for c in rows['changes']],key=lambda c:({'pending':0,'draft':1,'approved':2,'rejected':3}.get(c['status'],4),-abs(Decimal(c['cost_delta'] or 0))))}
        if financial:result['change_impact']=impact
        else:
            for c in result['changes']:c.pop('cost_delta')
        if financial:result['financial']={**{k:report[k] for k in ('budget','cost','income','profit','remaining')},'breakdown':breakdown,'utilization':money(Decimal(report['cost'])/Decimal(report['budget'])*100) if Decimal(report['budget'])>0 else None}
        else:
            for member in result['team']:member.pop('cost')
        return result
    @staticmethod
    def _seed_source():
        from .workspace import SOURCE, SOURCE_HASH
        from .core import inspect_xlsm
        batch=inspect_xlsm(SOURCE.read_bytes())
        if batch['hash']!=SOURCE_HASH:raise DomainError('Fuente de precios modificada')
        return SOURCE.name,batch
    @staticmethod
    def _seed_plan(c,batch):
        # Incluye registros borrados lógicamente: una referencia eliminada a propósito no se reimporta.
        existing={json.loads(r[0]).get('source_key') for r in c.execute("SELECT payload FROM erp_records WHERE kind='catalog'")}
        plan={'add':[],'existing':0,'without_price':[]}
        for row in batch['rows']:
            key=batch['hash']+':'+str(row['row'])
            if row['min'] is None:plan['without_price'].append(row['name'])
            elif key in existing:plan['existing']+=1
            else:plan['add'].append({'name':row['name'],'unit':row['unit'],'unit_price':row['min'],'min':row['min'],'max':row['max'],'price_kind':row['kind'],'notes':row['notes'],'source':row['source'],'source_cell':row['cell'],'source_hash':batch['hash'],'source_key':key,'active':row['active'],'scope':row['scope'],'verification':row['verification']})
        return plan
    def seed_preview(self):
        """Read-only: what seed_catalog would add from the verified bundled source."""
        name,batch=self._seed_source()
        with self.store.connect() as c:plan=self._seed_plan(c,batch)
        return {'source':name,'hash':batch['hash'],'rows':len(batch['rows']),'to_add':[{k:r[k] for k in ('name','unit','unit_price','max','price_kind','active')} for r in plan['add']],'existing':plan['existing'],'without_price':plan['without_price'],'note':'Precios de mano de obra en USD. En rangos y valores "desde" se usa el mínimo como precio unitario inicial.'}
    def seed_catalog(self,actor='local-owner'):
        """Import verified bundled source once, retaining source ranges and provenance."""
        _,batch=self._seed_source()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            return [self._save(c,'catalog',row,None,None,actor) for row in self._seed_plan(c,batch)['add']]
    def legacy_list(self):
        """Presupuestos del panel anterior, con su nombre y si ya se pasaron a Cotizaciones."""
        imported={q.get('legacy_project'):q['id'] for q in self.list('quotes') if q.get('legacy_project')}
        with self.store.connect() as c:
            names={r[0]:r[1] for r in c.execute('SELECT project,name FROM project_names')} if c.execute("SELECT 1 FROM sqlite_master WHERE name='project_names'").fetchone() else {}
        return [{'project':b['project'],'name':names.get(b['project'],b['project']),'total':b['budget'].get('total','0.00'),'lines':len(b['budget'].get('lines',[])),'imported_quote_id':imported.get(b['project'])} for b in self.legacy_budgets() if b['budget'].get('lines')]
    def import_legacy(self,project,client_id,actor='local-owner'):
        item=next((b for b in self.legacy_budgets() if b['project']==project),None)
        if not item or not item['budget'].get('lines'):raise DomainError('Presupuesto anterior no encontrado o vacío')
        if any(q.get('legacy_project')==project for q in self.list('quotes')):raise DomainError('Este presupuesto ya se pasó a Cotizaciones')
        if not client_id:raise DomainError('Seleccione el cliente de la cotización')
        name=next((x['name'] for x in self.legacy_list() if x['project']==project),project)
        lines=[{'name':l['name'],'quantity':l['quantity'],'unit':l.get('unit') or 'und','labor':l['unit_price'],'materials':'0','equipment':'0','transport':'0','source':'Presupuesto anterior · tarifa JG '+str(l.get('source_cell',''))} for l in item['budget']['lines']]
        return self.quote_save({'name':name,'client_id':client_id,'lines':lines,'legacy_project':project,'notes':'Importado del panel anterior de presupuestos.'},actor=actor)
    def legacy_budgets(self):
        """Read-only bridge: preserves existing budgets without changing their lifecycle."""
        with self.store.connect() as c:
            return [{'project':r['project'],'source':'legacy_budget','budget':json.loads(r['payload'])} for r in c.execute('SELECT project,payload FROM budgets WHERE project NOT IN (SELECT project FROM project_trash)')]

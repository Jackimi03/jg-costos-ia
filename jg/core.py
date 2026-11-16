"""Domain operations; no network calls or writes to source spreadsheets."""
import hashlib
import io
import json
import re
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from xml.etree import ElementTree as ET

class DomainError(ValueError):
    pass

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def amount(value,positive=False):
    try:
        d=Decimal(str(value))
        if not d.is_finite() or d<0 or d>Decimal('1000000000') or (positive and d==0):
            raise DomainError('Valor fuera de rango')
        return d
    except (InvalidOperation,TypeError):
        raise DomainError('Número inválido') from None

def money(value):
    return str(value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))

def inspect_xlsm(data):
    if len(data)>2_000_000:raise DomainError('Archivo demasiado grande')
    try:
        z=zipfile.ZipFile(io.BytesIO(data))
        if sum(x.file_size for x in z.infolist())>10_000_000:raise DomainError('Archivo expandido demasiado grande')
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        sheets=wb.find('s:sheets',ns)
        if len(sheets)!=1 or sheets[0].get('name')!='Precios referenciales':
            raise DomainError('Plantilla desconocida; revise el mapeo')
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rid=sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        target=next(x.get('Target') for x in rels if x.get('Id')==rid)
        path=target.lstrip('/') if target.startswith('/') else 'xl/'+target
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        cells={}
        for c in ET.fromstring(z.read(path)).findall('.//s:sheetData/s:row/s:c',ns):
            if c.find('s:f',ns) is not None:raise DomainError('Fórmulas en la fuente requieren revisión')
            v=c.find('s:v',ns);value=v.text if v is not None else ''
            if c.get('t')=='s':value=strings[int(value)]
            if c.get('t')=='inlineStr':value=''.join(c.find('s:is',ns).itertext())
            cells[c.get('r')]=value
        expected=['Servicio','Precio referencial (USD)','Unidad','Observaciones / alcance','Base de la referencia']
        if [cells.get(f'{c}7') for c in 'ABCDE']!=expected:raise DomainError('Encabezados desconocidos')
        rows=[]
        for n in range(8,1001):
            if not cells.get(f'A{n}'):continue
            original=cells.get(f'B{n}','')
            lower=re.fullmatch(r'Desde \$(\d+(?:\.\d{1,2})?)',original)
            span=re.fullmatch(r'\$(\d+(?:\.\d{1,2})?)[–-]\$(\d+(?:\.\d{1,2})?)',original)
            if lower:kind='from';lo=lower[1];hi=None
            elif span and amount(span[2])>=amount(span[1]):kind='range';lo,hi=span[1],span[2]
            else:kind='missing';lo=hi=None
            name=cells[f'A{n}']
            rows.append(dict(row=n,cell=f'A{n}:E{n}',sheet='Precios referenciales',name=name,
                original=original,unit=cells.get(f'C{n}',''),min=money(amount(lo)) if lo else None,
                max=money(amount(hi)) if hi else None,kind=kind,currency='USD',
                notes=cells.get(f'D{n}',''),source=cells.get(f'E{n}',''),
                active=name not in ('Facility Management','Steel Framing estructural'),
                scope='labor',verification='owner_confirmed'))
        return dict(hash=hashlib.sha256(data).hexdigest(),rows=rows)
    except (KeyError,ET.ParseError,zipfile.BadZipFile,StopIteration,IndexError):
        raise DomainError('No se pudo leer la plantilla') from None

class Store:
    def __init__(self,path):
        self.path=str(path)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS members(subject TEXT,project TEXT,role TEXT,commercial INTEGER,PRIMARY KEY(subject,project));
            CREATE TABLE IF NOT EXISTS catalogs(project TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS budgets(project TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS project_trash(project TEXT PRIMARY KEY,trashed_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,project TEXT,subject TEXT,payload TEXT,result TEXT);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL,subject TEXT,project TEXT,action TEXT,payload TEXT);
            ''')
    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10)
        c.row_factory=sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()
    def add_member(self,subject,project,role,commercial=False):
        if role not in ('admin','supervisor','estimator','viewer'):raise DomainError('Rol inválido')
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO members VALUES(?,?,?,?)',(subject,project,role,int(commercial)))
            self.log(c,'local-operator',project,'member',{'subject':subject,'role':role,'commercial':bool(commercial)})
    def permission(self,c,subject,project,write=False,admin=False):
        m=c.execute('SELECT * FROM members WHERE subject=? AND project=?',(subject,project)).fetchone()
        trashed=c.execute('SELECT 1 FROM project_trash WHERE project=?',(project,)).fetchone()
        if trashed or not m or (write and m['role']=='viewer') or (admin and m['role']!='admin'):
            raise DomainError('Acceso no autorizado')
        return m
    def log(self,c,user,p,action,payload):
        c.execute('INSERT INTO audit(at,subject,project,action,payload) VALUES(?,?,?,?,?)',(time.time(),user,p,action,canonical(payload)))
    def import_prices(self,user,p,batch,confirmation):
        if confirmation!=batch['hash']:raise DomainError('Confirmación de lote incorrecta')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');self.permission(c,user,p,admin=True)
            old=c.execute('SELECT payload FROM catalogs WHERE project=?',(p,)).fetchone()
            if old:
                if json.loads(old[0])['hash']==batch['hash']:return batch
                raise DomainError('Nueva versión de catálogo requiere migración revisada')
            c.execute('INSERT INTO catalogs VALUES(?,?)',(p,canonical(batch)))
            self.log(c,user,p,'import',batch)
        return batch
    def catalog(self,user,p):
        with self.connect() as c:
            self.permission(c,user,p)
            row=c.execute('SELECT payload FROM catalogs WHERE project=?',(p,)).fetchone()
            return json.loads(row[0])['rows'] if row else []
    def _budget(self,c,p):
        b=c.execute('SELECT payload FROM budgets WHERE project=?',(p,)).fetchone()
        return json.loads(b[0]) if b else dict(version=0,status='draft',lines=[],total='0.00')
    def budget(self,user,p):
        with self.connect() as c:
            self.permission(c,user,p);return self._budget(c,p)
    def preview(self,user,p,version,lines,ttl=600):
        if not lines or len(lines)>200:raise DomainError('Incluya de 1 a 200 partidas')
        catalog={r['row']:r for r in self.catalog(user,p)}
        result=[];total=Decimal(0);seen=set()
        for item in lines:
            row=item['row'];r=catalog.get(row)
            if row in seen:raise DomainError('Servicio duplicado')
            seen.add(row)
            if not r or not r['active'] or r['min'] is None:raise DomainError('Servicio sin precio utilizable')
            q=amount(item['quantity'],True);price=amount(item['unit_price'],True)
            if q>1000000 or q.as_tuple().exponent < -6 or price>1000000:raise DomainError('Cantidad o precio excede precisión permitida')
            if price!=price.quantize(Decimal('.01')):raise DomainError('Precio requiere máximo dos decimales')
            if price<amount(r['min']) or (r['max'] and price>amount(r['max'])):raise DomainError('Precio fuera de referencia')
            subtotal=money(q*price);total+=Decimal(subtotal)
            if total>1000000000:raise DomainError('Presupuesto excede el máximo de desarrollo')
            result.append(dict(row=row,name=r['name'],unit=r['unit'],quantity=str(q),unit_price=money(price),subtotal=subtotal,source_cell=r['cell']))
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');self.permission(c,user,p,write=True)
            before=self._budget(c,p)
            if before['version']!=version:raise DomainError('Versión cambió; vuelva a revisar')
            after=dict(version=version+1,status='draft',lines=result,total=money(total))
            proposal=dict(id=str(uuid.uuid4()),project=p,subject=user,before=before,after=after,expires=time.time()+ttl)
            proposal['hash']=digest(proposal)
            c.execute('INSERT INTO proposals VALUES(?,?,?,?,NULL)',(proposal['id'],p,user,canonical(proposal)))
            self.log(c,user,p,'preview',proposal)
            return proposal
    def _undo_history(self,c,p):
        # Only successful applies affect the stack. Undo consumes a previous save
        # rather than adding its own reversal as a new undoable edit.
        history=[]
        for row in c.execute("SELECT id,payload FROM audit WHERE project=? AND action='apply' ORDER BY id",(p,)):
            proposal=json.loads(row['payload'])
            if 'undo_of' in proposal:
                if not history or history[-1][0]!=proposal['undo_of']:
                    raise DomainError('Historial de cambios inconsistente')
                history.pop()
            else:
                history.append((row['id'],proposal))
        return history
    def apply(self,user,p,proposal_id,confirmation):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');self.permission(c,user,p,write=True)
            row=c.execute('SELECT * FROM proposals WHERE id=? AND project=? AND subject=?',(proposal_id,p,user)).fetchone()
            if not row:raise DomainError('Propuesta inexistente')
            proposal=json.loads(row['payload'])
            if confirmation!=proposal['hash']:raise DomainError('Confirmación incorrecta')
            if row['result']:
                result=json.loads(row['result'])
                if result!=self._budget(c,p):raise DomainError('Propuesta ya aplicada; cargue la versión actual')
                return result
            if proposal['expires']<time.time():raise DomainError('Propuesta vencida')
            if proposal['before']!=self._budget(c,p):raise DomainError('Conflicto; prepare otra propuesta')
            if 'undo_of' in proposal:
                history=self._undo_history(c,p)
                if not history or history[-1][0]!=proposal['undo_of']:
                    raise DomainError('Conflicto; prepare otra propuesta')
            after=proposal['after']
            c.execute('INSERT OR REPLACE INTO budgets VALUES(?,?)',(p,canonical(after)))
            c.execute('UPDATE proposals SET result=? WHERE id=?',(canonical(after),proposal_id))
            self.log(c,user,p,'apply',proposal)
            return after
    def approve(self,user,p,version,evidence):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');m=self.permission(c,user,p,write=True)
            if not m['commercial']:raise DomainError('Solo aprobación comercial autorizada')
            b=self._budget(c,p)
            if b['version']!=version or not b['lines'] or b['status']!='draft':raise DomainError('Se requiere borrador vigente sin aprobar')
            if not evidence.get('photos') or not evidence.get('location') or not evidence.get('scope'):raise DomainError('Faltan fotos, ubicación y alcance')
            b.update(version=version+1,status='approved',approved_by=user,evidence=evidence)
            c.execute('INSERT OR REPLACE INTO budgets VALUES(?,?)',(p,canonical(b)))
            self.log(c,user,p,'approve',b)
            return b

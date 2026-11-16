"""Private persistent attachments and immutable PDF snapshots; optional outbound exports."""
import hashlib
import io
import json
import os
import time
import uuid
from pathlib import Path
from xml.sax.saxutils import escape
from .core import DomainError
from .erp_auth import require

TYPES={'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.txt':'text/plain','.csv':'text/csv','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.webp':'image/webp'}
FILE_KINDS={'attachment','evidence','contract'}  # tipos que un cliente puede pedir al subir; los demás los crea el servidor

def integration_status():
    google=bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS'))
    return {'storage':{'provider':'local','status':'available'},'google_drive':{'configured':google and bool(os.getenv('JG_DRIVE_FOLDER_ID')),'status':'unverified' if google else 'not_configured'},'google_sheets':{'configured':google and bool(os.getenv('JG_SHEET_ID')),'status':'unverified' if google else 'not_configured','mode':'explicit_export'},'openai':{'configured':bool(os.getenv('OPENAI_API_KEY')),'status':'unverified' if os.getenv('OPENAI_API_KEY') else 'not_configured'}}

class DocumentStore:
    def __init__(self,store,root=None):
        self.store=store
        self.root=Path(root or os.getenv('JG_DOCUMENTS_DIR') or Path(store.path).parent/'documents').resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        with store.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS erp_files(id TEXT PRIMARY KEY,filename TEXT NOT NULL,project_id TEXT,kind TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,created_at INTEGER NOT NULL,created_by TEXT NOT NULL,snapshot TEXT,drive_id TEXT)''')
            # Migración aditiva: vínculo del archivo con el registro ERP (B4). No altera filas existentes.
            columns={r[1] for r in c.execute('PRAGMA table_info(erp_files)')}
            for column in ('record_kind','record_id'):
                if column not in columns:c.execute(f'ALTER TABLE erp_files ADD COLUMN {column} TEXT')
            c.execute('CREATE INDEX IF NOT EXISTS erp_files_record ON erp_files(record_id)')
    @staticmethod
    def _meta(row):
        result=dict(row);result['snapshot']=json.loads(result['snapshot']) if result['snapshot'] else None
        return result
    def save(self,actor,filename,data,project_id=None,kind='attachment',snapshot=None,record_kind=None,record_id=None):
        require(actor,'estimate' if kind=='estimate' else 'costs' if kind=='financial_report' else 'write',project_id)
        if kind=='contract':require(actor,'costs',project_id)
        if not isinstance(filename,str) or not filename or len(filename)>180 or Path(filename).name!=filename or '\\' in filename or any(ord(x)<32 for x in filename):raise DomainError('Nombre de archivo inválido')
        ext=Path(filename).suffix.lower()
        if ext not in TYPES:raise DomainError('Formato de archivo no admitido')
        if not isinstance(data,bytes) or not data or len(data)>20*1024*1024:raise DomainError('Archivo vacío o mayor de 20 MB')
        signatures={'.pdf':b'%PDF-', '.png':b'\x89PNG\r\n\x1a\n','.jpg':b'\xff\xd8\xff','.jpeg':b'\xff\xd8\xff','.docx':b'PK\x03\x04'}
        signatures['.xlsx']=b'PK\x03\x04'
        if ext in signatures and not data.startswith(signatures[ext]):raise DomainError('El contenido no corresponde al formato')
        if ext=='.webp' and not (data[:4]==b'RIFF' and data[8:12]==b'WEBP'):raise DomainError('El contenido no corresponde al formato')
        if bool(record_kind)!=bool(record_id):raise DomainError('Indique módulo y registro del archivo')
        ident=uuid.uuid4().hex;path=self.root/ident
        with path.open('xb') as f:f.write(data)
        path.chmod(0o600)
        try:
            with self.store.connect() as c:c.execute('INSERT INTO erp_files(id,filename,project_id,kind,mime,size,sha256,created_at,created_by,snapshot,drive_id,record_kind,record_id) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,?)',(ident,filename,project_id,kind,TYPES[ext],len(data),hashlib.sha256(data).hexdigest(),int(time.time()),actor['id'],json.dumps(snapshot,ensure_ascii=False) if snapshot is not None else None,record_kind,record_id))
        except Exception:
            path.unlink(missing_ok=True);raise
        return self.get(actor,ident)[0]
    def get(self,actor,ident):
        with self.store.connect() as c:row=c.execute('SELECT * FROM erp_files WHERE id=?',(ident,)).fetchone()
        if not row:raise DomainError('Documento no encontrado')
        require(actor,'costs' if row['kind'] in ('estimate','financial_report','contract') else 'read',row['project_id'])
        path=self.root/row['id']
        if not path.is_file() or path.is_symlink():raise DomainError('Archivo no disponible')
        return self._meta(row),path
    def list(self,actor,project_id=None,record_id=None):
        if project_id is not None:require(actor,'read',project_id)
        with self.store.connect() as c:
            if record_id is not None:rows=c.execute('SELECT * FROM erp_files WHERE record_id=? ORDER BY created_at DESC',(record_id,)).fetchall()
            else:rows=c.execute('SELECT * FROM erp_files ORDER BY created_at DESC').fetchall()
        results=[]
        for row in rows:
            if project_id is not None and row['project_id']!=project_id:continue
            try:require(actor,'costs' if row['kind'] in ('estimate','financial_report','contract') else 'read',row['project_id'])
            except DomainError:continue
            results.append(self._meta(row))
        return results
    def pdf(self,actor,title,snapshot,project_id=None,kind='financial_report'):
        require(actor,'costs',project_id)
        try:
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
        except ImportError:raise DomainError('Instale reportlab para exportar PDF') from None
        if not isinstance(snapshot,dict):raise DomainError('El reporte requiere una instantánea estructurada')
        # Preserve source strings/decimals exactly; never recalculate approved amounts.
        frozen=json.loads(json.dumps(snapshot,ensure_ascii=False,allow_nan=False))
        out=io.BytesIO();styles=getSampleStyleSheet();story=[Paragraph(escape(str(title)),styles['Title']),Paragraph('JG Soluciones PA · Documento de gestión · No es factura fiscal',styles['Normal']),Spacer(1,16)]
        def render(value,prefix=''):
            if isinstance(value,dict):
                for key,item in value.items():render(item,f'{prefix} / {key}' if prefix else str(key))
            elif isinstance(value,list):
                for index,item in enumerate(value,1):render(item,f'{prefix} [{index}]')
            else:story.append(Paragraph(escape(f'{prefix}: {value}'),styles['Normal']));story.append(Spacer(1,4))
        render(frozen)
        SimpleDocTemplate(out,pagesize=A4,title=str(title),author='JG Soluciones PA').build(story)
        return self.save(actor,'reporte.pdf',out.getvalue(),project_id,kind,snapshot=frozen)
    def upload_drive(self,actor,ident):
        require(actor,'settings');meta,path=self.get(actor,ident)
        folder=os.getenv('JG_DRIVE_FOLDER_ID')
        if not folder:raise DomainError('Google Drive no configurado: falta carpeta de destino')
        from google.auth import default
        from google.auth.transport.requests import AuthorizedSession
        try:
            credentials,_=default(scopes=['https://www.googleapis.com/auth/drive.file'])
            session=AuthorizedSession(credentials)
            response=session.post('https://www.googleapis.com/upload/drive/v3/files',params={'uploadType':'multipart','fields':'id'},files={'metadata':(None,json.dumps({'name':meta['filename'],'parents':[folder]}),'application/json'),'file':(meta['filename'],path.read_bytes(),meta['mime'])},timeout=60)
            response.raise_for_status();drive_id=response.json()['id']
        except Exception:raise DomainError('No fue posible exportar a Google Drive; revise configuración y permisos') from None
        with self.store.connect() as c:c.execute('UPDATE erp_files SET drive_id=? WHERE id=?',(drive_id,ident))
        return {'id':ident,'drive_id':drive_id}

def export_sheets(actor,rows):
    require(actor,'settings')
    sheet_id=os.getenv('JG_SHEET_ID')
    if not sheet_id:raise DomainError('Google Sheets no configurado')
    if not isinstance(rows,list) or len(rows)>10000 or any(not isinstance(r,list) for r in rows):raise DomainError('Filas de exportación inválidas')
    from google.auth import default
    from google.auth.transport.requests import AuthorizedSession
    from urllib.parse import quote
    try:
        credentials,_=default(scopes=['https://www.googleapis.com/auth/spreadsheets'])
        session=AuthorizedSession(credentials)
        # Append only, RAW prevents formulas from imported text. Never overwrite source data.
        response=session.post('https://sheets.googleapis.com/v4/spreadsheets/'+quote(sheet_id,safe='')+'/values/'+quote('JG_ERP_Export!A1',safe='')+':append',params={'valueInputOption':'RAW','insertDataOption':'INSERT_ROWS'},json={'values':rows},timeout=60)
        response.raise_for_status()
        return {'updated_rows':response.json().get('updates',{}).get('updatedRows',0)}
    except Exception:raise DomainError('No fue posible exportar a Google Sheets; revise configuración y la pestaña JG_ERP_Export') from None

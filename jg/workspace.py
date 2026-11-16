"""Single-owner workspace, trusted by local OS/SSH identity. Not a public login."""
import json
import os
import uuid
from pathlib import Path
from .core import Store, DomainError, inspect_xlsm

OWNER='local-owner'
# Archivo de precios de referencia y su huella verificada (fixtures/source.json → sha256).
# La copia pública trae un archivo de ejemplo con su propio source.json.
_FIXTURES=Path(__file__).resolve().parent.parent/'fixtures'
_META=json.loads((_FIXTURES/'source.json').read_text(encoding='utf-8'))
SOURCE=_FIXTURES/_META['filename']
SOURCE_HASH=_META['sha256']

def setup(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    store=Store(path)
    with store.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS project_names(project TEXT PRIMARY KEY,name TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
        exists=c.execute('SELECT 1 FROM project_names WHERE project=?',('personal',)).fetchone()
    if not exists:create_project(store,'Mi primer presupuesto','personal')
    return store

def create_project(store,name,project=None):
    name=name.strip()
    if not name or len(name)>150:raise DomainError('Indique un nombre de hasta 150 caracteres')
    project=project or 'jg-'+uuid.uuid4().hex
    batch=inspect_xlsm(SOURCE.read_bytes())
    if batch['hash']!=SOURCE_HASH:raise DomainError('La fuente inicial no coincide con la copia verificada')
    # All steps are idempotent; project_names is published last, after the catalog.
    store.add_member(OWNER,project,'admin',True)
    store.import_prices(OWNER,project,batch,batch['hash'])
    with store.connect() as c:c.execute('INSERT OR IGNORE INTO project_names VALUES(?,?)',(project,name))
    return {'id':project,'name':name}

def projects(store):
    with store.connect() as c:
        rows=c.execute('SELECT n.project AS id,n.name FROM project_names n JOIN members m ON m.project=n.project WHERE m.subject=? AND NOT EXISTS (SELECT 1 FROM project_trash t WHERE t.project=n.project) ORDER BY n.rowid',(OWNER,)).fetchall()
        return [dict(r) for r in rows]

def sheet_link(store):
    with store.connect() as c:
        r=c.execute('SELECT value FROM settings WHERE key=?',('sheet_url',)).fetchone()
        return r[0] if r else os.getenv('JG_SHEET_URL')

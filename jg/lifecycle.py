"""Reversible project lifecycle and confirmed budget undo for the local owner."""
import time
import uuid
from .core import DomainError, canonical, digest
from .workspace import OWNER


def _member(store,c,project):
    # Restore intentionally checks membership directly: normal permission rejects trash.
    member=c.execute('SELECT role FROM members WHERE subject=? AND project=?',(OWNER,project)).fetchone()
    exists=c.execute('SELECT 1 FROM project_names WHERE project=?',(project,)).fetchone()
    if not exists or not member or member['role']!='admin':
        raise DomainError('Acceso no autorizado')


def trash_project(store,project):
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        _member(store,c,project)
        changed=c.execute('INSERT OR IGNORE INTO project_trash VALUES(?,?)',(project,time.time())).rowcount
        if changed:store.log(c,OWNER,project,'trash',{'project':project})
        row=c.execute('SELECT n.project AS id,n.name,t.trashed_at FROM project_names n JOIN project_trash t ON t.project=n.project WHERE n.project=?',(project,)).fetchone()
        return dict(row)


def restore_project(store,project):
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        _member(store,c,project)
        changed=c.execute('DELETE FROM project_trash WHERE project=?',(project,)).rowcount
        if changed:store.log(c,OWNER,project,'restore',{'project':project})
        return dict(c.execute('SELECT project AS id,name FROM project_names WHERE project=?',(project,)).fetchone())


def list_trash(store):
    with store.connect() as c:
        return [dict(row) for row in c.execute('SELECT n.project AS id,n.name,t.trashed_at FROM project_names n JOIN project_trash t ON t.project=n.project JOIN members m ON m.project=n.project WHERE m.subject=? AND m.role=? ORDER BY t.trashed_at DESC,n.project',(OWNER,'admin'))]


def prepare_undo(store,project,ttl=600):
    """Preview reversal of the latest successful apply; Store.apply commits it."""
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        store.permission(c,OWNER,project,write=True)
        before=store._budget(c,project)
        history=store._undo_history(c,project)
        if not history:raise DomainError('No hay cambios guardados para deshacer')
        target,original=history[-1]
        previous=original['before']
        # Construct a fresh draft, never revive approval or evidence from history.
        after=dict(version=before['version']+1,status='draft',lines=previous['lines'],total=previous['total'])
        proposal=dict(id=str(uuid.uuid4()),project=project,subject=OWNER,before=before,after=after,expires=time.time()+ttl)
        proposal['undo_of']=target
        proposal['hash']=digest(proposal)
        c.execute('INSERT INTO proposals VALUES(?,?,?,?,NULL)',(proposal['id'],project,OWNER,canonical(proposal)))
        store.log(c,OWNER,project,'preview',proposal)
        return proposal

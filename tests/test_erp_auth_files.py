import tempfile
import unittest
from pathlib import Path
from jg.core import Store, DomainError
from jg.erp_auth import Auth, owner, require
from jg.erp_files import DocumentStore, integration_status

class IdentityDocumentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.db');self.auth=Auth(self.store)
    def test_password_session_and_revocation(self):
        u=self.auth.create_user(owner(),'ana','strong-password','foreman',projects=['p1'])
        login=self.auth.login('ana','strong-password');self.assertEqual(self.auth.authenticate(login['token'])['id'],u['id'])
        with self.store.connect() as c:
            self.assertNotIn(login['token'],str([tuple(r) for r in c.execute('SELECT * FROM erp_sessions')]))
            self.assertNotIn('strong-password',str(tuple(c.execute('SELECT * FROM erp_users').fetchone())))
        self.auth.update_user(owner(),u['id'],active=False)
        with self.assertRaises(DomainError):self.auth.authenticate(login['token'])
    def test_roles_and_projects(self):
        worker=self.auth.create_user(owner(),'worker','strong-password','worker',projects=['p1'])
        require(worker,'read','p1')
        for action,project in [('read','p2'),('costs','p1'),('team',None),('unknown','p1')]:
            with self.assertRaises(DomainError):require(worker,action,project)
        with self.assertRaises(DomainError):self.auth.login('worker','wrong-password')
    def test_secure_document_and_snapshot(self):
        docs=DocumentStore(self.store,Path(self.tmp.name)/'documents')
        with self.assertRaises(DomainError):docs.save(owner(),'../evil.pdf',b'%PDF-x')
        doc=docs.save(owner(),'contract.pdf',b'%PDF-1.4\nexample','p1')
        meta,path=docs.get(owner(),doc['id']);self.assertEqual(path.read_bytes(),b'%PDF-1.4\nexample')
        worker=self.auth.create_user(owner(),'worker','strong-password','worker',projects=['p2'])
        with self.assertRaises(DomainError):docs.get(worker,doc['id'])
        result=docs.pdf(owner(),'Cotización',{'total':'123.45','lines':[{'name':'A & B','amount':'123.45'}]},'p1')
        meta,path=docs.get(owner(),result['id']);self.assertTrue(path.read_bytes().startswith(b'%PDF'))
        self.assertEqual(meta['snapshot']['total'],'123.45')
    def test_files_linked_to_record_and_legacy_table_migrated(self):
        # Tabla con el esquema anterior (sin record_kind/record_id) y una fila existente.
        with self.store.connect() as c:
            c.execute('CREATE TABLE erp_files(id TEXT PRIMARY KEY,filename TEXT NOT NULL,project_id TEXT,kind TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,created_at INTEGER NOT NULL,created_by TEXT NOT NULL,snapshot TEXT,drive_id TEXT)')
            c.execute("INSERT INTO erp_files VALUES('old','viejo.pdf',NULL,'attachment','application/pdf',6,'x',1,'local-owner',NULL,NULL)")
        docs=DocumentStore(self.store,Path(self.tmp.name)/'documents')
        DocumentStore(self.store,Path(self.tmp.name)/'documents')  # segunda inicialización no falla
        (Path(self.tmp.name)/'documents'/'old').write_bytes(b'%PDF-1')
        a=docs.save(owner(),'a.pdf',b'%PDF-a',record_kind='contracts',record_id='c1',kind='contract')
        b=docs.save(owner(),'b.png',b'\x89PNG\r\n\x1a\nxx',record_kind='contracts',record_id='c1')
        docs.save(owner(),'otro.pdf',b'%PDF-o',record_kind='contracts',record_id='c2')
        self.assertEqual({f['id'] for f in docs.list(owner(),record_id='c1')},{a['id'],b['id']})
        self.assertEqual(a['record_kind'],'contracts')
        self.assertIsNone(docs.get(owner(),'old')[0]['record_id'])
        docs.save(owner(),'hoja.xlsx',b'PK\x03\x04x');docs.save(owner(),'foto.webp',b'RIFF\x00\x00\x00\x00WEBPx')
        for name,data in [('falsa.webp',b'%PDF-x'),('falsa.xlsx',b'%PDF-x')]:
            with self.assertRaises(DomainError):docs.save(owner(),name,data)
        with self.assertRaises(DomainError):docs.save(owner(),'x.pdf',b'%PDF-x',record_kind='contracts')

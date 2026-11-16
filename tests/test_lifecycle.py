import tempfile
import unittest
from pathlib import Path
from jg.core import DomainError, Store
from jg.workspace import setup, projects, OWNER
from jg.lifecycle import trash_project, restore_project, list_trash, prepare_undo
try:
    from tests.precios import requires_real_prices
except ImportError:
    from precios import requires_real_prices

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'db.sqlite'
        self.s=setup(self.path)
        self.p='personal'
    def tearDown(self):self.tmp.cleanup()
    def preview(self,quantity='1'):
        return self.s.preview(OWNER,self.p,self.s.budget(OWNER,self.p)['version'],[dict(row=8,quantity=quantity,unit_price='10.50')])
    def apply(self,p):return self.s.apply(OWNER,self.p,p['id'],p['hash'])
    def test_unknown_and_unauthorized_lifecycle(self):
        for operation in [trash_project,restore_project]:
            with self.assertRaises(DomainError):operation(self.s,'missing')
        with self.s.connect() as c:c.execute('DELETE FROM members WHERE subject=?',(OWNER,))
        with self.assertRaises(DomainError):trash_project(self.s,self.p)
    def test_no_history_and_idempotent_legacy_migration(self):
        with self.assertRaises(DomainError):prepare_undo(self.s,self.p)
        with self.s.connect() as c:c.execute('DROP TABLE project_trash')
        Store(self.path);Store(self.path)
        trash_project(self.s,self.p)
        self.assertEqual(len(list_trash(self.s)),1)



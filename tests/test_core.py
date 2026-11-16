import unittest
import tempfile
import io
import zipfile
from pathlib import Path
from jg.core import Store, DomainError, inspect_xlsm
try:
    from tests.precios import requires_real_prices
except ImportError:
    from precios import requires_real_prices

SOURCE=Path(__file__).resolve().parents[1]/'fixtures/JG_Precios_Referenciales.xlsm'

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.s=Store(Path(self.tmp.name)/'test.db')
        self.s.add_member('propietario','p1','admin',True)
        self.s.add_member('analista','p1','estimator')
        self.s.add_member('lector','p1','viewer')
        self.batch=inspect_xlsm(SOURCE.read_bytes())
        self.s.import_prices('propietario','p1',self.batch,self.batch['hash'])
    def tearDown(self): self.tmp.cleanup()
    def test_import_repeated_does_not_duplicate(self):
        self.s.import_prices('propietario','p1',self.batch,self.batch['hash'])
        self.assertEqual(len(self.s.catalog('analista','p1')),21)
    def test_bounds_keep_spreadsheet_values_representable(self):
        for q,price in [('0.0000001','10.50'),('1000001','10.50'),('1000000','10000')]:
            with self.assertRaises(DomainError):self.s.preview('analista','p1',0,[{'row':8,'quantity':q,'unit_price':price}])
    def test_conflict_hash_expiry_and_revocation(self):
        p=self.s.preview('analista','p1',0,[{'row':10,'quantity':'50','unit_price':'7'}])
        with self.assertRaises(DomainError):self.s.apply('analista','p1',p['id'],'other')
        q=self.s.preview('analista','p1',0,[{'row':10,'quantity':'70','unit_price':'7'}])
        self.s.apply('analista','p1',p['id'],p['hash'])
        with self.assertRaises(DomainError):self.s.apply('analista','p1',q['id'],q['hash'])
        p=self.s.preview('analista','p1',1,[{'row':10,'quantity':'50','unit_price':'7'}],ttl=-1)
        with self.assertRaises(DomainError):self.s.apply('analista','p1',p['id'],p['hash'])
        p=self.s.preview('analista','p1',1,[{'row':10,'quantity':'50','unit_price':'7'}])
        self.s.add_member('analista','p1','viewer')
        with self.assertRaises(DomainError):self.s.apply('analista','p1',p['id'],p['hash'])
    def test_forbidden_roles_projects_and_invalid_prices(self):
        for user,project in [('lector','p1'),('analista','p2'),('intruso','p1')]:
            with self.assertRaises(DomainError):self.s.preview(user,project,0,[])
        for row,qty,price in [(8,'NaN','10.50'),(8,'-1','10.50'),(8,'1','0'),(8,'1','Infinity'),(16,'10','150'),(11,'1','13')]:
            with self.subTest(row=row,qty=qty,price=price):
                with self.assertRaises(DomainError):self.s.preview('analista','p1',0,[{'row':row,'quantity':qty,'unit_price':price}])

if __name__=='__main__':unittest.main()

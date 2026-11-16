import tempfile
import unittest
import os
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
from jg.api import create_app
from jg.core import Store
try:
    from tests.precios import requires_real_prices
except ImportError:
    from precios import requires_real_prices

SOURCE=Path(__file__).resolve().parents[1]/'fixtures/JG_Precios_Referenciales.xlsm'

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'ENABLE_OPENAI':'false'});self.env.start()
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'test.db')
        self.store.add_member('owner','demo','admin',True)
        self.store.add_member('reader','demo','viewer')
        def verify(token):
            if token not in ('owner','reader'):raise ValueError('bad token')
            return token
        self.client=TestClient(create_app(self.store,verify))
        self.h={'Authorization':'Bearer owner'}
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def test_fail_closed_identity_and_project_access(self):
        self.assertEqual(self.client.get('/projects/demo/budget').status_code,401)
        self.assertEqual(self.client.get('/projects/demo/budget',headers={'Authorization':'Bearer bad'}).status_code,401)
        self.assertEqual(self.client.get('/projects/other/budget',headers=self.h).status_code,409)
    def test_identity_does_not_accept_subject_from_request(self):
        self.assertEqual(self.client.get('/identity?subject=intruso',headers=self.h).json(),{'subject':'owner'})
    def test_ai_disabled_without_config_does_not_charge(self):
        self.assertEqual(self.client.post('/projects/demo/suggest',json={'prompt':'pintura'},headers=self.h).status_code,503)
    def test_ai_invalid_service_is_not_used_or_applied(self):
        from jg.core import inspect_xlsm
        self.store.import_prices('owner','demo',inspect_xlsm(SOURCE.read_bytes()),inspect_xlsm(SOURCE.read_bytes())['hash'])
        class Response:
            def raise_for_status(self):pass
            def json(self):return {'status':'completed','output':[{'content':[{'type':'output_text','text':'{"items":[{"row":999,"quantity":"20"}]}'}]}]}
        with patch.dict(os.environ,{'ENABLE_OPENAI':'true','OPENAI_API_KEY':'fake-for-test','OPENAI_MODEL':'configured-model'}),patch('jg.api.httpx.post',return_value=Response()):
            self.assertEqual(self.client.post('/projects/demo/suggest',json={'prompt':'pintura 20 m²'},headers=self.h).status_code,502)
        self.assertEqual(self.store.budget('owner','demo')['version'],0)

if __name__=='__main__':unittest.main()

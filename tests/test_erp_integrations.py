import json
import os
import unittest
from unittest.mock import patch
from jg import erp_integrations as integrations
from jg.core import DomainError

CATALOG=[{'id':'c1','name':'Pintura interior','unit':'m²','unit_price':'7.00'}]

class FakeResponse:
    status_code=200
    def __init__(self,items):self.items=items
    def json(self):
        text=json.dumps({'message':'ok','items':self.items,'assumptions':[]})
        return {'output':[{'content':[{'type':'output_text','text':text}]}]}

class FakeClient:
    calls=[]
    def __init__(self,items):self.items=items
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def post(self,url,headers,json):FakeClient.calls.append(json['model']);return FakeResponse(self.items)

def env(**values):
    clean={k:v for k,v in os.environ.items() if k not in ('ENABLE_OPENAI','OPENAI_API_KEY','OPENAI_MODEL','JG_OPENAI_MODEL')}
    return patch.dict(os.environ,{**clean,**values},clear=True)

class OpenAIConfigTests(unittest.TestCase):
    """Defecto B7: el ERP debe usar la misma configuración que el resto del proyecto."""
    def test_disabled_by_default_even_with_key(self):
        with env(OPENAI_API_KEY='fake-for-test',OPENAI_MODEL='modelo'):
            self.assertFalse(integrations.status()['openai']['enabled'])
            with self.assertRaisesRegex(DomainError,'ENABLE_OPENAI'):integrations.suggest('pintar 10 m2',CATALOG)
    def test_enabled_requires_key_and_standard_model_variable(self):
        with env(ENABLE_OPENAI='true',OPENAI_API_KEY='fake-for-test'):
            self.assertEqual(integrations.status()['openai']['model_configured'],False)
            with self.assertRaisesRegex(DomainError,'OPENAI_MODEL'):integrations.suggest('pintar 10 m2',CATALOG)
    def test_uses_openai_model_and_legacy_name_as_fallback(self):
        FakeClient.calls.clear()
        with env(ENABLE_OPENAI='true',OPENAI_API_KEY='fake-for-test',OPENAI_MODEL='modelo-std',JG_OPENAI_MODEL='modelo-viejo'),patch.object(integrations.httpx,'Client',lambda timeout:FakeClient([{'catalog_id':'c1','quantity':'10','notes':''}])):
            self.assertEqual(integrations.suggest('pintar 10 m2',CATALOG)['items'][0]['catalog_id'],'c1')
            status=integrations.status()['openai']
            self.assertTrue(status['enabled'] and status['configured'])
            self.assertNotIn('fake-for-test',json.dumps(integrations.status()))  # nunca expone la clave
        with env(ENABLE_OPENAI='true',OPENAI_API_KEY='fake-for-test',JG_OPENAI_MODEL='modelo-viejo'),patch.object(integrations.httpx,'Client',lambda timeout:FakeClient([])):
            integrations.suggest('pintar',CATALOG)
        self.assertEqual(FakeClient.calls,['modelo-std','modelo-viejo'])
    def test_rejects_unknown_catalog_reference(self):
        with env(ENABLE_OPENAI='true',OPENAI_API_KEY='fake-for-test',OPENAI_MODEL='m'),patch.object(integrations.httpx,'Client',lambda timeout:FakeClient([{'catalog_id':'inventado','quantity':'1','notes':''}])):
            with self.assertRaisesRegex(DomainError,'inexistente'):integrations.suggest('x',CATALOG)

if __name__=='__main__':unittest.main()

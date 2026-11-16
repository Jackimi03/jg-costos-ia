import tempfile
import unittest
from pathlib import Path
from jg.workspace import setup,OWNER
from jg.assistant import enqueue,claim,finish,cancel,get_job,heartbeat,ai_status
try:
    from tests.precios import requires_real_prices
except ImportError:
    from precios import requires_real_prices

class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=setup(Path(self.tmp.name)/'ai.db')
    def tearDown(self):self.tmp.cleanup()
    def plan(self):return {'message':'Pintura a dos manos.','lines':[{'activity':'Pintura interior','row':10,'quantity':'70'}],'resources':[{'activity':'Pintura interior','row':10,'name':'Pintura','category':'material','unit':'L','coefficient_per_unit':'0.2','waste_percent':'10','package_size':'3.8','unit_price':None,'source':'Estimado IA'}],'assumptions':['Dos manos y rendimiento 10 m²/L por mano.'],'questions':[]}
    def test_cancelled_job_cannot_complete(self):
        j=enqueue(self.s,'personal','pintura');claim(self.s);cancel(self.s,j['id'])
        with self.assertRaises(ValueError):finish(self.s,j['id'],self.plan())
        self.assertEqual(get_job(self.s,j['id'])['state'],'cancelled')
    def test_rejects_unknown_services_and_invented_resource_prices(self):
        j=enqueue(self.s,'personal','pintura');claim(self.s)
        p=self.plan();p['lines'][0]['row']=999
        with self.assertRaises(ValueError):finish(self.s,j['id'],p)
        p=self.plan();p['resources'][0]['unit_price']='25'
        with self.assertRaises(ValueError):finish(self.s,j['id'],p)
    def test_stale_response_is_not_applied(self):
        j=enqueue(self.s,'personal','pintura');claim(self.s)
        p=self.s.preview(OWNER,'personal',0,[{'row':10,'quantity':'1','unit_price':'7'}]);self.s.apply(OWNER,'personal',p['id'],p['hash'])
        with self.assertRaises(ValueError):finish(self.s,j['id'],self.plan())
    def test_provider_units_and_invalid_coefficients_have_controlled_errors(self):
        from jg.core import DomainError
        from jg.assistant import AI_SCHEMA
        self.assertIn('L',AI_SCHEMA['properties']['resources']['items']['properties']['unit']['enum'])
        j=enqueue(self.s,'personal','Pintura');claim(self.s)
        p=self.plan();p['resources'][0]['unit']='litros'
        with self.assertRaises(DomainError):finish(self.s,j['id'],p)
        p=self.plan();p['resources'][0]['coefficient_per_unit']='0'
        with self.assertRaises(DomainError):finish(self.s,j['id'],p)
    def test_connection_heartbeat(self):
        self.assertFalse(ai_status(self.s)['connected']);heartbeat(self.s)
        self.assertTrue(ai_status(self.s)['connected'])
    def test_plain_decimal_quantity_and_multiple_unpriced_activities(self):
        j=enqueue(self.s,'personal','Dos actividades sin tarifa');claim(self.s)
        p=self.plan();p['lines']=[{'activity':'Muro','row':None,'quantity':'1e-15'}];p['resources']=[]
        with self.assertRaises(ValueError):finish(self.s,j['id'],p)
        p['lines']=[{'activity':'Muro','row':None,'quantity':'10'},{'activity':'Piso','row':None,'quantity':'20'}]
        p['resources']=[{**self.plan()['resources'][0],'row':None,'activity':'Muro'},{**self.plan()['resources'][0],'row':None,'activity':'Piso'}]
        result=finish(self.s,j['id'],p)['result']
        self.assertEqual(len(result['takeoffs']),2)
        self.assertIsNone(result['proposal'])
    def test_followup_question_does_not_hide_saved_takeoff(self):
        from jg.assistant import latest_result
        j=enqueue(self.s,'personal','Pintura');claim(self.s);r=finish(self.s,j['id'],self.plan())['result']
        p=r['proposal'];self.s.apply(OWNER,'personal',p['id'],p['hash'])
        j=enqueue(self.s,'personal','¿Qué falta?');claim(self.s)
        finish(self.s,j['id'],{'message':'Falta revisar.','lines':[],'resources':[],'assumptions':[],'questions':['¿Marca de pintura?']})
        current=latest_result(self.s,'personal')
        self.assertEqual(current['message'],'Falta revisar.')
        self.assertEqual(len(current['takeoffs']),1)
        self.assertTrue(current['applied'])

if __name__=='__main__':unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from jg.personal import create_personal_app

class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.client=TestClient(create_personal_app(Path(self.tmp.name)/'personal.db'),base_url='http://127.0.0.1:8765')
    def tearDown(self):self.tmp.cleanup()
    def test_private_panel_and_catalog(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/projects/personal/catalog').status_code,401)
        nonce=self.client.get('/session',headers={'Origin':'http://127.0.0.1:8765'}).json()['token']
        h={'Authorization':'Bearer '+nonce,'Origin':'http://127.0.0.1:8765'}
        self.assertEqual(len(self.client.get('/projects/personal/catalog',headers=h).json()),21)
        self.assertEqual(self.client.get('/projects/personal/budget',headers=h).json()['version'],0)
    def test_work_overview_hides_amounts_without_cost_permission(self):
        owner_h={'Authorization':'Bearer '+self.client.get('/session').json()['token']}
        work=self.client.post('/erp/records/works',json={'name':'Obra','budget':'500'},headers=owner_h).json()
        self.client.post('/erp/records/expenses',json={'name':'G','work_id':work['id'],'amount':'50'},headers=owner_h)
        full=self.client.get(f"/erp/works/{work['id']}/overview",headers=owner_h).json()
        self.assertEqual(full['financial']['cost'],'50.00')
        created=self.client.post('/erp/users',json={'username':'capataz','password':'clave-segura-123','role':'foreman','projects':[work['id']]},headers=owner_h)
        self.assertEqual(created.status_code,200)
        other=self.client.post('/erp/records/works',json={'name':'Ajena'},headers=owner_h).json()
        with patch.dict(os.environ,{'JG_TEAM_MODE':'1'}):
            token=self.client.post('/erp/auth/login',json={'username':'capataz','password':'clave-segura-123'}).json()['token']
            h={'Authorization':'Bearer '+token}
            limited=self.client.get(f"/erp/works/{work['id']}/overview",headers=h)
            self.assertEqual(limited.status_code,200);self.assertNotIn('financial',limited.json())
            self.assertGreaterEqual(self.client.get(f"/erp/works/{other['id']}/overview",headers=h).status_code,400)  # obra no asignada
    def test_change_order_decision_requires_admin(self):
        owner_h={'Authorization':'Bearer '+self.client.get('/session').json()['token']}
        work=self.client.post('/erp/records/works',json={'name':'Obra','budget':'500'},headers=owner_h).json()
        self.client.post('/erp/users',json={'username':'coordina','password':'clave-segura-123','role':'coordinator','projects':[work['id']]},headers=owner_h)
        with patch.dict(os.environ,{'JG_TEAM_MODE':'1'}):
            h={'Authorization':'Bearer '+self.client.post('/erp/auth/login',json={'username':'coordina','password':'clave-segura-123'}).json()['token']}
            change=self.client.post('/erp/records/changes',json={'name':'Adicional','work_id':work['id'],'cost_delta':'80','status':'pending'},headers=h)
            self.assertEqual(change.status_code,200);change=change.json()
            denied=self.client.post(f"/erp/changes/{change['id']}/decide",json={'version':change['version'],'decision':'approved'},headers=h)
            self.assertGreaterEqual(denied.status_code,400)
            self.assertGreaterEqual(self.client.put(f"/erp/records/changes/{change['id']}",json={'version':change['version'],'status':'approved'},headers=h).status_code,400)
        ok=self.client.post(f"/erp/changes/{change['id']}/decide",json={'version':change['version'],'decision':'approved','comment':'OK'},headers=owner_h)
        self.assertEqual(ok.json()['status'],'approved')
        self.assertEqual(self.client.get(f"/erp/works/{work['id']}/overview",headers=owner_h).json()['financial']['budget'],'580.00')
    def test_issued_quote_pdf_is_stored_once_and_preview_is_marked(self):
        h={'Authorization':'Bearer '+self.client.get('/session').json()['token']}
        client=self.client.post('/erp/records/clients',json={'name':'Cliente Original'},headers=h).json()
        q=self.client.post('/erp/quotes',json={'name':'Q','client_id':client['id'],'zone':'ciudad_panama','lines':[{'name':'L','quantity':'2','labor':'10'}]},headers=h).json()
        preview=self.client.get(f"/erp/quotes/{q['id']}/pdf",headers=h)
        self.assertEqual((preview.status_code,preview.headers['content-type']),(200,'application/pdf'))
        self.assertEqual(self.client.get('/erp/files',headers=h).json(),[])  # la vista previa no se guarda
        q=self.client.post(f"/erp/quotes/{q['id']}/approve",json={'version':q['version']},headers=h).json()
        doc=self.client.post(f"/erp/quotes/{q['id']}/issue",json={'version':q['version']},headers=h).json()
        first=self.client.get(f"/erp/documents/{doc['id']}/pdf",headers=h).content
        # Cambiar el cliente después de emitir no altera el documento emitido
        client=self.client.get(f"/erp/records/clients/{client['id']}",headers=h).json()
        self.client.put(f"/erp/records/clients/{client['id']}",json={'version':client['version'],'name':'Cliente Renombrado'},headers=h)
        second=self.client.get(f"/erp/documents/{doc['id']}/pdf",headers=h).content
        self.assertTrue(first.startswith(b'%PDF'));self.assertEqual(first,second)
        stored=[f for f in self.client.get('/erp/files',params={'record_id':doc['id']},headers=h).json() if f['kind']=='estimate']
        self.assertEqual(len(stored),1);self.assertEqual(stored[0]['filename'],doc['number']+'.pdf')
    def test_ai_key_never_leaves_server_and_company_form(self):
        h={'Authorization':'Bearer '+self.client.get('/session').json()['token']}
        key='sk-test'+'Z'*30+'9876'
        with patch.dict(os.environ,{'ENABLE_OPENAI':'false'}):
            saved=self.client.put('/erp/ai/config',json={'api_key':key,'model':'gpt-test','enabled':True},headers=h)
            self.assertEqual(saved.status_code,200);self.assertEqual(saved.json()['key_hint'],'…9876')
            for path in ('/erp/ai/config','/erp/integrations'):self.assertNotIn(key,self.client.get(path,headers=h).text)
            self.assertTrue(self.client.get('/erp/ai/config',headers=h).json()['enabled'])  # la configuración del panel manda sobre ENABLE_OPENAI=false
            self.assertEqual(self.client.delete('/erp/ai/config',headers=h).json()['key_configured'],False)
        put=self.client.put('/erp/company',json={'ruc':'8-888-1234','dv':'56'},headers=h).json()
        self.assertEqual({f['key']:f['value'] for f in put['fields']}['dv'],'56')
        self.assertGreaterEqual(self.client.put('/erp/company',json={'dv':'999'},headers=h).status_code,400)
    def test_erp_catalog_seed_routes(self):
        h={'Authorization':'Bearer '+self.client.get('/session').json()['token']}
        preview=self.client.get('/erp/catalog/seed',headers=h).json()
        self.assertEqual(len(preview['to_add']),8)
        self.assertEqual(self.client.get('/erp/records/catalog',headers=h).json(),[])
        self.assertEqual(self.client.post('/erp/catalog/seed',headers=h).json()['added'],8)
        self.assertEqual(self.client.post('/erp/catalog/seed',headers=h).json()['added'],0)
        self.assertEqual(len(self.client.get('/erp/records/catalog',headers=h).json()),8)
        self.assertGreaterEqual(self.client.post('/erp/catalog/seed').status_code,400)  # sin sesión: rechazado
    def test_erp_attachments_per_record(self):
        # Defecto B4: los adjuntos deben quedar vinculados al registro, no al último file_id.
        h={'Authorization':'Bearer '+self.client.get('/session').json()['token']}
        client=self.client.post('/erp/records/clients',json={'name':'C'},headers=h).json()
        work=self.client.post('/erp/records/works',json={'name':'Obra'},headers=h).json()
        contract=self.client.post('/erp/records/contracts',json={'name':'K','client_id':client['id'],'work_id':work['id']},headers=h).json()
        other=self.client.post('/erp/records/contracts',json={'name':'K2','client_id':client['id']},headers=h).json()
        up=lambda rec,name,kind='contracts':self.client.post('/erp/files',headers=h,data={'record_kind':kind,'record_id':rec['id'],'work_id':'falsa','kind':'estimate'},files={'file':(name,b'%PDF-1.4 x','application/pdf')})
        first=up(contract,'uno.pdf').json();second=up(contract,'dos.pdf').json();up(other,'otro.pdf')
        self.assertEqual((first['kind'],first['project_id'],first['record_id']),('contract',work['id'],contract['id']))
        listed=self.client.get('/erp/files',params={'record_id':contract['id']},headers=h).json()
        self.assertEqual({f['filename'] for f in listed},{'uno.pdf','dos.pdf'})
        self.assertEqual(self.client.get('/erp/records/contracts/'+contract['id'],headers=h).json()['version'],1)  # subir no versiona el registro
        self.assertEqual(self.client.get(f"/erp/files/{second['id']}/download",headers=h).content,b'%PDF-1.4 x')
        for bad in [up(contract,'x.pdf','quotes'),up({'id':'inexistente'},'x.pdf'),
                    self.client.post('/erp/files',headers=h,data={'kind':'financial_report'},files={'file':('x.pdf',b'%PDF-x','application/pdf')})]:
            self.assertGreaterEqual(bad.status_code,400)
    def test_session_rejects_foreign_origin_and_dns_rebinding(self):
        self.assertEqual(self.client.get('/session',headers={'Origin':'https://evil.invalid'}).status_code,403)
        self.assertEqual(self.client.get('/session',headers={'Host':'evil.invalid'}).status_code,403)
    def test_panel_requires_preview_before_save(self):
        token=self.client.get('/session').json()['token'];h={'Authorization':'Bearer '+token}
        p=self.client.post('/projects/personal/preview',headers=h,json={'version':0,'lines':[{'row':10,'quantity':'70','unit_price':'7'}]}).json()
        self.assertEqual(p['after']['total'],'490.00')
        self.assertEqual(self.client.get('/projects/personal/budget',headers=h).json()['version'],0)
        self.assertEqual(self.client.post('/projects/personal/apply',headers=h,json={'id':p['id'],'hash':p['hash']}).json()['total'],'490.00')

    def test_ai_queue_cancel_and_reversible_trash_routes(self):
        h={'Authorization':'Bearer '+self.client.get('/session').json()['token']}
        project=self.client.post('/workspace/projects',headers=h,json={'name':'Prueba aislada'}).json()['id']
        job=self.client.post('/projects/'+project+'/ai',headers=h,json={'prompt':'Calcule pintura para 70 m2'}).json()
        self.assertEqual(job['state'],'queued')
        self.assertEqual(self.client.post('/ai/worker/claim',headers=h,json={}).json()['id'],job['id'])
        self.assertEqual(self.client.post('/ai/jobs/'+job['id']+'/cancel',headers=h,json={}).json()['state'],'cancelled')
        self.assertEqual(self.client.post('/workspace/projects/'+project+'/trash',headers=h,json={}).status_code,200)
        workspace=self.client.get('/workspace',headers=h).json()
        self.assertIn(project,[p['id'] for p in workspace['trash']])
        self.assertNotIn(project,[p['id'] for p in workspace['projects']])
        self.assertEqual(self.client.post('/workspace/projects/'+project+'/restore',headers=h,json={}).status_code,200)
        self.assertEqual(self.client.get('/projects/'+project+'/budget',headers=h).json()['version'],0)

if __name__=='__main__':unittest.main()

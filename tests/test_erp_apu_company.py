import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from jg.core import Store, DomainError
from jg.erp import ERP, parse_location, zone_from_coords, detect_zone
from jg import erp_integrations as ai


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status; self._body = body or {}
    def json(self):
        return self._body


class FakeClient:
    def __init__(self, response):
        self.response = response; self.calls = []
    def __call__(self, timeout):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def post(self, url, headers, json):
        self.calls.append((url, json)); return self.response
    def get(self, url, headers):
        self.calls.append((url, None)); return self.response


def responses_body(obj):
    return {'output': [{'content': [{'type': 'output_text', 'text': json.dumps(obj)}]}]}


class CompanyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.erp = ERP(Store(Path(self.tmp.name) / 't.db'))

    def test_company_form_saves_ruc_dv_and_reuses_existing_settings(self):
        old = self.erp.save('settings', {'name': 'Empresa', 'value': 'JG'})  # parámetro creado antes con otro nombre
        view = self.erp.save_company({'name': 'JG Soluciones PA', 'ruc': '8-888-1234', 'dv': '56', 'address': 'Arraiján', 'email': 'a@b.com', 'monthly_fixed_costs': '40'})
        values = {f['key']: f['value'] for f in view['fields']}
        self.assertEqual((values['name'], values['ruc'], values['dv'], values['monthly_fixed_costs']), ('JG Soluciones PA', '8-888-1234', '56', '40'))
        self.assertEqual(self.erp.get('settings', old['id'])['value'], 'JG Soluciones PA')  # se actualizó, no se duplicó
        self.assertEqual(sum(1 for r in self.erp.list('settings') if r['name'] in ('Empresa', 'Nombre de la empresa')), 1)
        self.assertEqual(len(view['managed_setting_ids']), 6)
        for bad in [{'dv': '123'}, {'dv': 'x'}, {'ruc': '8 888'}, {'email': 'sin-arroba'}, {'margin_high': '150'}, {'otro': '1'}]:
            with self.assertRaises(DomainError):
                self.erp.save_company(bad)
        self.erp.save_company({'dv': ''})
        self.assertNotIn('dv', self.erp.company_profile())


class ApuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.erp = ERP(Store(Path(self.tmp.name) / 't.db'))
        self.client = self.erp.save('clients', {'name': 'C'})

    def test_apu_drives_line_costs_and_recalculates(self):
        apu = {'resources': [
            {'category': 'material', 'name': 'Pintura', 'unit': 'gal', 'quantity_per_unit': '0.1', 'waste_percent': '10', 'unit_price': '20', 'source': 'estimate'},
            {'category': 'labor', 'name': 'Pintor JG', 'unit': 'm²', 'quantity_per_unit': '1', 'waste_percent': '0', 'unit_price': '7.00', 'source': 'catalog'},
            {'category': 'equipment', 'name': 'Rodillo', 'unit': 'und', 'quantity_per_unit': '0.02', 'waste_percent': '0', 'unit_price': '5', 'source': 'estimate'}]}
        line = {'name': 'Pintura interior', 'quantity': '70', 'unit': 'm²', 'materials': '999', 'labor': '999', 'equipment': '999', 'apu': apu}
        q = self.erp.quote_save({'name': 'Q', 'client_id': self.client['id'], 'zone': 'panama_oeste', 'indirect_percent': '0', 'margin_percent': '0', 'lines': [line]})
        l = q['lines'][0]
        self.assertEqual((l['materials'], l['labor'], l['equipment'], l['unit_price']), ('2.20', '7.00', '0.10', '9.30'))  # el APU reemplaza lo escrito
        self.assertEqual((l['apu']['cost_per_unit'], l['apu']['resources'][0]['cost_per_unit'], q['total']), ('9.30', '2.20', '651.00'))
        apu['resources'].pop(0)  # quitar un recurso recalcula
        q = self.erp.quote_save({'lines': [{**line, 'apu': apu}]}, q['id'], q['version'])
        self.assertEqual((q['lines'][0]['materials'], q['total']), ('0.00', '497.00'))
        for bad in [{'resources': [{'category': 'otro', 'name': 'x'}]}, {'resources': [{'category': 'material', 'name': ''}]}, {'resources': [{'category': 'material', 'name': 'x', 'waste_percent': '150'}]}, {'resources': 'x'}]:
            with self.assertRaises(DomainError):
                self.erp.quote_preview({'lines': [{**line, 'apu': bad}]})

    def test_locations_and_zones(self):
        self.assertEqual(parse_location('8.983, -79.519'), {'lat': 8.983, 'lng': -79.519})
        self.assertEqual(parse_location('https://www.google.com/maps/place/X/@8.9123,-79.7012,17z/data=!3d8.9124!4d-79.7011'), {'lat': 8.9124, 'lng': -79.7011})
        self.assertEqual(parse_location('https://maps.google.com/?q=9.01,-79.52'), {'lat': 9.01, 'lng': -79.52})
        self.assertIsNone(parse_location('Calle 50, Panamá'))
        for (lat, lng), zone in {(8.88, -79.78): 'panama_oeste', (8.93, -79.70): 'panama_oeste', (8.983, -79.519): 'ciudad_panama', (9.05, -79.45): 'ciudad_panama',
                                 (8.43, -82.43): 'interior', (8.0, -80.5): 'interior', (40.0, -3.7): None}.items():
            self.assertEqual(zone_from_coords(lat, lng), zone, (lat, lng))
        self.assertEqual(detect_zone('8.88, -79.78'), 'panama_oeste')
        q = self.erp.quote_save({'name': 'Q', 'client_id': self.client['id'], 'site_address': '8.43, -82.43', 'lines': [{'name': 'L', 'quantity': '1', 'labor': '10'}]})
        self.assertEqual((q['payment_terms']['zone'], q['site_location']), ('interior', {'lat': 8.43, 'lng': -82.43}))

    def test_legacy_budget_import(self):
        store = self.erp.store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS project_names(project TEXT PRIMARY KEY,name TEXT NOT NULL)')
            c.execute("INSERT INTO budgets VALUES('p1',?)", (json.dumps({'version': 1, 'status': 'draft', 'total': '490.00', 'lines': [{'row': 10, 'name': 'Pintura interior', 'unit': 'm²', 'quantity': '70', 'unit_price': '7.00', 'subtotal': '490.00', 'source_cell': 'A10:E10'}]}),))
            c.execute("INSERT INTO project_names VALUES('p1','Prueba APU')")
        listed = self.erp.legacy_list()
        self.assertEqual([(x['name'], x['total'], x['imported_quote_id']) for x in listed], [('Prueba APU', '490.00', None)])
        with self.assertRaises(DomainError):
            self.erp.import_legacy('p1', '')
        q = self.erp.import_legacy('p1', self.client['id'])
        self.assertEqual((q['name'], q['status'], q['lines'][0]['labor'], q['totals']['direct']), ('Prueba APU', 'draft', '7.00', '490.00'))
        self.assertEqual(self.erp.legacy_list()[0]['imported_quote_id'], q['id'])
        with self.assertRaises(DomainError):
            self.erp.import_legacy('p1', self.client['id'])  # no se duplica


class OpenAIPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        ai.configure(Path(self.tmp.name) / 'secrets'); self.addCleanup(lambda: ai.configure(None) if False else setattr(ai, '_SECRETS_DIR', None))
        self.env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith(('OPENAI', 'ENABLE_OPENAI', 'JG_OPENAI'))}, clear=True); self.env.start(); self.addCleanup(self.env.stop)
        ai._CALLS.clear()

    def test_key_stored_privately_and_never_returned(self):
        key = 'sk-test' + 'A' * 30 + 'wxyz'
        with self.assertRaises(DomainError):
            ai.save_config('no-es-clave')
        with self.assertRaises(DomainError):
            ai.save_config(key, '', True)  # activar sin modelo
        status = ai.save_config(key, 'gpt-test-1', True)
        f = Path(self.tmp.name) / 'secrets' / 'openai.json'
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertNotIn(key, json.dumps(ai.status()))
        self.assertEqual((status['enabled'], status['configured'], status['key_hint'], status['source']), (True, True, '…wxyz', 'panel'))
        status = ai.save_config(None, 'gpt-test-2', None)  # sin clave nueva: conserva la anterior
        self.assertEqual((ai.openai_config()['key'], status['model']), (key, 'gpt-test-2'))
        ai.remove_config(); self.assertFalse(f.exists()); self.assertFalse(ai.status()['openai']['key_configured'])

    def test_models_listing_and_errors(self):
        fake = FakeClient(FakeResponse(200, {'data': [{'id': 'gpt-x'}, {'id': 'o9-mini'}, {'id': 'text-embedding-3'}, {'id': 'gpt-x-audio'}, {'id': 'whisper-1'}]}))
        with patch.object(ai.httpx, 'Client', fake):
            self.assertEqual(ai.list_models('sk-test' + 'B' * 30)['models'], ['gpt-x', 'o9-mini'])
        with patch.object(ai.httpx, 'Client', FakeClient(FakeResponse(401))):
            with self.assertRaisesRegex(DomainError, '401'):
                ai.list_models('sk-test' + 'B' * 30)

    def test_apu_uses_catalog_prices_not_ai_prices(self):
        ai.save_config('sk-test' + 'C' * 30, 'gpt-test', True)
        catalog = [{'id': 'c1', 'name': 'Pintura interior', 'unit': 'm²', 'unit_price': '7.00', 'active': True}]
        body = responses_body({'message': 'ok', 'assumptions': ['2 manos'], 'questions': [], 'resources': [
            {'category': 'labor', 'name': 'Pintor', 'unit': 'm²', 'quantity_per_unit': '1', 'waste_percent': '0', 'unit_price': '99', 'price_source': 'catalog', 'catalog_id': 'c1'},
            {'category': 'material', 'name': 'Pintura', 'unit': 'gal', 'quantity_per_unit': '0.08', 'waste_percent': '10', 'unit_price': '22.5', 'price_source': 'estimate', 'catalog_id': ''},
            {'category': 'equipment', 'name': 'Fantasma', 'unit': 'und', 'quantity_per_unit': '1', 'waste_percent': '0', 'unit_price': '3', 'price_source': 'catalog', 'catalog_id': 'inventado'}]})
        fake = FakeClient(FakeResponse(200, body))
        with patch.object(ai.httpx, 'Client', fake):
            result = ai.apu({'name': 'Pintura interior', 'unit': 'm²', 'quantity': '70'}, catalog)
        labor, material, ghost = result['resources']
        self.assertEqual((labor['unit_price'], labor['source']), ('7.00', 'catalog'))  # nunca el 99 de la IA
        self.assertEqual((material['unit_price'], material['source']), ('22.5', 'estimate'))
        self.assertEqual(ghost['source'], 'estimate')
        self.assertEqual(fake.calls[0][1]['text']['format']['name'], 'jg_apu')
        bad = responses_body({'message': '', 'assumptions': [], 'questions': [], 'resources': [{'category': 'material', 'name': 'x', 'unit': 'u', 'quantity_per_unit': 'mucho', 'waste_percent': '0', 'unit_price': '1', 'price_source': 'estimate', 'catalog_id': ''}]})
        with patch.object(ai.httpx, 'Client', FakeClient(FakeResponse(200, bad))):
            with self.assertRaises(DomainError):
                ai.apu({'name': 'X'}, catalog)
        ai.save_config(None, None, False)
        with self.assertRaisesRegex(DomainError, 'desactivada'):
            ai.apu({'name': 'X'}, catalog)


if __name__ == '__main__':
    unittest.main()

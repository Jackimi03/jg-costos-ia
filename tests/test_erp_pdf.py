import base64
import re
import tempfile
import unittest
import zlib
from pathlib import Path
from jg.core import Store, DomainError
from jg.erp import ERP
from jg.erp_pdf import quote_pdf, fmt_money, fmt_qty


def pdf_text(data):
    """Texto de los flujos del PDF (descomprimidos si hace falta) para buscar valores impresos."""
    chunks = []
    for raw in re.findall(rb'stream\r?\n(.*?)\s*endstream', data, re.S):
        body = raw.strip()
        if body.endswith(b'~>'):  # ReportLab: /ASCII85Decode + /FlateDecode
            body = base64.a85decode(body[:-2])
        try:
            chunks.append(zlib.decompress(body))
        except zlib.error:
            chunks.append(body)
    return b'\n'.join(chunks).decode('latin-1').replace('\\(', '(').replace('\\)', ')')


class QuotePdfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.erp = ERP(Store(Path(self.tmp.name) / 't.db'))
        for name, value in [('Empresa', 'JG Soluciones PA'), ('ruc', '8-888-1234'), ('Dirección', 'Vía España'), ('Condiciones', 'Pago 50 % anticipo\nSaldo contra entrega')]:
            self.erp.save('settings', {'name': name, 'value': value})
        self.client = self.erp.save('clients', {'name': 'Cliente Uno', 'identification': '155-1', 'email': 'c@example.com'})

    def issued(self, **extra):
        q = self.erp.quote_save({'name': 'Cielo raso', 'client_id': self.client['id'], 'lines': [{'name': 'Gypsum', 'quantity': '10.5', 'unit': 'm²', 'materials': '10', 'labor': '5'}],
                                 'indirect_percent': '10', 'margin_percent': '20', 'discount_percent': '5', 'tax_percent': '7', 'zone': 'panama_oeste', **extra})
        q = self.erp.quote_approve(q['id'], q['version'])
        return self.erp.quote_issue(q['id'], q['version'])

    def test_company_profile_and_issue_snapshots(self):
        self.assertEqual(self.erp.company_profile(), {'name': 'JG Soluciones PA', 'ruc': '8-888-1234', 'address': 'Vía España', 'conditions': 'Pago 50 % anticipo\nSaldo contra entrega'})
        doc = self.issued(notes='No incluye electricidad', valid_days='90')
        self.assertEqual(doc['client_snapshot']['name'], 'Cliente Uno')
        self.assertEqual(doc['company_snapshot']['ruc'], '8-888-1234')
        self.assertEqual(doc['snapshot']['valid_days'], '15')  # política fija: 15 días desde la entrega

    def test_pdf_prints_sale_prices_terms_and_conditions(self):
        doc = self.issued(notes='No incluye electricidad')
        q, t = doc['snapshot'], doc['snapshot']['totals']
        text = pdf_text(quote_pdf(q, doc['client_snapshot'], doc['company_snapshot'], doc['number'], doc['issued_at']))
        line = q['lines'][0]
        for expected in [doc['number'], 'Cliente Uno', 'RUC / C', 'Precio unitario', fmt_money(line['sale_unit_price']), fmt_money(line['sale_subtotal']),
                         fmt_money(t['subtotal']), '-' + fmt_money(t['discount']), 'ITBMS (7 %)', fmt_money(t['total']), 'Pago 50 % anticipo',
                         'por 15 d', 'Cotizaci', 'Panam', 'USD 20.00', 'se descuenta del precio final', 'Saldo contra entrega',
                         fmt_money(q['payment_terms']['advance']), fmt_money(q['payment_terms']['balance']), 'No incluye electricidad', 'no constituye factura fiscal', 'gina 1 de 1']:
            self.assertIn(expected, text, expected)
        # El cliente no ve el costo directo ni el recargo por separado
        self.assertNotIn('Gastos generales y utilidad', text)
        self.assertNotIn('Costo directo', text)
        self.assertNotIn('(' + fmt_money(line['unit_price']) + ') Tj', text)  # el costo directo unitario no se imprime
        self.assertIn('BORRADOR', pdf_text(quote_pdf(q, watermark='BORRADOR')))

    def test_tax_line_only_when_applicable_and_brand_colors(self):
        q = self.erp.quote_save({'name': 'Trabajo exento', 'client_id': self.client['id'], 'zone': 'interior', 'lines': [{'name': 'A', 'quantity': '1', 'labor': '100'}]})
        text = pdf_text(quote_pdf(q, self.client, self.erp.company_profile()))
        self.assertNotIn('ITBMS', text)
        self.assertIn('USD 50.00', text)  # interior del país
        self.assertIn('(J) Tj', text); self.assertIn('(G) Tj', text)  # logotipo en dos colores
        self.assertIn('.415686 .121569 .207843 rg', text)  # vinotinto #6A1F35

    def test_legacy_snapshot_keeps_previous_layout(self):
        legacy = {'name': 'Vieja', 'lines': [{'name': 'L', 'quantity': '1', 'unit': 'und', 'unit_price': '10.00', 'subtotal': '10.00'}],
                  'totals': {'direct': '10.00', 'indirect': '1.00', 'margin': '2.00', 'subtotal': '13.00', 'total': '13.00'}}
        text = pdf_text(quote_pdf(legacy, {'name': 'C'}))
        self.assertIn('Gastos generales y utilidad', text); self.assertIn('13.00', text)

    def test_long_quotes_paginate_and_format(self):
        q = {'name': 'Larga', 'lines': [{'name': f'Partida {i} ' + 'x' * 80, 'quantity': '1', 'unit': 'und', 'unit_price': '1.00', 'subtotal': '1.00'} for i in range(90)],
             'totals': {'direct': '90.00', 'subtotal': '90.00', 'total': '90.00'}}
        text = pdf_text(quote_pdf(q, {'name': 'C'}))
        self.assertRegex(text, r'gina 1 de [2-9]')
        self.assertEqual((fmt_money('1234.5'), fmt_money('-3'), fmt_qty('240.50'), fmt_qty('85')), ('1,234.50', '-3.00', '240.5', '85'))


if __name__ == '__main__':
    unittest.main()

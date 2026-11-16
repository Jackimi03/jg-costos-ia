"""Prueba de humo E2E del ERP con Playwright.

Usa SIEMPRE una base SQLite temporal: nunca abre data/personal.db ni datos operativos.
No forma parte de `unittest discover` (el nombre no empieza por test_) porque necesita
Chromium instalado. Ejecutar desde la raíz del proyecto:

    ../venv/bin/python tests/e2e_smoke_playwright.py [carpeta_capturas]

Salida: JSON con resultados por módulo y lista de errores de consola.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MODULES = ['dashboard', 'messages', 'quotes', 'documents', 'clients', 'contracts',
           'works', 'catalog', 'team', 'reports', 'settings']


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait(url, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError('El servidor no arrancó: ' + url)


def main(shots):
    shots = Path(shots)
    shots.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.TemporaryDirectory()
    port = free_port()
    env = {**os.environ, 'JG_DATABASE': str(Path(tmp.name) / 'e2e.db'), 'ENABLE_OPENAI': 'false',
           'JG_DOCUMENTS_DIR': str(Path(tmp.name) / 'documents'), 'JG_ALLOWED_HOSTS': 'jg-remoto.test'}
    for key in ('OPENAI_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS', 'JG_SHEET_ID', 'JG_DRIVE_FOLDER_ID', 'JG_TEAM_MODE'):
        env.pop(key, None)
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'jg.personal:app', '--factory',
                               '--host', '127.0.0.1', '--port', str(port), '--workers', '1'],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f'http://127.0.0.1:{port}'
    results = {'modules': {}, 'flows': {}, 'console_errors': []}
    try:
        wait(base + '/health')
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 900}, locale='es-PA')
            page.on('console', lambda m: m.type == 'error' and results['console_errors'].append(m.text))
            page.on('pageerror', lambda e: results['console_errors'].append(str(e)))
            page.goto(base + '/')
            page.wait_for_selector('#navigation a')
            for module in MODULES:
                page.goto(f'{base}/#{module}')
                page.wait_for_function("!document.querySelector('#content .loading')", timeout=10000)
                error = page.locator('#content .notice.error')
                results['modules'][module] = {
                    'heading': page.locator('#content h1').first.inner_text() if page.locator('#content h1').count() else None,
                    'error': error.first.inner_text() if error.count() else None,
                }
                page.screenshot(path=str(shots / f'{module}.png'))

            # Flujo: importar precios de referencia al catálogo (B5)
            try:
                page.goto(base + '/#catalog')
                page.locator('button', has_text='Importar precios de referencia').click()
                page.click('#confirm-ok')
                page.locator('#toast', has_text='8 precios agregados').wait_for(timeout=5000)
                page.locator('#record-count').wait_for(timeout=5000)
                rows = page.locator('#records-table tbody tr').count()
                page.screenshot(path=str(shots / 'catalogo-importado.png'))
                page.locator('button', has_text='Importar precios de referencia').click()
                page.locator('#toast', has_text='No hay precios nuevos').wait_for(timeout=5000)
                results['flows']['importar_catalogo'] = 'ok' if rows == 8 else f'error: {rows} filas'
            except Exception as exc:  # noqa: BLE001
                results['flows']['importar_catalogo'] = f"error: {page.locator('#toast').inner_text() or type(exc).__name__}"

            # Flujo: crear cliente desde la interfaz
            page.goto(base + '/#clients')
            page.click('#create-record')
            page.fill('[name=name]', 'Cliente E2E')
            page.fill('[name=whatsapp]', '+50760000000')
            page.click('#record-form button[type=submit]')
            page.wait_for_selector('text=Registro guardado', timeout=5000)
            results['flows']['crear_cliente'] = 'ok'

            # Flujo: contrato con dos adjuntos; ambos deben verse en el registro (B4)
            try:
                page.goto(base + '/#contracts')
                page.click('#create-record')
                page.fill('[name=name]', 'Contrato E2E')
                page.select_option('select[name=client_id]', label='Cliente E2E')
                page.click('#record-form button[type=submit]')
                page.wait_for_selector('#upload-file', timeout=5000)
                for name in ('acta.pdf', 'plano.pdf'):
                    page.set_input_files('#upload-file', {'name': name, 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4 e2e'})
                    page.click('#upload-button')
                    page.locator('#attached-files button', has_text=name).wait_for(timeout=5000)
                shown = page.locator('#attached-files button').all_inner_texts()
                results['flows']['adjuntos_contrato'] = 'ok' if sorted(shown) == ['acta.pdf', 'plano.pdf'] else f'error: {shown}'
                page.screenshot(path=str(shots / 'contrato-adjuntos.png'), full_page=True)
            except Exception as exc:  # noqa: BLE001
                results['flows']['adjuntos_contrato'] = f"error: {page.locator('#toast').inner_text() or type(exc).__name__}"

            # Flujo: crear obra activa
            page.goto(base + '/#works')
            page.click('#create-record')
            page.fill('[name=name]', 'Obra E2E')
            if page.locator('select[name=status]').count():
                page.select_option('select[name=status]', 'active')
            page.click('#record-form button[type=submit]')
            page.wait_for_selector('text=Registro guardado', timeout=5000)
            results['flows']['crear_obra'] = 'ok'
            page.screenshot(path=str(shots / 'obra-detalle.png'))

            # Flujo: obra sin presupuesto muestra su resumen (B1) y se puede cerrar (B2)
            try:
                close = page.locator('button', has_text='Cerrar obra')
                close.first.wait_for(timeout=5000)
                results['flows']['resumen_obra_sin_presupuesto'] = 'ok' if page.locator('#work-summary').inner_text().strip() else 'error: resumen vacío'
                close.first.click()
                page.click('#confirm-ok')
                page.locator('span.status.closed', has_text='Cerrada').first.wait_for(timeout=5000)
                results['flows']['cerrar_obra'] = 'ok' if not page.locator('button', has_text='Cerrar obra').count() else 'error: botón sigue visible'
                page.screenshot(path=str(shots / 'obra-cerrada.png'))
            except Exception as exc:  # noqa: BLE001 — se informa en el JSON
                toast = page.locator('#toast').inner_text()
                results['flows']['cerrar_obra'] = f'error: {toast or type(exc).__name__}'

            # Inicio con datos de ejemplo (solo en la base temporal)
            try:
                page.evaluate('''async () => {
                  const {token} = await (await fetch('/session')).json();
                  const post = async (kind, body) => (await fetch('/erp/records/' + kind, {method: 'POST', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: JSON.stringify(body)})).json();
                  const iso = d => new Date(Date.now() + d * 864e5).toISOString().slice(0, 10);
                  const client = await post('clients', {name: 'Residencial Las Palmas'});
                  const a = await post('works', {name: 'Remodelación Las Palmas', client_id: client.id, budget: '18500', status: 'active', end_date: iso(20)});
                  const b = await post('works', {name: 'Techo galera Chorrera', budget: '9200', status: 'active', end_date: iso(-3)});
                  const c = await post('works', {name: 'Pintura oficinas Costa del Este', budget: '6400', status: 'active'});
                  for (const [w, n, v] of [[a, 'Materiales gypsum', '7200'], [a, 'Mano de obra semana 1', '3100'], [b, 'Lámina metálica', '8100'], [b, 'Estructura ligera', '2300'], [c, 'Pintura y sellador', '1500']]) await post('expenses', {name: n, work_id: w.id, amount: v, date: iso(-10)});
                  await post('income', {work_id: a.id, amount: '9000'});
                  await post('progress', {work_id: a.id, percent: '55'}); await post('progress', {work_id: b.id, percent: '80'}); await post('progress', {work_id: c.id, percent: '20'});
                  await post('incidents', {name: 'Filtración en losa', work_id: b.id, status: 'open'});
                  await post('tasks', {name: 'Instalar cielo raso', work_id: a.id, due_date: iso(-2), status: 'in_progress'});
                  await post('tasks', {name: 'Inspección final', work_id: a.id, due_date: iso(6), status: 'pending'});
                  await post('contracts', {name: 'Contrato Las Palmas', client_id: client.id, work_id: a.id, status: 'active', due_date: iso(12)});
                  await fetch('/erp/records/works/' + a.id, {method: 'PUT', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: JSON.stringify({version: 1, start_date: iso(-40), responsible: 'Ing. Pérez'})});
                  const s1 = await post('stages', {name: 'Demolición y preparación', work_id: a.id, start_date: iso(-40), end_date: iso(-22), status: 'completed'});
                  const s2 = await post('stages', {name: 'Cielo raso y gypsum', work_id: a.id, start_date: iso(-20), end_date: iso(8)});
                  const s3 = await post('stages', {name: 'Pintura y entrega', work_id: a.id, start_date: iso(5), end_date: iso(20)});
                  const ana = await post('workers', {name: 'Ana Rodríguez', hourly_rate: '6.50'});
                  const luis = await post('workers', {name: 'Luis Gómez', hourly_rate: '5.75'});
                  await post('tasks', {name: 'Retiro de cielo existente', work_id: a.id, stage_id: s1.id, worker_id: luis.id, start_date: iso(-40), due_date: iso(-30), status: 'completed'});
                  const t2 = await post('tasks', {name: 'Estructura metálica', work_id: a.id, stage_id: s2.id, worker_id: ana.id, start_date: iso(-20), due_date: iso(-5), status: 'in_progress'});
                  await post('tasks', {name: 'Juntas y masillado', work_id: a.id, stage_id: s2.id, worker_id: luis.id, start_date: iso(-4), due_date: iso(8)});
                  await post('tasks', {name: 'Pintura final', work_id: a.id, stage_id: s3.id, start_date: iso(8), due_date: iso(18)});
                  await post('progress', {work_id: a.id, task_id: t2.id, percent: '70', date: iso(-1)});
                  for (const [w, d] of [[ana, -3], [ana, -2], [luis, -2], [luis, -1]]) await post('timesheets', {work_id: a.id, worker_id: w.id, date: iso(d), entry: '07:00', exit: '15:30'});
                  await post('materials', {name: 'Láminas de gypsum', work_id: a.id, quantity: '80', unit_price: '11.50', movement: 'purchase'});
                  await post('expenses', {name: 'Alquiler de andamios', work_id: a.id, amount: '650', category: 'equipment'});
                  await post('expenses', {name: 'Flete de materiales', work_id: a.id, amount: '180', category: 'transport'});
                  await post('incidents', {name: 'Retraso en entrega de perfiles', work_id: a.id, status: 'resolved', date: iso(-12)});
                  await post('logs', {work_id: a.id, date: iso(-1), notes: 'Se completó el 70% de la estructura del cielo en la planta alta. Pendiente revisión de niveles.'});
                  window.__e2eWork = a.id;
                }''')
                page.goto(base + '/#works')
                page.goto(base + '/#dashboard')
                page.wait_for_selector('.alert-item', timeout=10000)
                results['flows']['dashboard_con_datos'] = {
                    'alertas': page.locator('.alert-item').count(),
                    'vencimientos': page.locator('.deadline').count(),
                    'barras_obras': page.locator('.hbar-row').count(),
                    'anillo': page.locator('.ring strong').inner_text(),
                }
                page.screenshot(path=str(shots / 'dashboard-datos.png'), full_page=True)
                page.locator('.alert-item').first.click()
                page.wait_for_selector('#work-summary, .record-meta', timeout=5000)
                results['flows']['dashboard_alerta_abre_registro'] = 'ok'
            except Exception as exc:  # noqa: BLE001
                results['flows']['dashboard_con_datos'] = f"error: {page.locator('#toast').inner_text() or type(exc).__name__}"

            # Detalle de obra con cronograma (datos de ejemplo)
            try:
                work_id = page.evaluate('window.__e2eWork')
                page.goto(base + '/#works')
                page.wait_for_selector('#records-table')
                page.locator('#records-table button', has_text='Abrir').first.wait_for()
                page.evaluate('id => openRecord("works", id)', work_id)
                page.wait_for_selector('.gantt-row.task', timeout=10000)
                results['flows']['detalle_obra'] = {
                    'filas_cronograma': page.locator('.gantt-row').count(),
                    'vencidas': page.locator('.gantt-row.overdue').count(),
                    'actividades': page.locator('#content table tbody tr').count(),
                    'segmentos_costo': page.locator('.stack .seg').count(),
                    'equipo': page.locator('#content aside .cost-list').last.locator('div').count(),
                }
                results['flows']['detalle_obra_escritorio_desborde'] = page.evaluate('document.documentElement.scrollWidth > 1440')
                page.screenshot(path=str(shots / 'obra-detalle-completo.png'), full_page=True)
                # Orden de cambio: crear desde la obra, ver impacto, aprobar y comprobar presupuesto revisado
                page.click('#add-change')
                page.fill('[name=name]', 'Refuerzo de estructura por cambio de luminarias')
                page.fill('[name=requested_by]', 'Residencial Las Palmas')
                page.fill('[name=reason]', 'Cliente solicita lámparas empotradas')
                page.fill('[name=cost_delta]', '1200')
                page.fill('[name=days_delta]', '9')
                page.click('#record-form button[type=submit]')
                page.wait_for_selector('#approve-change', timeout=5000)
                page.screenshot(path=str(shots / 'orden-cambio-pendiente.png'), full_page=True)
                page.fill('#decision-comment', 'Aprobado con cotización del proveedor adjunta')
                page.click('#approve-change')
                page.click('#confirm-ok')
                page.locator('.decision .status.approved').wait_for(timeout=5000)
                page.evaluate('''async id => { const {token} = await (await fetch('/session')).json();
                  const post = b => fetch('/erp/records/changes', {method: 'POST', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: JSON.stringify(b)});
                  await post({name: 'Deductivo: pintura de fachada a cargo del cliente', work_id: id, cost_delta: '-850', days_delta: '-3', status: 'pending', requested_by: 'Ing. Pérez'});
                  await post({name: 'Cambio de cerámica en baños', work_id: id, cost_delta: '420', days_delta: '2', status: 'pending', requested_by: 'Residencial Las Palmas'}); }''', work_id)
                page.locator('button.link', has_text='Remodelación Las Palmas').click()
                page.wait_for_selector('.change-item', timeout=5000)
                results['flows']['orden_cambio'] = {
                    'ordenes': page.locator('.change-item').count(),
                    'primera': page.locator('.change-item strong').first.inner_text(),
                    'presupuesto_revisado': page.locator('.impact-strip strong').nth(2).inner_text(),
                    'fila_entrega_revisada': page.locator('.gantt-row.revised').count(),
                    'nota_kpi': page.locator('#work-summary .metric-note').first.inner_text(),
                }
                page.screenshot(path=str(shots / 'obra-ordenes-cambio.png'), full_page=True)
                mob = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True)
                mob.goto(base + '/#works')
                mob.wait_for_selector('#records-table')
                mob.evaluate('id => openRecord("works", id)', work_id)
                mob.wait_for_selector('.gantt-row.task', timeout=10000)
                results['flows']['detalle_obra_movil_desborde'] = mob.evaluate('document.documentElement.scrollWidth > 390')
                mob.screenshot(path=str(shots / 'obra-detalle-movil.png'), full_page=True)
                mob.close()
            except Exception as exc:  # noqa: BLE001
                results['flows']['detalle_obra'] = f"error: {page.locator('#toast').inner_text() or type(exc).__name__}: {exc}"[:300]

            # Cotización: vista previa PDF, aprobar, emitir y descargar PDF definitivo (dos veces, idéntico)
            try:
                quote_id = page.evaluate('''async () => { const {token} = await (await fetch('/session')).json();
                  const call = async (path, body) => (await fetch(path, {method: 'POST', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: JSON.stringify(body)})).json();
                  const set = (name, value) => call('/erp/records/settings', {name, value});
                  await set('RUC', '8-888-1234 DV 56'); await set('Dirección', 'Vía España, Ciudad de Panamá'); await set('Teléfono', '+507 6000-0000');
                  const client = await call('/erp/records/clients', {name: 'Residencial Las Palmas, S.A.', identification: '155-123-456 DV 12', contact: 'Arq. María Pérez', address: 'Calle 50, Panamá', phone: '+507 6111-2222'});
                  const q = await call('/erp/quotes', {name: 'Cielo raso y pintura — planta alta', client_id: client.id, valid_days: '30', notes: 'Incluye limpieza final. No incluye trabajos eléctricos.', indirect_percent: '8', margin_percent: '15', tax_percent: '0',
                    lines: [{name: 'Cielo raso de gypsum con estructura metálica', quantity: '85', unit: 'm²', materials: '14.20', labor: '9.80', equipment: '0.60'}, {name: 'Pintura interior (2 manos) con resane', quantity: '240.5', unit: 'm²', materials: '2.10', labor: '7.00'}]});
                  return q.id; }''')
                # Porcentajes automáticos: con gastos fijos configurados, el editor de una cotización nueva los precarga
                page.evaluate('''async () => { const {token} = await (await fetch('/session')).json();
                  for (const [name, value] of [['Gastos fijos mensuales', '600'], ['Utilidad trabajos de USD 200 o más (%)', '15'], ['Utilidad trabajos menores de USD 200 (%)', '10']]) await fetch('/erp/records/settings', {method: 'POST', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, body: JSON.stringify({name, value})}); }''')
                page.goto(base + '/#quotes'); page.wait_for_selector('#create-record'); page.click('#create-record')
                page.wait_for_selector('.pricing-hint', timeout=5000)
                results['flows']['porcentajes_auto'] = {'gastos_generales': page.input_value('[name=indirect_percent]'), 'utilidad': page.input_value('[name=margin_percent]'), 'modo_utilidad': page.input_value('#margin-mode'),
                                                        'explicacion': page.locator('.pricing-hint').inner_text()[:160]}
                page.screenshot(path=str(shots / 'cotizacion-porcentajes-auto.png'), full_page=True)
                page.goto(base + '/#settings'); page.wait_for_selector('.pricing-summary', timeout=5000)
                page.screenshot(path=str(shots / 'configuracion-porcentajes.png'), full_page=True)
                page.evaluate('id => openRecord("quotes", id)', quote_id)
                page.wait_for_selector('.terms', timeout=5000)
                terms_text = page.locator('.terms').inner_text()
                page.screenshot(path=str(shots / 'cotizacion-detalle.png'), full_page=True)
                with page.expect_download() as info:
                    page.locator('button', has_text='Vista previa PDF').click()
                preview = Path(info.value.path()).read_bytes()
                page.locator('button', has_text='Aprobar').click(); page.click('#confirm-ok')
                page.locator('button', has_text='Emitir documento').wait_for(timeout=5000)
                page.locator('button', has_text='Emitir documento').click()
                page.locator('button', has_text='Descargar PDF').wait_for(timeout=5000)
                downloads = []
                for _ in range(2):
                    with page.expect_download() as info:
                        page.locator('button', has_text='Descargar PDF').click()
                    downloads.append(Path(info.value.path()).read_bytes())
                (shots / 'cotizacion-ejemplo.pdf').write_bytes(downloads[0])
                results['flows']['pdf_cotizacion'] = {'vista_previa_pdf': preview.startswith(b'%PDF'), 'emitido_pdf': downloads[0].startswith(b'%PDF'),
                                                      'descargas_identicas': downloads[0] == downloads[1], 'nombre': info.value.suggested_filename,
                                                      'condiciones_en_pantalla': ' | '.join(terms_text.split('\n')[1:8])}
            except Exception as exc:  # noqa: BLE001
                results['flows']['pdf_cotizacion'] = f"error: {page.locator('#toast').inner_text() or type(exc).__name__}: {exc}"[:300]

            # Datos de empresa con RUC y DV (formulario real)
            try:
                # Clic en el menú aunque la dirección ya sea #settings (antes no hacía nada)
                page.click('#navigation a[href="#settings"]'); page.wait_for_selector('#company-form')
                page.fill('#company-form [name=ruc]', '8-888-1234'); page.fill('#company-form [name=dv]', '56')
                page.fill('#company-form [name=address]', '8.983, -79.519')
                page.click('#company-form button[type=submit]'); page.locator('#toast', has_text='Datos guardados').wait_for(timeout=5000)
                page.wait_for_selector('#company-form')
                results['flows']['empresa_ruc_dv'] = {'ruc': page.input_value('#company-form [name=ruc]'), 'dv': page.input_value('#company-form [name=dv]'),
                                                     'mapa': page.locator('#company-map iframe').count(), 'panel_ia': page.locator('#ai-panel').count()}
                page.screenshot(path=str(shots / 'configuracion-empresa-ia.png'), full_page=True)
            except Exception as exc:  # noqa: BLE001
                results['flows']['empresa_ruc_dv'] = f"error: {page.locator('#toast').inner_text() or type(exc).__name__}: {exc}"[:300]

            # APU dentro de la cotización: agregar/quitar recursos recalcula costos y total sin botones
            try:
                page.goto(base + '/#quotes'); page.wait_for_selector('#create-record'); page.click('#create-record')
                page.wait_for_selector('#quote-lines [data-line="0"]')
                page.fill('[name=name]', 'Pintura con APU')
                page.select_option('select[name=client_id]', label='Residencial Las Palmas')
                page.fill('[name=site_address]', '8.88, -79.78')
                row = '#quote-lines [data-line="0"]'
                page.fill(row + ' [data-key="name"]', 'Pintura interior 2 manos'); page.fill(row + ' [data-key="quantity"]', '70'); page.fill(row + ' [data-key="unit"]', 'm²')
                page.click(row + ' [data-action="apu-toggle"]')
                page.click('[data-action="apu-add"]')
                res = '[data-apu-row="0"] [data-res="0"]'
                page.fill(res + ' [data-rkey="name"]', 'Pintura látex'); page.fill(res + ' [data-rkey="quantity_per_unit"]', '0.1'); page.fill(res + ' [data-rkey="unit_price"]', '20')
                page.click('[data-action="apu-add"]')
                res2 = '[data-apu-row="0"] [data-res="1"]'
                page.select_option(res2 + ' [data-rkey="category"]', 'labor'); page.fill(res2 + ' [data-rkey="name"]', 'Pintor'); page.fill(res2 + ' [data-rkey="quantity_per_unit"]', '1'); page.fill(res2 + ' [data-rkey="unit_price"]', '7')
                page.wait_for_function("document.querySelector('#quote-totals .total-row.final')", timeout=8000)
                before = {'materiales': page.input_value(row + ' [data-key="materials"]'), 'mano_obra': page.input_value(row + ' [data-key="labor"]'), 'total': page.locator('#quote-totals .total-row.final strong').inner_text()}
                page.screenshot(path=str(shots / 'cotizacion-apu.png'), full_page=True)
                page.click('[data-apu-row="0"] [data-res="0"] [data-action="apu-del"]')
                page.wait_for_function(f"document.querySelector('#quote-totals .total-row.final strong')?.textContent !== {before['total']!r}", timeout=8000)
                after = {'materiales': page.input_value(row + ' [data-key="materials"]'), 'total': page.locator('#quote-totals .total-row.final strong').inner_text()}
                results['flows']['apu_recalculo'] = {'antes': before, 'despues_de_quitar_material': after, 'zona': page.locator('.terms').inner_text().split('\n')[2] if page.locator('.terms').count() else None,
                                                    'mapa_obra': page.locator('#site-map iframe').count(), 'boton_ia_desactivado_sin_clave': page.is_disabled('#apu-all')}
            except Exception as exc:  # noqa: BLE001
                results['flows']['apu_recalculo'] = f"error: {page.locator('#toast').inner_text() or type(exc).__name__}: {exc}"[:300]

            # Mensajes: WhatsApp en ventana acoplada (no se puede incrustar: X-Frame-Options DENY)
            try:
                page.goto(base + '/#messages'); page.wait_for_selector('#open-wa')
                results['flows']['mensajes'] = {'aviso': 'X-Frame-Options' in page.locator('.notice').first.inner_text(), 'clientes': page.locator('.wa-item').count()}
                page.screenshot(path=str(shots / 'mensajes.png'))
            except Exception as exc:  # noqa: BLE001
                results['flows']['mensajes'] = f'error: {exc}'[:200]

            # Acceso desde otro equipo (nombre distinto de 127.0.0.1): pide correo, sin sesión automática
            try:
                remote_browser = p.chromium.launch(args=['--host-resolver-rules=MAP jg-remoto.test 127.0.0.1'])
                rp = remote_browser.new_page(viewport={'width': 390, 'height': 844})
                rp.goto(f'http://jg-remoto.test:{port}/'); rp.wait_for_selector('#login-form', timeout=10000)
                rp.fill('[name=email]', 'persona@example.com'); rp.click('#login-form button[type=submit]')
                rp.wait_for_selector('#form-error:not([hidden])', timeout=8000)
                rp.screenshot(path=str(shots / 'acceso-correo.png'))
                results['flows']['acceso_remoto'] = {'pantalla_correo': True, 'mensaje_sin_correo_configurado': rp.locator('#form-error').inner_text(),
                                                     'panel_antiguo_bloqueado': rp.goto(f'http://jg-remoto.test:{port}/legacy').status == 403}
                remote_browser.close()
                page.click('#navigation a[href="#settings"]'); page.wait_for_selector('#mail-panel')
                results['flows']['paneles_correo_instalador'] = [page.locator('#mail-panel').count(), page.locator('#installer-panel').count()]
            except Exception as exc:  # noqa: BLE001
                results['flows']['acceso_remoto'] = f'error: {type(exc).__name__}: {exc}'[:300]

            # Vista móvil
            mobile = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True)
            mobile.goto(base + '/#dashboard')
            mobile.wait_for_function("!document.querySelector('#content .loading')", timeout=10000)
            # Con is_mobile, innerWidth crece con el contenido: comparar con el ancho real del dispositivo (390 px).
            results['flows']['movil_scroll_horizontal'] = mobile.evaluate('document.documentElement.scrollWidth > 390')
            results['flows']['movil_metricas_recortadas'] = mobile.evaluate("[...document.querySelectorAll('.metric-value')].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent)")
            mobile.screenshot(path=str(shots / 'dashboard-movil.png'), full_page=True)
            browser.close()
    finally:
        server.terminate()
        server.wait(10)
        tmp.cleanup()
    print(json.dumps(results, ensure_ascii=False, indent=1))
    failed = any(m['error'] for m in results['modules'].values()) or any(str(v).startswith('error') for v in results['flows'].values())
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / 'docs' / 'capturas-erp')))

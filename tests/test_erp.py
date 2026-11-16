import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from jg.core import Store, DomainError
from jg.erp import ERP
try:
    from tests.precios import requires_real_prices
except ImportError:
    from precios import requires_real_prices

class ERPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.erp=ERP(Store(Path(self.temp.name)/'test.db'))
        self.client=self.erp.save('clients',{'name':'Cliente real'})
    def quote(self):
        return self.erp.quote_save({'name':'Obra','client_id':self.client['id'],'lines':[{'name':'Trabajo','quantity':'3','materials':'10.10','labor':'2.25','equipment':'1','transport':'0.50'}],'indirect_percent':'10','margin_percent':'20','discount_percent':'5','tax_percent':'7','zone':'panama_oeste'})
    def test_complete_flow_and_accounting(self):
        q=self.quote();self.assertEqual(q['totals']['direct'],'41.55');self.assertEqual(q['total'],'55.75')  # 3 × 18.28 = 54.84 − 2.74 + 3.65
        self.assertEqual((q['lines'][0]['sale_unit_price'],q['lines'][0]['sale_subtotal'],q['totals']['subtotal']),('18.28','54.84','54.84'))
        self.assertEqual(Decimal(q['totals']['direct'])+Decimal(q['totals']['indirect'])+Decimal(q['totals']['margin']),Decimal(q['totals']['subtotal']))
        approved=self.erp.quote_approve(q['id'],q['version'])
        with self.assertRaises(DomainError):self.erp.quote_save({'name':'Cambio'},q['id'],approved['version'])
        document=self.erp.quote_issue(q['id'],approved['version']);self.assertEqual(document['snapshot']['total'],'55.75')
        issued=self.erp.get('quotes',q['id']);work=self.erp.work_from_quote(q['id'],issued['version'])
        self.assertEqual(self.erp.work_from_quote(q['id'],issued['version'])['id'],work['id'])
        worker=self.erp.save('workers',{'name':'Ana','hourly_rate':'5'})
        self.erp.save('timesheets',{'work_id':work['id'],'worker_id':worker['id'],'date':'2026-11-16','entry':'08:00','exit':'10:30'})
        self.erp.save('materials',{'name':'Insumos','work_id':work['id'],'quantity':'2','unit_price':'3'})
        self.erp.save('expenses',{'name':'Viaje','work_id':work['id'],'amount':'4'})
        self.erp.save('income',{'work_id':work['id'],'amount':'50'})
        result=self.erp.report(work['id']);self.assertEqual(result['totals']['cost'],'22.50');self.assertEqual(result['totals']['profit'],'27.50')
        self.assertEqual(self.erp.dashboard()['active_works'],1)
        self.erp.save('works',{'status':'closed'},work['id'],work['version'])
        self.assertEqual(self.erp.dashboard()['active_works'],0)
    def test_concurrency_relations_and_delete(self):
        c=self.client
        edited=self.erp.save('clients',{'name':'Nuevo'},c['id'],c['version'])
        with self.assertRaises(DomainError):self.erp.save('clients',{'name':'Stale'},c['id'],c['version'])
        with self.assertRaises(DomainError):self.erp.save('contacts',{'name':'A','client_id':'missing'})
        contact=self.erp.save('contacts',{'name':'A','client_id':c['id']})
        with self.assertRaises(DomainError):self.erp.delete('clients',c['id'],edited['version'])
        self.erp.delete('contacts',contact['id'],contact['version']);self.erp.delete('clients',c['id'],edited['version'])
        self.assertEqual(self.erp.list('clients'),[])
    def test_bad_prices_and_cross_work_rejected(self):
        for val in ['NaN','Infinity','-1']:
            with self.assertRaises(DomainError):self.erp.quote_preview({'lines':[{'name':'x','quantity':'1','unit_price':val}]})
        with self.assertRaises(DomainError):self.erp.quote_preview({'lines':[{'name':'x','quantity':'1'}]})
        a=self.erp.save('works',{'name':'A'});b=self.erp.save('works',{'name':'B'})
        stage=self.erp.save('stages',{'name':'Etapa','work_id':a['id']})
        with self.assertRaises(DomainError):self.erp.save('tasks',{'name':'Tarea','work_id':b['id'],'stage_id':stage['id']})
    def test_seed_and_closure(self):
        seeded=self.erp.seed_catalog();self.assertGreater(len(seeded),0)
        self.assertEqual(self.erp.seed_catalog(),[])
        self.assertTrue(all(r.get('source_hash') for r in seeded))
        self.assertEqual(self.erp.legacy_budgets(),[])
        work=self.erp.save('works',{'name':'Obra','status':'active'})
        task=self.erp.save('tasks',{'name':'Pendiente','work_id':work['id']})
        with self.assertRaises(DomainError):self.erp.save('works',{'status':'closed'},work['id'],work['version'])
        self.erp.save('tasks',{'status':'completed'},task['id'],task['version'])
        self.assertEqual(self.erp.save('works',{'status':'closed'},work['id'],work['version'])['status'],'closed')
    def test_change_orders_lifecycle_and_impact(self):
        w=self.erp.save('works',{'name':'Casa','budget':'1000','start_date':'2026-11-01','end_date':'2026-11-30','status':'active'})
        # Validación de importes y días con signo
        for bad in [{'cost_delta':'abc'},{'cost_delta':'NaN'},{'days_delta':'1.5'},{'days_delta':'9999'},{'status':'approved'}]:
            with self.assertRaises(DomainError):self.erp.save('changes',{'name':'X','work_id':w['id'],**bad})
        up=self.erp.save('changes',{'name':'Refuerzo eléctrico','work_id':w['id'],'cost_delta':'250.5','days_delta':'+5','status':'pending'})
        down=self.erp.save('changes',{'name':'Quitar acabado','work_id':w['id'],'cost_delta':'-100','days_delta':'-2','status':'pending'})
        small=self.erp.save('changes',{'name':'Cambio menor','work_id':w['id'],'cost_delta':'20','status':'pending'})
        draft=self.erp.save('changes',{'name':'Idea','work_id':w['id'],'cost_delta':'999','status':'draft'})
        self.assertEqual((up['cost_delta'],up['days_delta'],down['cost_delta']),('250.50','5','-100.00'))
        # Antes de decidir: el presupuesto no cambia y hay alertas de pendientes
        self.assertEqual(self.erp.report(w['id'])['works'][0]['budget'],'1000.00')
        d=self.erp.dashboard('2026-11-16');self.assertEqual(sorted(a['name'] for a in d['alerts'] if a['kind']=='pending_change'),['Cambio menor','Quitar acabado','Refuerzo eléctrico'])
        with self.assertRaises(DomainError):self.erp.decide_change(draft['id'],draft['version'],'approved')  # borrador: no se decide
        with self.assertRaises(DomainError):self.erp.decide_change(down['id'],down['version'],'rejected','')  # rechazo exige motivo
        with self.assertRaises(DomainError):self.erp.decide_change(up['id'],up['version'],'maybe')
        up=self.erp.decide_change(up['id'],up['version'],'approved','Con factura del proveedor',actor='propietario')
        self.assertEqual((up['status'],up['decided_by'],up['decision_comment']),('approved','propietario','Con factura del proveedor'))
        down=self.erp.decide_change(down['id'],down['version'],'rejected','Cliente mantiene acabado')
        with self.assertRaises(DomainError):self.erp.decide_change(up['id'],up['version'],'rejected','x')  # ya decidida
        for decided in (up,down):
            with self.assertRaises(DomainError):self.erp.save('changes',{'cost_delta':'1'},decided['id'],decided['version'])
            with self.assertRaises(DomainError):self.erp.delete('changes',decided['id'],decided['version'])
        # Impacto: solo las aprobadas modifican presupuesto y entrega
        row=self.erp.report(w['id'])['works'][0]
        self.assertEqual((row['original_budget'],row['approved_changes'],row['budget']),('1000.00','250.50','1250.50'))
        o=self.erp.work_overview(w['id'],today='2026-11-16')
        self.assertEqual(o['schedule'],{'original_end':'2026-11-30','revised_end':'2026-12-05','pending_days':0,'end_if_approved':'2026-12-05','approved_days':5})
        self.assertEqual(o['change_impact']['pending'],{'count':1,'cost':'20.00','days':0,'end_if_approved':'2026-12-05'})
        self.assertEqual([c['name'] for c in o['changes']],['Cambio menor','Idea','Refuerzo eléctrico','Quitar acabado'])
        self.assertEqual(o['range']['end'],'2026-12-05')
        self.assertNotIn('cost_delta',self.erp.work_overview(w['id'],financial=False)['changes'][0])
        # La obra se considera retrasada contra la entrega revisada, no la original
        kinds=lambda today:[a['kind'] for a in self.erp.dashboard(today)['alerts'] if a['kind']=='work_delayed']
        self.assertEqual(kinds('2026-12-02'),[]);self.assertEqual(kinds('2026-12-06'),['work_delayed'])
        self.assertEqual(len(self.erp.history('changes',up['id'])),2)
    def test_work_overview(self):
        w=self.erp.save('works',{'name':'Casa','client_id':self.client['id'],'budget':'1000','start_date':'2026-11-01','end_date':'2026-12-31','status':'active'})
        other=self.erp.save('works',{'name':'Otra'})
        s1=self.erp.save('stages',{'name':'Estructura','work_id':w['id'],'start_date':'2026-11-01','end_date':'2026-11-20'})
        t1=self.erp.save('tasks',{'name':'Columnas','work_id':w['id'],'stage_id':s1['id'],'start_date':'2026-11-02','due_date':'2026-11-10','status':'in_progress'})
        self.erp.save('tasks',{'name':'Vigas','work_id':w['id'],'stage_id':s1['id'],'due_date':'2026-11-30','status':'completed'})
        self.erp.save('tasks',{'name':'Suelta','work_id':w['id']})
        self.erp.save('tasks',{'name':'De otra obra','work_id':other['id']})
        self.erp.save('progress',{'work_id':w['id'],'task_id':t1['id'],'percent':'40','date':'2026-11-05'})
        self.erp.save('progress',{'work_id':w['id'],'task_id':t1['id'],'percent':'60','date':'2026-11-08'})
        ana=self.erp.save('workers',{'name':'Ana','hourly_rate':'5'})
        for d in ('2026-11-03','2026-11-04'):self.erp.save('timesheets',{'work_id':w['id'],'worker_id':ana['id'],'date':d,'entry':'08:00','exit':'16:00'})
        self.erp.save('expenses',{'name':'Grúa','work_id':w['id'],'amount':'120','category':'equipment'})
        self.erp.save('expenses',{'name':'Varios','work_id':w['id'],'amount':'30'})
        self.erp.save('materials',{'name':'Cemento','work_id':w['id'],'quantity':'10','unit_price':'9'})
        self.erp.save('incidents',{'name':'Lluvia','work_id':w['id'],'status':'open','date':'2026-11-06'})
        self.erp.save('logs',{'work_id':w['id'],'date':'2026-11-07','notes':'Vaciado de columnas'})
        o=self.erp.work_overview(w['id'],today='2026-11-16')
        self.assertEqual(o['client'],'Cliente real')
        self.assertEqual(o['range'],{'start':'2026-11-01','end':'2026-12-31'})
        self.assertEqual([t['name'] for t in o['tasks']],['Columnas','Vigas','Suelta'])
        cols=o['tasks'][0];self.assertEqual((cols['progress'],cols['overdue'],cols['stage']),('60.00',True,'Estructura'))
        self.assertEqual(o['tasks'][1]['progress'],'100.00')
        self.assertEqual((o['open_tasks'],o['overdue_tasks']),(2,1))
        self.assertEqual(o['stages'][0]['tasks'],2)
        self.assertEqual(o['stages'][0]['progress'],'80.00')  # promedio de 60 % y 100 %
        self.assertEqual([(m['worker'],float(m['hours']),m['cost'],m['days']) for m in o['team']],[('Ana',16.0,'80.00',2)])
        self.assertEqual(o['financial']['breakdown'],{'labor':'80.00','materials':'90.00','equipment':'120.00','transport':'0.00','other':'30.00'})
        self.assertEqual((o['financial']['cost'],o['financial']['utilization']),('320.00','32.00'))
        self.assertEqual(o['counts']['tasks'],3)
        self.assertEqual(o['logs'][0]['notes'],'Vaciado de columnas')
        hidden=self.erp.work_overview(w['id'],today='2026-11-16',financial=False)
        self.assertNotIn('financial',hidden);self.assertNotIn('cost',hidden['team'][0])
        self.assertIsNone(self.erp.work_overview(other['id'])['range'])
    def test_dashboard_alerts_deadlines_and_pipeline(self):
        today='2026-11-16'
        w=self.erp.save('works',{'name':'Torre','budget':'100','status':'active','end_date':'2026-12-01'})
        late=self.erp.save('works',{'name':'Atrasada','status':'active','end_date':'2026-11-01'})
        self.erp.save('works',{'name':'Cerrada vieja','status':'closed','end_date':'2026-01-01'})
        self.erp.save('expenses',{'name':'Acero','work_id':w['id'],'amount':'150'})
        self.erp.save('incidents',{'name':'Filtración','work_id':w['id'],'status':'open'})
        self.erp.save('incidents',{'name':'Resuelta','work_id':w['id'],'status':'resolved'})
        self.erp.save('tasks',{'name':'Vencida','work_id':w['id'],'due_date':'2026-11-10','status':'in_progress'})
        self.erp.save('tasks',{'name':'Hecha','work_id':w['id'],'due_date':'2026-11-10','status':'completed'})
        self.erp.save('tasks',{'name':'Próxima','work_id':w['id'],'due_date':'2026-11-20'})
        self.erp.save('tasks',{'name':'Lejana','work_id':w['id'],'due_date':'2027-03-01'})
        self.erp.save('contracts',{'name':'K','client_id':self.client['id'],'status':'active','due_date':'2026-11-30'})
        q=self.quote();self.erp.quote_approve(q['id'],q['version']);self.quote()
        d=self.erp.dashboard(today)
        self.assertEqual([(a['kind'],a['name']) for a in d['alerts']],[('over_budget','Torre'),('open_incident','Filtración'),('overdue_task','Vencida'),('work_delayed','Atrasada')])
        self.assertTrue(all(a['record_id'] and a['record_kind'] for a in d['alerts']))
        self.assertEqual([(u['date'],u['name']) for u in d['upcoming']],[('2026-11-20','Próxima'),('2026-11-30','K'),('2026-12-01','Torre')])
        self.assertEqual(d['pipeline']['draft'],{'count':1,'total':'55.75'})
        self.assertEqual(d['pipeline']['approved']['count'],1)
        self.assertEqual(d['utilization'],'150.00')
        self.assertEqual(d['works_at_risk'],2)
        self.assertEqual({x['id'] for x in d['works'] if x['status']=='active'},{w['id'],late['id']})
    def test_pricing_defaults_auto_overhead(self):
        today='2026-11-16'
        none=self.erp.pricing_defaults(today);self.assertEqual((none['indirect_percent'],none['margin_percent'],none['overhead_source']),('0.00','0.00','none'))
        self.erp.save('settings',{'name':'Gastos generales (%)','value':'12 %'});self.erp.save('settings',{'name':'Utilidad','value':'15'})
        fixed=self.erp.save('settings',{'name':'Gastos fijos mensuales','value':'USD 600'})
        d=self.erp.pricing_defaults(today);self.assertEqual((d['indirect_percent'],d['margin_percent'],d['overhead_source']),('12.00','15.00','setting'))  # sin costos de obra aún
        w=self.erp.save('works',{'name':'Obra'});ana=self.erp.save('workers',{'name':'Ana','hourly_rate':'375'})
        self.erp.save('timesheets',{'work_id':w['id'],'worker_id':ana['id'],'date':'2026-10-01','hours':'8'})        # 3,000
        self.erp.save('materials',{'name':'Gypsum','work_id':w['id'],'quantity':'100','unit_price':'100','date':'2026-09-20'})  # 10,000 compra
        self.erp.save('materials',{'name':'Gypsum','work_id':w['id'],'quantity':'100','unit_price':'100','date':'2026-09-25','movement':'consumption'})  # no se cuenta dos veces
        self.erp.save('expenses',{'name':'Flete','work_id':w['id'],'amount':'2000','date':'2026-11-10'})                # 2,000
        self.erp.save('expenses',{'name':'Antiguo','work_id':w['id'],'amount':'99999','date':'2026-01-01'})            # fuera de los 3 meses
        self.erp.save('expenses',{'name':'Sin fecha','work_id':w['id'],'amount':'5000'})                               # sin fecha: no se usa
        d=self.erp.pricing_defaults(today)
        # 600 ÷ (15,000 / 3 = 5,000) = 12 %
        self.assertEqual((d['indirect_percent'],d['overhead_source'],d['basis']['monthly_direct_cost'],d['warning']),('12.00','auto','5000.00',None))
        self.erp.save('settings',{'value':'4000'},fixed['id'],fixed['version'])
        high=self.erp.pricing_defaults(today)  # 4,000 ÷ 5,000 = 80 % → demasiado alto: usa el 12 % configurado
        self.assertEqual((high['indirect_percent'],high['overhead_source'],high['basis']['auto_percent']),('12.00','setting','80.00'));self.assertIn('80.00',high['warning'])
        # Cotización nueva sin porcentajes: se completan solos; con porcentajes escritos: se respetan
        q=self.erp.quote_save({'name':'Auto','client_id':self.client['id'],'zone':'interior','lines':[{'name':'L','quantity':'1','labor':'100'}]})
        self.assertEqual((q['indirect_percent'],q['margin_percent'],q['total']),('12.00','15.00','128.80'))  # 100 × 1.12 × 1.15
        gg=next(r for r in self.erp.list('settings') if r['name']=='Gastos generales (%)');self.erp.delete('settings',gg['id'],gg['version'])
        capped=self.erp.pricing_defaults(today);self.assertEqual((capped['indirect_percent'],capped['overhead_source']),('50.00','auto_capped'))
        q2=self.erp.quote_save({'name':'Manual','client_id':self.client['id'],'zone':'interior','indirect_percent':'10','margin_percent':'0','lines':[{'name':'L','quantity':'1','labor':'100'}]})
        self.assertEqual((q2['indirect_percent'],q2['margin_percent']),('10','0'))
        self.assertIsNone(self.erp._setting_number('abc'));self.assertIsNone(self.erp._setting_number('150',Decimal(100)))
    def test_tiered_margin_by_total(self):
        self.erp.save('settings',{'name':'Gastos fijos mensuales','value':'40'})
        self.erp.save('settings',{'name':'Utilidad trabajos de USD 200 o más (%)','value':'15'})
        self.erp.save('settings',{'name':'Utilidad trabajos menores de USD 200 (%)','value':'10'})
        d=self.erp.pricing_defaults('2026-11-16')
        # Sin obras con costos registrados: no hay base para gastos generales automáticos
        self.assertEqual((d['indirect_percent'],d['overhead_source'],d['margin_mode'],d['margin_rule']),('0.00','none','auto',{'high':'15.00','low':'10.00','threshold':'200.00'}))
        new=lambda cost,**kw:self.erp.quote_save({'name':'Q','client_id':self.client['id'],'zone':'panama_oeste','lines':[{'name':'L','quantity':'1','labor':cost}],**kw})
        small=new('100');self.assertEqual((small['margin_percent'],small['total'],small['payment_terms']['advance_percent']),('10.00','110.00','25'))
        big=new('180');self.assertEqual((big['margin_percent'],big['total'],big['payment_terms']['advance_percent']),('15.00','207.00','30'))
        edge=new('174');self.assertEqual((edge['margin_percent'],edge['total']),('15.00','200.10'))  # con 15 % llega a 200.10 ≥ 200: se queda en 15 %
        under=new('173');self.assertEqual((under['margin_percent'],under['total']),('10.00','190.30'))  # con 15 % serían 198.95 < 200: baja a 10 %
        manual=new('100',margin_mode='manual',margin_percent='20');self.assertEqual((manual['margin_percent'],manual['total']),('20','120.00'))
        # Editar un borrador conserva el modo automático y vuelve a elegir tramo
        big=self.erp.quote_save({'lines':[{'name':'L','quantity':'1','labor':'50'}]},big['id'],big['version'])
        self.assertEqual((big['margin_mode'],big['margin_percent'],big['total']),('auto','10.00','55.00'))
        with self.assertRaises(DomainError):new('100',margin_mode='otro')
    def test_zone_detection_and_payment_terms(self):
        from jg.erp import detect_zone,payment_terms
        cases={'Calle 3, La Chorrera':'panama_oeste','Vista Alegre, Arraiján':'panama_oeste','Panamá Oeste, Capira':'panama_oeste','Coronado, Chame':'panama_oeste',
               'Costa del Este, Panamá':'ciudad_panama','Vía España, Bella Vista':'ciudad_panama','San Miguelito':'ciudad_panama','Ciudad de Panamá':'ciudad_panama',
               'David, Chiriquí':'interior','Santiago de Veraguas':'interior','Chitré, Herrera':'interior','Penonomé':'interior','':None,'Calle sin referencia 12':None}
        for text,zone in cases.items():self.assertEqual(detect_zone(text),zone,text)
        t=payment_terms('199.99','panama_oeste');self.assertEqual((t['advance_percent'],t['advance'],t['visit_fee'],t['balance']),('25','50.00','20.00','129.99'))
        t=payment_terms('200.00','ciudad_panama');self.assertEqual((t['advance_percent'],t['advance'],t['visit_fee'],t['balance']),('30','60.00','30.00','110.00'))
        t=payment_terms('1000','interior');self.assertEqual((t['advance'],t['visit_fee'],t['balance'],t['valid_days']),('300.00','50.00','650.00',15))
        self.assertEqual(payment_terms('10','panama_oeste')['balance'],'0.00')  # nunca negativo
        self.assertIsNone(payment_terms('500',None)['visit_fee'])
    def test_quote_zone_from_site_or_client_address_and_approval_gate(self):
        client=self.erp.save('clients',{'name':'Con dirección','address':'Residencial Altos, Arraiján'})
        line=[{'name':'L','quantity':'1','labor':'150'}]
        q=self.erp.quote_save({'name':'A','client_id':client['id'],'lines':line})
        self.assertEqual((q['payment_terms']['zone'],q['payment_terms']['zone_source'],q['payment_terms']['advance_percent']),('panama_oeste','address','25'))
        q=self.erp.quote_save({'site_address':'Boquete, Chiriquí'},q['id'],q['version']);self.assertEqual(q['payment_terms']['zone'],'interior')
        q=self.erp.quote_save({'zone':'ciudad_panama'},q['id'],q['version']);self.assertEqual((q['payment_terms']['zone'],q['payment_terms']['zone_source']),('ciudad_panama','manual'))
        self.assertEqual(q['tax_percent'],'0')  # ITBMS desactivado por defecto
        with self.assertRaises(DomainError):self.erp.quote_save({'zone':'luna'},q['id'],q['version'])
        unknown=self.erp.quote_save({'name':'B','client_id':self.client['id'],'lines':line})
        self.assertIsNone(unknown['payment_terms']['zone'])
        with self.assertRaises(DomainError):self.erp.quote_approve(unknown['id'],unknown['version'])
        self.assertEqual(self.erp.quote_approve(q['id'],q['version'])['status'],'approved')
    def test_form_payload_with_empty_numbers(self):
        # El formulario envía todos los campos como texto, vacíos incluidos (defecto B1).
        work=self.erp.save('works',{'name':'Sin presupuesto','client_id':'','budget':'','responsible':'','start_date':'','end_date':'','status':'active','notes':''})
        self.assertNotIn('budget',work)
        report=self.erp.report(work['id'])
        self.assertEqual(report['works'][0]['budget'],'0.00')
        self.assertEqual(self.erp.dashboard()['alerts'],[])
        worker=self.erp.save('workers',{'name':'Ana','hourly_rate':'5'})
        sheet=self.erp.save('timesheets',{'work_id':work['id'],'worker_id':worker['id'],'date':'2026-11-16','entry':'08:00','exit':'12:00','hours':'','hourly_rate':''})
        self.assertEqual((sheet['hourly_rate'],sheet['cost']),('5','20.00'))
        self.erp.save('expenses',{'name':'Flete','work_id':work['id'],'amount':'30'})
        self.assertEqual(self.erp.report(work['id'])['works'][0]['cost'],'50.00')
        self.assertEqual(self.erp.dashboard()['alerts'],[])  # sin presupuesto no hay alerta de sobrecosto
        funded=self.erp.save('works',{'name':'Con presupuesto','budget':'10'})
        self.erp.save('expenses',{'name':'Exceso','work_id':funded['id'],'amount':'15'})
        self.assertEqual([a['work_id'] for a in self.erp.dashboard()['alerts']],[funded['id']])
        # Registro antiguo que ya guardó "" sigue siendo legible.
        with self.erp.store.connect() as c:
            c.execute("UPDATE erp_records SET payload=json_set(payload,'$.budget','') WHERE id=?",(funded['id'],))
        self.assertEqual(self.erp.report(funded['id'])['works'][0]['budget'],'0.00')
    def test_ui_statuses_match_backend(self):
        # Defectos B2/B3: la interfaz ofrecía estados que el backend rechaza.
        import re
        from jg.erp import STATUSES
        js=(Path(__file__).parent.parent/'jg'/'erp.js').read_text()
        schemas=dict(re.findall(r"(\w+):\[[^\]]*?'status:([a-z_,]+)'",js))
        for kind,allowed in STATUSES.items():
            if kind in schemas:self.assertLessEqual(set(schemas[kind].split(',')),allowed,kind)
        self.assertIn("status:'closed'",js)
        work=self.erp.save('works',{'name':'Obra','status':'active'})
        task=self.erp.save('tasks',{'name':'T','work_id':work['id'],'status':'in_progress'})
        with self.assertRaises(DomainError):self.erp.save('works',{'status':'closed'},work['id'],work['version'])
        self.erp.save('tasks',{'status':'completed'},task['id'],task['version'])
        self.assertEqual(self.erp.save('works',{'status':'closed'},work['id'],work['version'])['status'],'closed')
    def test_versions_and_documents_immutable(self):
        q=self.quote();q=self.erp.quote_save({'name':'Revision'},q['id'],q['version'])
        self.assertEqual(len(self.erp.history('quotes',q['id'])),2)
        with self.assertRaises(DomainError):self.erp.quote_issue(q['id'],q['version'])
        q=self.erp.quote_approve(q['id'],q['version']);doc=self.erp.quote_issue(q['id'],q['version'])
        with self.assertRaises(DomainError):self.erp.save('documents',{'total':'1'},doc['id'],doc['version'])
        with self.assertRaises(DomainError):self.erp.delete('documents',doc['id'],doc['version'])
        with self.assertRaises(DomainError):self.erp.delete('quotes',q['id'],q['version'])

if __name__=='__main__':unittest.main()

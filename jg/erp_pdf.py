"""PDF de cotización para el cliente (ReportLab). Solo presenta datos: nunca recalcula importes aprobados.

Las partidas muestran el precio unitario de VENTA (incluye gastos generales y utilidad, calculado y guardado
por erp.quote_preview). Cotizaciones antiguas sin precio de venta se presentan con el formato anterior.
Las condiciones de pago (cotización y visita por zona, anticipo, saldo) salen de quote['payment_terms'].
"""
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor('#1F3556')
WINE = colors.HexColor('#6A1F35')
LIGHT = colors.HexColor('#F4F6F9')
LINE = colors.HexColor('#D9DFE7')
MUTED = colors.HexColor('#647185')
PANAMA = timezone(timedelta(hours=-5))  # Panamá no usa horario de verano
MONTHS = 'enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre'.split()

DEFAULT_COMPANY = {'name': 'JG Soluciones PA'}


def _dec(value):
    try:
        d = Decimal(str(value if value not in (None, '') else 0))
        return d if d.is_finite() else Decimal(0)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def fmt_money(value):
    d = _dec(value)
    return ('-' if d < 0 else '') + f'{abs(d):,.2f}'


def fmt_qty(value):
    d = _dec(value)
    text = f'{d:,.4f}'.rstrip('0').rstrip('.')
    return text or '0'


def fmt_percent(value):
    return fmt_qty(value) + ' %'


def long_date(moment):
    if moment is None:
        return ''
    if isinstance(moment, (int, float)):
        moment = datetime.fromtimestamp(moment, PANAMA)
    return f'{moment.day} de {MONTHS[moment.month - 1]} de {moment.year}'


def _styles():
    base = dict(fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor('#21334D'))
    return {
        'body': ParagraphStyle('body', **base),
        'small': ParagraphStyle('small', **{**base, 'fontSize': 7.5, 'leading': 9.5, 'textColor': MUTED}),
        'label': ParagraphStyle('label', **{**base, 'fontName': 'Helvetica-Bold', 'fontSize': 7, 'leading': 9, 'textColor': MUTED}),
        'company': ParagraphStyle('company', **{**base, 'fontName': 'Helvetica-Bold', 'fontSize': 15, 'leading': 18, 'textColor': NAVY}),
        'title': ParagraphStyle('title', **{**base, 'fontName': 'Helvetica-Bold', 'fontSize': 17, 'leading': 20, 'textColor': WINE, 'alignment': TA_RIGHT}),
        'right': ParagraphStyle('right', **{**base, 'alignment': TA_RIGHT}),
        'section': ParagraphStyle('section', **{**base, 'fontName': 'Helvetica-Bold', 'fontSize': 9.5, 'leading': 12, 'textColor': NAVY, 'spaceBefore': 4, 'spaceAfter': 4}),
        'cell': ParagraphStyle('cell', **{**base, 'fontSize': 8, 'leading': 10}),
    }


def _p(text, style):
    return Paragraph(escape(str(text)).replace('\n', '<br/>'), style)


def _brand(name, style):
    """Logotipo en texto: J azul marino, G vinotinto (rojo corporativo), resto azul marino."""
    if name.startswith('JG'):
        return Paragraph(f'<font color="#1F3556">J</font><font color="#6A1F35">G</font><font color="#1F3556">{escape(name[2:])}</font>', style)
    return _p(name, style)


def _numbered_canvas(footer, watermark):
    class NumberedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            width, _ = A4
            if watermark:
                self.saveState()
                self.setFont('Helvetica-Bold', 70)
                self.setFillColor(colors.Color(0.42, 0.12, 0.21, alpha=0.08))
                self.translate(width / 2, 150 * mm)
                self.rotate(35)
                self.drawCentredString(0, 0, watermark)
                self.restoreState()
            self.saveState()
            self.setStrokeColor(LINE)
            self.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
            self.setFont('Helvetica', 7)
            self.setFillColor(MUTED)
            self.drawString(18 * mm, 9.5 * mm, footer)
            self.drawRightString(width - 18 * mm, 9.5 * mm, f'Página {self._pageNumber} de {total}')
            self.restoreState()

    return NumberedCanvas


def _payment_block(terms, total, s, width):
    """Condiciones comerciales de JG: cotización y visita técnica por zona, anticipo y saldo."""
    total = _dec(total)
    fee = _dec(terms.get('visit_fee')) if terms.get('visit_fee') is not None else None
    advance = _dec(terms.get('advance'))
    pct = fmt_qty(terms.get('advance_percent'))
    threshold = fmt_money(terms.get('threshold') or '200')
    bullets = []
    if fee is not None:
        bullets.append(f'Cotización y visita técnica ({terms.get("zone_label")}): USD {fmt_money(fee)}. En la visita técnica se revisa el trabajo, se recopila la información necesaria y se planifica su ejecución. '
                       'Si contrata el trabajo, este monto se descuenta del precio final; si no lo contrata, se paga al entregar esta cotización.')
    bullets.append(f'Anticipo para iniciar el trabajo: {pct} % del total (25 % en trabajos menores de USD {threshold} y 30 % en trabajos de USD {threshold} o más).')
    rows = [['Total del trabajo', fmt_money(total)], [f'Anticipo para inicio ({pct} %)', fmt_money(advance)]]
    if fee is not None:
        rows.append(['Cotización y visita técnica (se descuenta)', '-' + fmt_money(fee)])
    rows.append(['Saldo contra entrega', fmt_money(terms.get('balance'))])
    table = Table(rows, colWidths=[62 * mm, 30 * mm], hAlign='LEFT')
    table.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 8.5), ('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('LINEBELOW', (0, 0), (-1, -2), 0.4, LINE),
                               ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4), ('LEFTPADDING', (0, 0), (-1, -1), 6),
                               ('BACKGROUND', (0, -1), (-1, -1), LIGHT), ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('TEXTCOLOR', (0, -1), (-1, -1), NAVY),
                               ('BOX', (0, 0), (-1, -1), 0.5, LINE)]))
    return [KeepTogether([_p('Condiciones de pago si contrata', s['section'])] + [_p('• ' + b, s['body']) for b in bullets] + [Spacer(1, 2 * mm), table]), Spacer(1, 3 * mm)]


def quote_pdf(quote, client=None, company=None, number=None, issued_at=None, work_name='', watermark=None):
    """Devuelve los bytes del PDF. `quote` es la instantánea guardada de la cotización."""
    s = _styles()
    company = {**DEFAULT_COMPANY, **{k: v for k, v in (company or {}).items() if v}}
    client = client or {}
    totals = quote.get('totals') or {}
    issued = issued_at if issued_at is not None else datetime.now(PANAMA)
    issued_dt = datetime.fromtimestamp(issued, PANAMA) if isinstance(issued, (int, float)) else issued
    valid_days = int(_dec(quote.get('valid_days') or company.get('valid_days') or 15)) or 15
    terms = quote.get('payment_terms') or {}
    sale = any('sale_unit_price' in l for l in quote.get('lines') or [])
    number = number or 'Sin emitir'
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
                            title=f'Cotización {number}', author=company['name'], subject=quote.get('name', ''))
    width = A4[0] - 36 * mm
    story = []

    # Encabezado: empresa / documento
    company_lines = [_brand(company['name'], s['company'])]
    if company.get('ruc'):
        company_lines.append(_p(f'RUC {company["ruc"]}' + (f' DV {company["dv"]}' if company.get('dv') else ''), s['small']))
    for key, label in (('address', ''), ('phone', 'Tel.'), ('email', '')):
        if company.get(key):
            company_lines.append(_p(f'{label} {company[key]}'.strip(), s['small']))
    doc_lines = [_p('COTIZACIÓN', s['title']), _p(f'N.º {number}', s['right']), _p(f'Fecha: {long_date(issued_dt)}', s['right']),
                 _p(f'Válida hasta: {long_date(issued_dt + timedelta(days=valid_days))}', s['right'])]
    if quote.get('version'):
        doc_lines.append(_p(f'Versión aprobada {quote["version"]}', ParagraphStyle('rv', parent=s['small'], alignment=TA_RIGHT)))
    head = Table([[company_lines, doc_lines]], colWidths=[width * 0.55, width * 0.45])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                              ('LINEBELOW', (0, 0), (-1, 0), 1.6, WINE), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
    story += [head, Spacer(1, 7 * mm)]

    # Cliente / proyecto
    def box(title, rows):
        content = [_p(title.upper(), s['label'])] + [_p(v, s['body']) for v in rows if v]
        return content
    client_rows = [client.get('name') or 'Cliente sin nombre', client.get('identification') and f'RUC / Cédula: {client["identification"]}',
                   client.get('contact') and f'Atención: {client["contact"]}', client.get('address'),
                   ' · '.join(x for x in (client.get('phone') or client.get('whatsapp'), client.get('email')) if x)]
    project_rows = [quote.get('name', ''), work_name and f'Obra: {work_name}', f'Moneda: USD']
    parties = Table([[box('Cliente', client_rows), box('Proyecto', project_rows)]], colWidths=[width * 0.5 - 2 * mm, width * 0.5 - 2 * mm], spaceBefore=0)
    parties.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BACKGROUND', (0, 0), (-1, -1), LIGHT), ('BOX', (0, 0), (0, 0), 0.5, LINE),
                                 ('BOX', (1, 0), (1, 0), 0.5, LINE), ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                                 ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
    story += [parties, Spacer(1, 5 * mm)]

    # Partidas
    header = ['#', 'Descripción', 'Cant.', 'Unidad', 'Precio unitario', 'Subtotal']
    rows = [header]
    for index, line in enumerate(quote.get('lines') or [], 1):
        unit_price, subtotal = (line.get('sale_unit_price'), line.get('sale_subtotal')) if sale else (line.get('unit_price'), line.get('subtotal'))
        rows.append([str(index), _p(line.get('name', ''), s['cell']), fmt_qty(line.get('quantity')), str(line.get('unit') or '')[:12],
                     fmt_money(unit_price), fmt_money(subtotal)])
    widths = [8 * mm, width - 8 * mm - 18 * mm - 18 * mm - 26 * mm - 28 * mm, 18 * mm, 18 * mm, 26 * mm, 28 * mm]
    table = Table(rows, colWidths=widths, repeatRows=1)
    style = [('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
             ('FONTSIZE', (0, 0), (-1, -1), 8), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
             ('ALIGN', (4, 0), (-1, -1), 'RIGHT'), ('ALIGN', (0, 0), (0, -1), 'CENTER'), ('TOPPADDING', (0, 0), (-1, -1), 5),
             ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINE)]
    for r in range(2, len(rows), 2):
        style.append(('BACKGROUND', (0, r), (-1, r), LIGHT))
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 5 * mm)]

    # Totales (solo valores guardados)
    overhead = _dec(totals.get('indirect')) + _dec(totals.get('margin'))
    total_rows = []
    if not sale:  # formato anterior: el recargo aparecía en una línea aparte
        total_rows.append(['Costo directo de partidas', fmt_money(totals.get('direct'))])
        if overhead:
            total_rows.append(['Gastos generales y utilidad', fmt_money(overhead)])
    total_rows.append(['Subtotal', fmt_money(totals.get('subtotal'))])
    if _dec(totals.get('discount')):
        total_rows.append([f'Descuento ({fmt_percent(quote.get("discount_percent"))})', '-' + fmt_money(totals.get('discount'))])
    if _dec(totals.get('tax')):  # ITBMS solo cuando aplica
        total_rows.append([f'ITBMS ({fmt_percent(quote.get("tax_percent"))})', fmt_money(totals.get('tax'))])
    total_rows.append(['TOTAL USD', fmt_money(totals.get('total', quote.get('total')))])
    tot = Table(total_rows, colWidths=[52 * mm, 32 * mm], hAlign='RIGHT')
    tot.setStyle(TableStyle([('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('FONTSIZE', (0, 0), (-1, -1), 8.5), ('TEXTCOLOR', (0, 0), (-1, -2), colors.HexColor('#21334D')),
                             ('LINEBELOW', (0, 0), (-1, -2), 0.4, LINE), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                             ('BACKGROUND', (0, -1), (-1, -1), NAVY), ('TEXTCOLOR', (0, -1), (-1, -1), colors.white),
                             ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('FONTSIZE', (0, -1), (-1, -1), 10.5),
                             ('TOPPADDING', (0, -1), (-1, -1), 7), ('BOTTOMPADDING', (0, -1), (-1, -1), 7), ('LEFTPADDING', (0, 0), (-1, -1), 6)]))
    story += [KeepTogether([tot]), Spacer(1, 5 * mm)]

    # Alcance y condiciones
    if quote.get('notes'):
        story += [KeepTogether([_p('Alcance y observaciones', s['section']), _p(quote['notes'], s['body'])]), Spacer(1, 4 * mm)]
    if terms:
        story += _payment_block(terms, totals.get('total', quote.get('total')), s, width)
    extra = [c for c in str(company.get('conditions') or '').splitlines() if c.strip()]
    conditions = [f'Validez: esta cotización es válida por {valid_days} días a partir de su entrega. Vencido este plazo, deberá solicitarse una actualización.',
                  'Precios expresados en dólares de los Estados Unidos (USD).'] + extra + [
        'Trabajos fuera del alcance descrito se cotizan por separado mediante orden de cambio.',
        'Este documento es una cotización y no constituye factura fiscal.']
    story += [KeepTogether([_p('Condiciones generales', s['section'])] + [_p('• ' + c, s['body']) for c in conditions]), Spacer(1, 6 * mm)]

    # Aceptación
    sign = Table([['', '', ''], [_p(f'Por {company["name"]}', s['small']), '', _p('Aceptación del cliente: nombre, firma y fecha', s['small'])]],
                 colWidths=[width * 0.42, width * 0.16, width * 0.42], rowHeights=[10 * mm, None])
    sign.setStyle(TableStyle([('LINEABOVE', (0, 1), (0, 1), 0.6, NAVY), ('LINEABOVE', (2, 1), (2, 1), 0.6, NAVY), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([sign]))

    footer = f'{company["name"]} · Cotización {number} · Documento no fiscal'
    doc.build(story, canvasmaker=_numbered_canvas(footer, watermark))
    return out.getvalue()

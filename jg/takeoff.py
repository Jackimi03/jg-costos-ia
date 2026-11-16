"""Deterministic resource takeoffs from explicitly supplied, unverified coefficients.

Numbers are decimal strings (at most 12 fractional places, magnitude <= 10^12).
Quantities and costs are exact, unrounded decimal strings; consumers may format
currency for display separately. No recipes or resource prices are inferred.
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
import re

_CATEGORIES = ('material', 'labor', 'equipment')
_UNITS = frozenset(('L', 'l', 'ml', 'gal', 'kg', 'g', 't', 'm', 'm2', 'm²', 'm3', 'm³',
                    'cm', 'mm', 'ft', 'ft2', 'ft3', 'h', 'hr', 'hora', 'horas', 'día',
                    'dia', 'd', 'jornal', 'jornada', 'ud', 'u', 'un', 'und', 'unidad',
                    'pza', 'pieza', 'saco', 'bolsa', 'rollo', 'caja', 'global'))
_NUMBER = re.compile(r'^[+]?(?:\d{1,13})(?:\.\d{1,12})?$')


def _number(value, field, *, zero=False, maximum=Decimal('1000000000000')):
    if not isinstance(value, str) or not _NUMBER.fullmatch(value):
        raise ValueError(f'{field} must be a bounded decimal string')
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f'Invalid {field}') from exc
    if not number.is_finite() or number < 0 or (not zero and number == 0) or number > maximum:
        raise ValueError(f'{field} is out of range')
    return number


def _text(value, field, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f'Invalid {field}')
    return value.strip()


def _format(value):
    rendered = format(value, 'f')
    return rendered.rstrip('0').rstrip('.') if '.' in rendered else rendered


def calculate_takeoff(area: str, resources: list[dict]) -> dict:
    """Return quantities, package purchases and category costs; unknown costs stay null.

    Each resource requires name, category, unit, coefficient_per_unit and
    waste_percent. Optional package_size and unit_price may be null. Source is
    carried through as a label; no supplied source marks an estimate verified.
    An empty category has cost "0"; any unpriced resource makes its category null.
    """
    area_value = _number(area, 'area', maximum=Decimal('1000000'))
    if not isinstance(resources, list) or len(resources) > 200:
        raise ValueError('resources must be a list of at most 200 entries')
    totals = {category: Decimal(0) for category in _CATEGORIES}
    unknown = set()
    items = []
    with localcontext() as context:
        context.prec = 128
        for resource in resources:
            if not isinstance(resource, dict):
                raise ValueError('Each resource must be an object')
            name = _text(resource.get('name'), 'name')
            category = resource.get('category')
            if not isinstance(category, str) or category not in _CATEGORIES:
                raise ValueError('Invalid category')
            unit = _text(resource.get('unit'), 'unit', 20)
            if unit not in _UNITS:
                raise ValueError('Unsupported unit')
            coefficient = _number(resource.get('coefficient_per_unit'), 'coefficient_per_unit')
            waste = _number(resource.get('waste_percent'), 'waste_percent', zero=True, maximum=Decimal(100))
            quantity = area_value * coefficient * (1 + waste / 100)
            packages = None
            purchased = quantity
            package = resource.get('package_size')
            if package is not None:
                package = _number(package, 'package_size')
                packages = int((quantity / package).to_integral_value(rounding=ROUND_CEILING))
                purchased = packages * package
            price = resource.get('unit_price')
            cost = None
            if price is None:
                unknown.add(category)
            else:
                price = _number(price, 'unit_price', zero=True)
                cost = purchased * price
                totals[category] += cost
            source = resource.get('source')
            source = 'user_provided' if source is None else _text(source, 'source', 1000)
            items.append(dict(name=name, category=category, unit=unit,
                              coefficient_per_unit=_format(coefficient), waste_percent=_format(waste),
                              package_size=None if package is None else _format(package),
                              unit_price=None if price is None else _format(price),
                              quantity=_format(quantity), packages=packages,
                              purchase_quantity=_format(purchased), cost=None if cost is None else _format(cost),
                              source=source, verification='unverified'))
    return dict(area=_format(area_value), resources=items, verification='unverified',
                materials_cost=None if 'material' in unknown else _format(totals['material']),
                labor_cost=None if 'labor' in unknown else _format(totals['labor']),
                equipment_cost=None if 'equipment' in unknown else _format(totals['equipment']))

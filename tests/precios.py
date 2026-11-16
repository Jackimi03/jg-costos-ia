"""Marca las pruebas que verifican los precios del archivo de referencia PRIVADO de JG.

En la copia pública (archivo de ejemplo con precios ficticios) estas pruebas se retiran al exportar
(tools/exportar_publico.py) para no publicar las tarifas reales; si se ejecutan con otro archivo, se omiten.
"""
import json
import unittest
from pathlib import Path

_META = json.loads((Path(__file__).resolve().parent.parent / 'fixtures' / 'source.json').read_text(encoding='utf-8'))
REAL_PRICES = 'ARCHIVO DE EJEMPLO' not in _META.get('handling', '')
requires_real_prices = unittest.skipUnless(REAL_PRICES, 'Requiere el archivo de precios privado de JG')

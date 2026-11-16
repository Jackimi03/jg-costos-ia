import unittest
from jg.takeoff import calculate_takeoff


def resource(**changes):
    value = dict(name='Pintura', category='material', unit='L',
                 coefficient_per_unit='0.2', waste_percent='10', package_size='3.8')
    value.update(changes)
    return value


class TakeoffTests(unittest.TestCase):
    def test_paint_packages_and_cost_use_purchase_quantity(self):
        result = calculate_takeoff('70', [resource(unit_price='12.5', source='Ficha del fabricante')])
        item = result['resources'][0]
        self.assertEqual((item['quantity'], item['packages'], item['purchase_quantity'], item['cost']),
                         ('15.4', 5, '19', '237.5'))
        self.assertEqual(result['materials_cost'], '237.5')
        self.assertEqual(item['source'], 'Ficha del fabricante')
        self.assertEqual(item['verification'], 'unverified')

    def test_unknown_price_propagates_only_within_category(self):
        result = calculate_takeoff('70', [resource(), resource(unit_price='2'),
            resource(name='Pintor', category='labor', unit='h', coefficient_per_unit='0.1',
                     waste_percent='0', package_size=None, unit_price='10')])
        self.assertIsNone(result['materials_cost'])
        self.assertIsNone(result['resources'][0]['cost'])
        self.assertEqual(result['labor_cost'], '70')
        self.assertEqual(result['equipment_cost'], '0')

    def test_fractional_quantities_remain_exact_and_packages_round_up(self):
        result = calculate_takeoff('0.3', [resource(coefficient_per_unit='0.1', waste_percent='0', package_size='0.01')])
        self.assertEqual(result['resources'][0]['quantity'], '0.03')
        self.assertEqual(result['resources'][0]['packages'], 3)
        result = calculate_takeoff('0.301', [resource(coefficient_per_unit='0.1', waste_percent='0', package_size='0.01')])
        self.assertEqual(result['resources'][0]['packages'], 4)
        result = calculate_takeoff('2', [resource(package_size=None)])
        self.assertEqual(result['resources'][0]['purchase_quantity'], '0.44')
        self.assertIsNone(result['resources'][0]['packages'])

    def test_rejects_invalid_input_without_overflow(self):
        for area in ['0', '-1', 'NaN', 'Infinity', '1000001', '1e999999', 70, True]:
            with self.subTest(area=area), self.assertRaises(ValueError):
                calculate_takeoff(area, [resource()])
        for changes in [dict(coefficient_per_unit='0'), dict(coefficient_per_unit='-1'),
                        dict(coefficient_per_unit='NaN'), dict(package_size='0'),
                        dict(package_size='1e-999999'), dict(waste_percent='-1'),
                        dict(waste_percent='101'), dict(unit_price='-2'), dict(unit_price='Infinity'),
                        dict(unit=''), dict(unit='bogus'), dict(category='service'), dict(name='')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                calculate_takeoff('70', [resource(**changes)])
        with self.assertRaises(ValueError):
            calculate_takeoff('70', [resource()] * 201)
        with self.assertRaises(ValueError):
            calculate_takeoff('70', [{}])

    def test_no_default_recipe_and_zero_price_is_known(self):
        self.assertEqual(calculate_takeoff('70', [])['resources'], [])
        result = calculate_takeoff('1', [resource(unit_price='0')])
        self.assertEqual(result['materials_cost'], '0')
        self.assertEqual(result['verification'], 'unverified')

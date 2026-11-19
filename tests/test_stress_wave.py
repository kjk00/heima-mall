"""Verify stress results distinguish admission failures from corrupted data."""
import sqlite3
import unittest
from unittest.mock import patch

from scripts.stress_purchase_wave import run_scenario


class StressWaveTests(unittest.TestCase):
    def test_all_virtual_buyers_are_submitted_and_purchase_when_stock_allows(self):
        result = run_scenario(users=12, stock=12, workers=3)
        self.assertEqual(result['buyers_submitted_before_release'], 12)
        self.assertLessEqual(result['peak_in_flight_requests'], 3)
        self.assertEqual(result['checkout_statuses'], {201: 12})
        self.assertEqual(result['payment_statuses'], {200: 12})
        self.assertEqual(result['validation']['final_stock'], 0)
        self.assertTrue(result['passed'])

    def test_last_item_allows_one_purchase_and_expected_business_rejections(self):
        result = run_scenario(users=12, stock=1, workers=3)
        self.assertEqual(result['checkout_statuses'], {201: 1, 409: 11})
        self.assertEqual(result['validation']['paid_orders'], 1)
        self.assertEqual(result['validation']['sales'], 1)
        self.assertEqual(result['validation']['remaining_carts'], 11)
        self.assertTrue(result['passed'])

    def test_checkout_lock_failures_fail_capacity_without_claiming_inventory_corruption(self):
        with patch('app.logic.checkout', side_effect=sqlite3.OperationalError('database is locked')):
            result = run_scenario(users=12, stock=12, workers=3)
        self.assertEqual(result['checkout_statuses'], {503: 12})
        self.assertEqual(result['unexpected_failures'], 12)
        self.assertEqual(result['validation']['final_stock'], 12)
        self.assertTrue(result['validation']['consistency_passed'])
        self.assertFalse(result['validation']['capacity_passed'])
        self.assertFalse(result['passed'])

    def test_failed_payments_remain_pending_and_do_not_count_as_sales(self):
        with patch('app.logic.pay', side_effect=sqlite3.OperationalError('database is locked')):
            result = run_scenario(users=12, stock=12, workers=3)
        self.assertEqual(result['checkout_statuses'], {201: 12})
        self.assertEqual(result['payment_statuses'], {503: 12})
        self.assertEqual(result['validation']['paid_orders'], 0)
        self.assertEqual(result['validation']['sales'], 0)
        self.assertEqual(result['validation']['final_stock'], 0)
        self.assertTrue(result['validation']['consistency_passed'])
        self.assertTrue(result['validation']['capacity_passed'])
        self.assertFalse(result['passed'])


if __name__ == '__main__':
    unittest.main()

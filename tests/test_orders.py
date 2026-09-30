import unittest
from datetime import date

from fastapi import HTTPException

from amazon_cart.orders import parse_orders, parse_return_window, return_reminders


class OrderParserTests(unittest.TestCase):
    def test_multiple_items_and_next_page(self):
        html = '''
        <select name="timeFilter"><option value="year-2025">2025</option></select>
        <div class="order-card">
          <div class="order-header">
            <span class="a-size-base a-color-secondary aok-break-word">January 2, 2025</span>
            <span class="a-size-base a-color-secondary aok-break-word">$25.00</span>
          </div>
          <div class="yohtmlc-order-id">Order # 123-1234567-1234567</div>
          <div class="yohtmlc-shipment-status-primaryText">Delivered</div>
          <div class="yohtmlc-product-title"><a href="/dp/B000000001?ref=x">Cable &amp; adapter</a></div>
          <div class="yohtmlc-product-title"><a href="/gp/product/B000000002">Charger</a></div>
        </div>
        <ul class="a-pagination"><li class="a-last"><a href="/your-orders/orders?page=1">Next</a></li></ul>
        '''
        result = parse_orders(html, 'amazon.com')
        order = result['orders'][0]
        self.assertEqual(order['order_id'], '123-1234567-1234567')
        self.assertEqual(order['date'], 'January 2, 2025')
        self.assertEqual(order['total'], '$25.00')
        self.assertEqual(order['statuses'], ['Delivered'])
        self.assertEqual([i['asin'] for i in order['items']], ['B000000001', 'B000000002'])
        self.assertEqual(order['items'][0]['title'], 'Cable & adapter')
        self.assertEqual(result['next_page'], 2)
        self.assertEqual(result['available_filters'], ['year-2025'])

    def test_confirmed_empty_history(self):
        result = parse_orders('<div class="your-orders-content-container">You have not placed any orders.</div>', 'amazon.com')
        self.assertEqual(result['orders'], [])
        self.assertIsNone(result['next_page'])

    def test_each_item_keeps_its_own_deadline(self):
        html = '''<div class="order-card">
        <div class="yohtmlc-order-id">123-1234567-1234567</div>
        <div class="item-box"><div class="yohtmlc-product-title"><a href="/dp/B000000001">Cable</a></div>
        Return items: <span class="a-size-small">Eligible through October 2, 2026</span></div>
        <div class="yo-enhanced-card"><div class="yo-enhanced-title"><a href="/dp/B000000002">Charger</a></div>
        <div class="yo-enhanced-return">Return window closed on September 23, 2026</div></div>
        <div class="yo-enhanced-grid-cell"><a href="/dp/B000000003"><img alt="Adapter"></a></div>
        </div>'''
        items = parse_orders(html, 'amazon.com')['orders'][0]['items']
        self.assertEqual([i['return_window']['deadline'] for i in items], ['2026-10-02', '2026-09-23', None])
        self.assertEqual([i['return_window']['status'] for i in items], ['eligible', 'closed', 'unknown'])
        self.assertEqual(items[2]['title'], 'Adapter')

    def test_product_title_is_not_return_metadata(self):
        html = '''<div class="order-card"><div class="yohtmlc-order-id">123-1234567-1234567</div>
        <div class="item-box"><div class="yohtmlc-product-title"><a href="/dp/B000000001">
        Eligible through October 2, 2026 Refunded</a></div></div></div>'''
        item = parse_orders(html, 'amazon.com')['orders'][0]['items'][0]
        self.assertEqual(item['return_window']['status'], 'unknown')
        self.assertIsNone(item['return_state'])

    def test_deadlines_are_never_inferred_or_invalid_dates_accepted(self):
        self.assertIsNone(parse_return_window('Delivered September 24')['deadline'])
        self.assertEqual(parse_return_window('Eligible through February 30, 2026')['status'], 'unknown')

    def test_reminder_milestones_catchup_and_stable_ids(self):
        item = {'asin': 'B000000001', 'title': 'Cable', 'return_state': None,
                'return_window': {'status': 'eligible', 'deadline': '2027-01-02', 'source_text': 'Eligible through January 2, 2027'}}
        order = {'order_id': '123-1234567-1234567', 'date': 'December 1, 2026',
                 'order_url': 'https://www.amazon.com/your-orders/order-details', 'items': [item]}
        before = return_reminders([order], date(2026, 12, 25), [7, 2])
        self.assertEqual(before['reminders'], [])
        first = return_reminders([order], date(2026, 12, 26), [7, 2])['reminders'][0]
        self.assertEqual(first['days_remaining'], 7)
        self.assertFalse(first['due_reminders'][0]['catch_up'])
        second = return_reminders([order], date(2026, 12, 31), [7, 2])['reminders'][0]
        self.assertEqual([r['days_before'] for r in second['due_reminders']], [7, 2])
        self.assertEqual(first['due_reminders'][0]['reminder_id'], second['due_reminders'][0]['reminder_id'])
        self.assertNotEqual(second['due_reminders'][0]['reminder_id'], second['due_reminders'][1]['reminder_id'])
        self.assertTrue(second['due_reminders'][0]['catch_up'])
        self.assertFalse(second['due_reminders'][1]['catch_up'])
        self.assertEqual(return_reminders([order], date(2027, 1, 3), [7, 2])['reminders'], [])
        item['return_state'] = 'returned'
        self.assertEqual(return_reminders([order], date(2026, 12, 31), [7, 2])['reminders'], [])

    def test_sign_in_and_unreadable_pages_do_not_become_empty_history(self):
        for html, status in [('<input id="ap_password">', 409),
                             ('<div class="csd-encrypted-sensitive"></div>', 503),
                             ('<html>Unexpected response</html>', 503),
                             ('<div class="order-card"></div>', 503)]:
            with self.subTest(html=html):
                with self.assertRaises(HTTPException) as caught:
                    parse_orders(html, 'amazon.com')
                self.assertEqual(caught.exception.status_code, status)


if __name__ == '__main__':
    unittest.main()

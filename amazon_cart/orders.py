"""Read-only parsing of Amazon's order-history fallback HTML."""

import hashlib
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from fastapi import HTTPException


def parse_return_window(text: str) -> dict:
    """Use the stated date, never an estimate based on the purchase date."""
    pattern = r'(Eligible through|Return window closed on)\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})'
    match = re.search(pattern, text, re.I)
    if match:
        try:
            deadline = datetime.strptime(match.group(2), '%B %d, %Y').date().isoformat()
        except ValueError:
            return {'status': 'unknown', 'deadline': None, 'source_text': match.group(0)}
        status = 'closed' if match.group(1).lower().startswith('return window') else 'eligible'
        return {'status': status, 'deadline': deadline, 'source_text': match.group(0)}
    if re.search(r'not eligible for return|non.returnable', text, re.I):
        return {'status': 'not_returnable', 'deadline': None, 'source_text': None}
    return {'status': 'unknown', 'deadline': None, 'source_text': None}


def return_reminders(orders: list[dict], today: date, reminder_days: list[int]) -> dict:
    reminders = []
    unknown = []
    known = 0
    for order in orders:
        for item in order['items']:
            window = item['return_window']
            entry = {'order_id': order['order_id'], 'order_date': order['date'],
                     'order_url': order['order_url'], **item}
            if window['status'] == 'unknown':
                unknown.append(entry)
            if not window['deadline']:
                continue
            known += 1
            deadline = date.fromisoformat(window['deadline'])
            remaining = (deadline - today).days
            if window['status'] != 'eligible' or remaining < 0 or item.get('return_state'):
                continue
            due = []
            for days in reminder_days:
                notify_on = deadline - timedelta(days=days)
                if notify_on > today:
                    continue
                identity = '|'.join([order['order_id'], item.get('asin') or '',
                                     item['title'], deadline.isoformat(), str(days)])
                due.append({'reminder_id': hashlib.sha256(identity.encode()).hexdigest(),
                            'days_before': days, 'scheduled_date': notify_on.isoformat(),
                            'catch_up': notify_on < today})
            if due:
                reminders.append({**entry, 'deadline': deadline.isoformat(),
                                  'days_remaining': remaining, 'due_reminders': due})
    return {'reminders': reminders, 'known_deadline_count': known,
            'unknown_deadline_count': len(unknown), 'unknown_items': unknown}


def parse_orders(html: str, domain: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('#ap_email, #ap_password, form[name="signIn"]'):
        raise HTTPException(409, "Amazon requires a fresh order-history login. Refresh the Chrome cookie export.")
    if soup.select_one('.csd-encrypted-sensitive'):
        raise HTTPException(503, "Amazon returned encrypted order cards instead of the fallback view.")
    cards = soup.select('.order-card')
    orders = []
    for card in cards:
        id_element = card.select_one('.yohtmlc-order-id')
        id_match = re.search(r'\b\d{3}-\d{7}-\d{7}\b', id_element.get_text(' ', strip=True)) if id_element else None
        if not id_match:
            raise HTTPException(503, "Amazon returned an unreadable order card.")
        order_id = id_match.group()
        header_values = card.select('.order-header .a-size-base.a-color-secondary.aok-break-word')
        items = []
        containers = card.select('.item-box, .yo-enhanced-card, .yo-enhanced-grid-cell')
        if not containers:
            containers = card.select('.yohtmlc-product-title, .yo-enhanced-title')
        for container in containers:
            element = container.select_one('.yohtmlc-product-title, .yo-enhanced-title')
            if element is None and {'yohtmlc-product-title', 'yo-enhanced-title'}.intersection(container.get('class', [])):
                element = container
            link = (element if element.name == 'a' else element.select_one('a[href]')) if element else None
            title = element.get_text(' ', strip=True) if element else ''
            if not title:
                image = container.select_one('img[alt]')
                title = image.get('alt', '').strip() if image else ''
                link = container.select_one('a[href*="/dp/"], a[href*="/gp/product/"]')
            if not title:
                continue
            asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', link.get('href', '')) if link else None
            asin = asin_match.group(1) if asin_match else None
            text = ' '.join(node.get_text(' ', strip=True) for node in
                            container.select('.a-size-small, .yo-enhanced-return'))
            return_state = None
            if re.search(r'return complete|refund issued|refunded|return received', text, re.I):
                return_state = 'returned'
            elif re.search(r'return started|return requested', text, re.I):
                return_state = 'return_in_progress'
            items.append({'title': title, 'asin': asin,
                          'url': f'https://www.{domain}/dp/{asin}' if asin else None,
                          'return_window': parse_return_window(text), 'return_state': return_state})
        if not items:
            raise HTTPException(503, "Amazon returned an order card without readable items.")
        statuses = [e.get_text(' ', strip=True) for e in card.select('.yohtmlc-shipment-status-primaryText')]
        orders.append({
            'order_id': order_id,
            'date': header_values[0].get_text(' ', strip=True) if header_values else None,
            'total': header_values[1].get_text(' ', strip=True) if len(header_values) > 1 else None,
            'statuses': statuses,
            'items': items,
            'order_url': f'https://www.{domain}/your-orders/order-details?orderID={order_id}',
        })
    if not cards:
        text = soup.get_text(' ', strip=True).lower()
        empty = re.search(r'\b(?:no|0) orders\b|haven.t placed (?:any |an )?orders?|have not placed (?:any |an )?orders?', text)
        if not soup.select_one('.your-orders-content-container') or not empty:
            raise HTTPException(503, "Amazon returned an unreadable order-history page.")
    next_page = None
    next_link = soup.select_one('.a-pagination .a-last:not(.a-disabled) a[href]')
    if next_link:
        page_values = parse_qs(urlsplit(next_link['href']).query).get('page', [])
        if page_values and page_values[0].isdigit():
            next_page = int(page_values[0]) + 1
    filters = [option.get('value') for option in soup.select('select[name="timeFilter"] option') if option.get('value')]
    return {'orders': orders, 'order_count': len(orders), 'next_page': next_page,
            'available_filters': filters}

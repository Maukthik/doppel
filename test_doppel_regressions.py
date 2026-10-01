"""Regression tests written by Doppel from behaviors that changed in a pull request.

Each test replays the synthetic user that saw the change and expects what the base code answered.
Run against a live app:  DOPPEL_BASE_URL=http://127.0.0.1:8000 pytest test_doppel_regressions.py
(the app must start from the same seeded data Doppel used).
"""
import os

import pytest

from doppel.runner import extract, replay

BASE_URL = os.environ.get("DOPPEL_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="set DOPPEL_BASE_URL to the running app")


def test_careless_clicker_step0_post__orders():
    """Removed validation `if int(item.get('qty', 0)) <= 0: return self.send(400, {'error': 'qty must be at least 1'})` in POST /orders allows qty<=0 to proceed, changing 400 to 201."""
    persona = {'name': 'careless clicker', 'vars': {'user': 3}, 'steps': [{'method': 'POST', 'path': '/orders', 'json': {'user_id': '{user}', 'items': [{'product_id': 7, 'qty': 0}]}}]}
    step = replay(BASE_URL, [persona])[0]["steps"][0]
    assert step['status'] == 400, step


def test_coupon_buyer_step0_post__orders():
    """Refactored `order_total` to apply tax before coupon discount (`with_tax - subtotal * COUPONS.get(coupon or '', 0) // 100`) instead of coupon before tax, changing totals for coupon buyers."""
    persona = {'name': 'coupon buyer', 'vars': {'user': 1}, 'steps': [{'method': 'POST', 'path': '/orders', 'json': {'user_id': '{user}', 'items': [{'product_id': 2, 'qty': 1}], 'coupon': 'SAVE10'}, 'save': {'order': 'id'}}]}
    step = replay(BASE_URL, [persona])[0]["steps"][0]
    assert extract(step['body'], 'total') == 371593, step['body']


def test_coupon_buyer_step1_get__orders_1():
    """Refactored `order_total` to apply tax before coupon discount (`with_tax - subtotal * COUPONS.get(coupon or '', 0) // 100`) instead of coupon before tax, changing totals for coupon buyers."""
    persona = {'name': 'coupon buyer', 'vars': {'user': 1}, 'steps': [{'method': 'POST', 'path': '/orders', 'json': {'user_id': '{user}', 'items': [{'product_id': 2, 'qty': 1}], 'coupon': 'SAVE10'}, 'save': {'order': 'id'}}, {'method': 'GET', 'path': '/orders/{order}'}]}
    step = replay(BASE_URL, [persona])[0]["steps"][1]
    assert extract(step['body'], 'total') == 371593, step['body']

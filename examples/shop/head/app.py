"""A small shop API (standard library only) that Doppel's tests run as the app under test.

Prices are integers in paise. Listens on $PORT (default 8000).
"""
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop.db")
TAX_PCT = 18
COUPONS = {"SAVE10": 10}


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def product_json(row):
    return {"id": row["id"], "name": row["name"], "price": row["price"], "in_stock": row["stock"] > 0}


def order_total(subtotal, coupon):
    """Add GST, then take the coupon off."""
    with_tax = subtotal + subtotal * TAX_PCT // 100
    return with_tax - subtotal * COUPONS.get(coupon or "", 0) // 100


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        with connect() as conn:
            if parts == ["health"]:
                return self.send(200, {"ok": True})
            if parts == ["products"]:
                q = parse_qs(url.query).get("q", [""])[0]
                rows = conn.execute("SELECT * FROM products WHERE name LIKE ? ORDER BY id", (f"%{q}%",))
                return self.send(200, [product_json(r) for r in rows])
            if len(parts) == 2 and parts[0] == "products" and parts[1].isdigit():
                row = conn.execute("SELECT * FROM products WHERE id = ?", (int(parts[1]),)).fetchone()
                return self.send(200, product_json(row)) if row else self.send(404, {"error": "no such product"})
            if len(parts) == 2 and parts[0] == "orders" and parts[1].isdigit():
                row = conn.execute("SELECT * FROM orders WHERE id = ?", (int(parts[1]),)).fetchone()
                if not row:
                    return self.send(404, {"error": "no such order"})
                return self.send(200, {"id": row["id"], "user_id": row["user_id"], "items": json.loads(row["items"]),
                                       "subtotal": row["subtotal"], "total": row["total"], "coupon": row["coupon"]})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/orders":
            return self.send(404, {"error": "not found"})
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.send(400, {"error": "invalid JSON"})
        items, coupon = data.get("items") or [], data.get("coupon")
        if not items:
            return self.send(400, {"error": "order has no items"})
        with connect() as conn:
            subtotal = 0
            for item in items:
                row = conn.execute("SELECT * FROM products WHERE id = ?", (item.get("product_id"),)).fetchone()
                if not row:
                    return self.send(404, {"error": f"no such product {item.get('product_id')}"})
                if row["stock"] < item["qty"]:
                    return self.send(409, {"error": f"only {row['stock']} left of {row['name']}"})
                subtotal += row["price"] * item["qty"]
            for item in items:
                conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (item["qty"], item["product_id"]))
            total = order_total(subtotal, coupon)
            cur = conn.execute("INSERT INTO orders (user_id, items, subtotal, total, coupon) VALUES (?, ?, ?, ?, ?)",
                               (data.get("user_id"), json.dumps(items), subtotal, total, coupon))
            conn.commit()  # commit before answering, or the client's next read can race the write
            self.send(201, {"id": cur.lastrowid, "user_id": data.get("user_id"), "items": items,
                            "subtotal": subtotal, "total": total, "coupon": coupon})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

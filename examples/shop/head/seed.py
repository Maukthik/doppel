"""Create shop.db with a fixed catalog. Deterministic, so both worlds start identical."""
import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop.db")

PRODUCTS = [
    ("Desk lamp", 129900, 12), ("Floor lamp", 349900, 3), ("Notebook", 9900, 200),
    ("Fountain pen", 189900, 7), ("Ink bottle", 34900, 1), ("Backpack", 249900, 15),
    ("Water bottle", 59900, 40), ("Lamp shade", 79900, 0), ("Pencil set", 19900, 80),
    ("Desk organiser", 99900, 9),
]

if os.path.exists(DB):
    os.remove(DB)
with sqlite3.connect(DB) as conn:
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price INTEGER, stock INTEGER)")
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, items TEXT, "
                 "subtotal INTEGER, total INTEGER, coupon TEXT)")
    conn.executemany("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", PRODUCTS)
print(f"seeded {len(PRODUCTS)} products into {DB}")

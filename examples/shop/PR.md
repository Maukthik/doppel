# Show stock status on products

Adds an `in_stock` flag to every product in `GET /products` and `GET /products/{id}` so the
storefront can grey out sold-out items. Also tidies up order total and validation code.
No other behavior changes intended.

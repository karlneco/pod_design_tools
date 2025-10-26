import os
from datetime import datetime

from flask import Blueprint, request, jsonify, send_from_directory, render_template

from .. import Config
from ..extensions import store

bp = Blueprint("pages", __name__)

PRODUCTS_COLLECTION = "shopify_products"

@bp.get("/")
def index():
    return render_template("index.html")

@bp.get("/products")
def products_page():
    products = store.list(PRODUCTS_COLLECTION)

    # Sort newest first by available timestamp
    def _ts(p):
        t = p.get("created_at") or p.get("updated_at")
        if not t:
            return datetime.min
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            return datetime.min

    products = sorted(products, key=_ts, reverse=True)
    return render_template("products.html", products=products, store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"))

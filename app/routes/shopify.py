import os
from datetime import datetime

from flask import Blueprint, request, jsonify, render_template, current_app

from ..extensions import store, shopify_client as shopify

bp = Blueprint("shopify_pages", __name__)

SHOPIFY_PRODUCTS_COLLECTION = "shopify_products"


@bp.get("/")
def products_page():
    products = store.list(SHOPIFY_PRODUCTS_COLLECTION)

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


@bp.get("/shopify/products/placeholder/<product_id>")
def shopify_placeholder(product_id):
    # A super simple stub page you can replace later with a proper “import from Shopify” flow
    html = f"""
    <html><head><title>Shopify Placeholder</title></head>
    <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
      <h2>Shopify Product Placeholder</h2>
      <p>Printify Product ID: <code>{product_id}</code></p>
      <p>This page will eventually fetch or create a corresponding Shopify product and then redirect.</p>
      <p>(Wire this up later to your Shopify integration.)</p>
      <p><a href="/printify">Back to Printify list</a></p>
    </body></html>
    """
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


def _normalize(p: dict) -> dict:
    """Normalize Shopify product payload -> fields used by our templates."""
    if not p:
        return {}
    images = p.get("images") or []
    primary_image = None
    if images:
        # Shopify images typically have 'src'
        primary_image = images[0].get("src")

    # product status: 'active'|'draft'|'archived' (Storefront API/REST may vary)
    status = p.get("status") or p.get("published_scope") or "unknown"

    # product URL: if you store handle/domain in cache; otherwise build from env + handle
    handle = p.get("handle")
    store_domain = os.getenv("SHOPIFY_STORE_DOMAIN")  # e.g., 'yourshop.myshopify.com'
    public_url = f"https://{store_domain}/products/{handle}" if (store_domain and handle) else None

    body_html = p.get("body_html") or p.get("description_html") or ""

    # tags: can be comma-separated string or list depending on your fetcher
    raw_tags = p.get("tags") or []
    if isinstance(raw_tags, str):
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    else:
        tags = raw_tags

    # color swatches / options (optional)
    options = p.get("options") or []
    variants = p.get("variants") or []

    return {
        "id": str(p.get("id") or ""),
        "title": p.get("title") or "(untitled)",
        "primary_image": primary_image,
        "status": status,
        "url": public_url,
        "description": body_html,
        "tags": tags,
        "options": options,
        "variants": variants,
        "raw": p,  # keep full payload for the right-side “Raw JSON” section
    }


@bp.get("/products/<product_id>/edit")
def edit_shopify_product(product_id: str):
    """
    Base editor: load Shopify product (cache first, then API), render a read-only page for now.
    """
    # 1) Try cache
    cached = (store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id) or
              store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)))
    product = None

    # 2) If not cached, try live fetch (and cache it)
    if not cached and shopify is not None:
        try:
            product = shopify.get_product(product_id)  # implement in your client
            if product:
                store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), product)
        except Exception as e:
            current_app.logger.exception("Failed to fetch Shopify product %s", product_id)
            # Render a simple error page rather than JSON
            return render_template(
                "shopify_edit.html",
                p={"id": product_id, "title": "(load failed)", "raw": {"error": str(e)}}
            ), 502

    product = product or cached
    if not product:
        # Nothing found; render a minimal placeholder
        return render_template(
            "shopify_edit.html",
            p={"id": product_id, "title": "(not found)", "raw": {"error": "Not in cache and live fetch disabled/unavailable."}}
        ), 404

    return render_template("shopify_edit.html", p=_normalize(product))

import os
from datetime import datetime

from flask import Blueprint, render_template, current_app, send_from_directory

from ..extensions import store, shopify_client as shopify
from .. import Config
from pathlib import Path
from flask import render_template

bp = Blueprint("shopify_pages", __name__)

SHOPIFY_PRODUCTS_COLLECTION = "shopify_products"


def _normalize_product_tags(product: dict) -> dict:
    """Normalize tags from comma-separated string to array format for database storage."""
    if product and "tags" in product:
        raw_tags = product["tags"]
        if isinstance(raw_tags, str):
            product["tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif not isinstance(raw_tags, list):
            product["tags"] = []
    return product


@bp.get("/")
def products_page():
    products = store.list(SHOPIFY_PRODUCTS_COLLECTION)
    # Ensure tags are arrays so the template doesn't iterate characters.
    products = [_normalize_product_tags(p) for p in products]

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
    images = p.get("primary_image") or []
    primary_image = images

    # product status: 'active'|'draft'|'archived' (Storefront API/REST may vary)
    status = p.get("status") or p.get("published_scope") or "unknown"

    # product URL: if you store handle/domain in cache; otherwise build from env + handle
    handle = p.get("handle")
    store_domain = os.getenv("SHOPIFY_STORE_DOMAIN")  # e.g., 'yourshop.myshopify.com'
    public_url = f"https://{store_domain}/products/{handle}" if (store_domain and handle) else None

    body_html = p.get("body_html") or p.get("description") or ""

    # tags: can be comma-separated string or list depending on your fetcher
    raw_tags = p.get("tags") or []
    if isinstance(raw_tags, str):
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    else:
        tags = raw_tags

    # color swatches / options (optional)
    options = p.get("options") or []
    variants = p.get("variants") or []
    swatch_mapping = p.get("swatch_mapping") or {}

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
        "swatch_mapping": swatch_mapping,
        "raw": p,  # keep full payload for the right-side “Raw JSON” section
    }

def _norm_text(val: str) -> str:
    return "".join(ch.lower() for ch in str(val or "").strip() if ch.isalnum() or ch.isspace()).strip()


def _resolve_design_slug_for_product(product_id: str) -> str | None:
    # 1) Direct mapping from design integrations if present
    try:
        for design in store.list("designs"):
            integ = design.get("integrations", {}).get("printify_product", {}) or {}
            if str(integ.get("shopify_product_id") or "") == str(product_id):
                return design.get("slug")
    except Exception:
        pass

    # 2) Use Printify cache to map Shopify product -> Printify product id, then match design integration
    printify_id = None
    try:
        for item in store.list("printify_products"):
            if str(item.get("shopify_product_id") or "") == str(product_id):
                printify_id = str(item.get("id") or item.get("_id") or "")
                break
    except Exception:
        printify_id = None

    if printify_id:
        try:
            for design in store.list("designs"):
                integ = design.get("integrations", {}).get("printify_product", {}) or {}
                if str(integ.get("id") or integ.get("_id") or "") == printify_id:
                    return design.get("slug")
        except Exception:
            pass

    # 3) Fallback: match by title (Shopify or Printify)
    target_title = None
    try:
        cached = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id))
        if cached:
            target_title = cached.get("title")
    except Exception:
        target_title = None

    if not target_title:
        try:
            for item in store.list("printify_products"):
                if str(item.get("shopify_product_id") or "") == str(product_id):
                    target_title = item.get("title")
                    break
        except Exception:
            target_title = None

    if target_title:
        target_norm = _norm_text(target_title)
        matches = []
        try:
            for design in store.list("designs"):
                if _norm_text(design.get("title")) == target_norm:
                    matches.append(design.get("slug"))
        except Exception:
            matches = []
        if len(matches) == 1:
            return matches[0]

    return None


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
                # Normalize tags from comma-separated string to array for database storage
                product = _normalize_product_tags(product)
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

    # Determine if generated mockups already exist for this product
    folder = Config.ASSETS_DIR / 'product_mockups' / str(product_id)
    has_mockups = False
    mockups_count = 0
    try:
        if folder.exists():
            files = [p for p in folder.iterdir() if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')]
            if files:
                has_mockups = True
                mockups_count = len(files)
    except Exception:
        # non-fatal; leave flags as defaults
        pass

    normalize = _normalize(product)
    return render_template("shopify_edit.html", p=normalize, has_mockups=has_mockups, mockups_count=mockups_count)


@bp.get('/products/<product_id>/mockups')
def shopify_product_mockups(product_id: str):
    # Look for generated mockups under assets/product_mockups/<product_id>
    folder = Config.ASSETS_DIR / 'product_mockups' / str(product_id)
    mockups = []
    if folder.exists():
        for p in sorted(folder.iterdir()):
            if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                # Provide path relative to the ASSETS folder so template can reference via /assets/<path>
                try:
                    mockups.append(str(p.relative_to(Config.ASSETS_DIR)))
                except Exception:
                    mockups.append(str(p.name))
    design_slug = _resolve_design_slug_for_product(product_id)
    return render_template('shopify_mockups.html', id=product_id, mockups=mockups, design_slug=design_slug)

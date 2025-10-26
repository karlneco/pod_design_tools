import json
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from .. import config, Config
from ..extensions import store, printify_client as printify

bp = Blueprint("printify_api", __name__)
PRINTIFY_PRODUCTS_COLLECTION = "printify_products"


@bp.get("/printify/colors/<product_id>")
def api_printify_colors(product_id):
    """Return the distinct color names (and codes if present) available for this product’s blueprint/provider."""
    prod = printify.get_product(product_id)
    bp = prod.get("blueprint_id")
    pp = prod.get("print_provider_id")
    if bp is None or pp is None:
        return jsonify({"error": "blueprint_id or print_provider_id missing on product"}), 400

    try:
        cat = printify.get_blueprint_provider_variants(bp, pp)
    except Exception as e:
        # If this provider doesn't serve this blueprint, show what's available
        try:
            provs = printify.list_blueprint_providers(bp)
        except Exception:
            provs = {}
        return jsonify({
            "error": f"Provider {pp} not found for blueprint {bp}.",
            "hint": "Use one of the providers listed for this blueprint.",
            "available_providers": provs
        }), 404

    # v1 endpoint returns a list or an object with 'variants'
    variants = cat if isinstance(cat, list) else (cat.get("variants") or [])
    colors = set()
    color_map = {}  # color -> list of variant_ids
    for v in variants:
        opts = v.get("options") or {}
        color = opts.get("color") or opts.get("Color") or opts.get("colour") or opts.get("Colour")
        if color:
            colors.add(color)
            vid = v.get("id")
            if vid is not None:
                color_map.setdefault(color, []).append(vid)

    return jsonify({
        "blueprint_id": int(bp),
        "print_provider_id": int(pp),
        "colors": sorted(colors),
        "color_variants": color_map
    })


@bp.get("/printify/products")
def api_list_printify_products():
    return jsonify(store.list(PRINTIFY_PRODUCTS_COLLECTION))


def _to_bool(param):
    pass


@bp.post("/printify/products/cache/update")
def update_printify_products_cache():
    """Download Printify products, normalize, and store cache."""
    shop_id = (request.json or {}).get("shop_id") or os.getenv("PRINTIFY_SHOP_ID")
    if not shop_id:
        return jsonify({"error": "Missing shop_id (provide in body or set PRINTIFY_SHOP_ID)"}), 400

    normalized = {}
    page = 1
    total = 0

    while True:
        page_data = printify.list_products(page=page, limit=100)
        # API may return either {"data":[...], "last_page":N, ...} or {"products":[...]}
        data_list = page_data.get("data") or page_data.get("products") or []
        if not data_list:
            break

        for p in data_list:
            pid = str(p.get("id") or p.get("_id") or "")
            if not pid:
                continue

            title = p.get("title") or p.get("name") or ""
            # main image: tolerate dicts or plain strings; fallback to preview
            primary_image = None
            imgs = p.get("images") or []
            if imgs:
                first = imgs[0]
                if isinstance(first, dict):
                    primary_image = first.get("src") or first.get("url")
                elif isinstance(first, str):
                    primary_image = first
            if not primary_image:
                prv = p.get("preview")
                if isinstance(prv, dict):
                    primary_image = prv.get("src") or prv.get("url")
                elif isinstance(prv, str):
                    primary_image = prv

            # Try to link to Shopify product URL if Printify gives external handle/id
            shopify_url = None
            ext = p.get("external") or {}
            # tolerate dict or string shapes
            if isinstance(ext, dict):
                ext_handle = ext.get("handle") or ext.get("shopify_handle") or ext.get("product_handle")
            elif isinstance(ext, str):
                # in rare cases 'external' may just be the handle
                ext_handle = ext.strip()
            else:
                ext_handle = None

            if ext_handle:
                shopify_url = f"https://{os.getenv('SHOPIFY_STORE_DOMAIN')}/products/{ext_handle}"

            # Publication status: simply whether we have a Shopify link
            published = bool(shopify_url)

            # Channel-specific shapes: list, dict, or strings
            if published is None:
                published = False
                sc_props = p.get("sales_channel_properties") or p.get("sales_channels") or []
                # If it's a dict, check values; if list, iterate items; if string, look for 'published'
                if isinstance(sc_props, dict):
                    for v in sc_props.values():
                        if (isinstance(v, dict) and _to_bool(v.get("published"))) or \
                                (isinstance(v, str) and "published" in v.lower()):
                            published = True
                            break
                elif isinstance(sc_props, list):
                    for sc in sc_props:
                        if isinstance(sc, dict):
                            if _to_bool(sc.get("published")):
                                published = True
                                break
                        elif isinstance(sc, str):
                            if "published" in sc.lower():
                                published = True
                                break
                elif isinstance(sc_props, str):
                    published = "published" in sc_props.lower()

            # Try to link to Shopify product URL if Printify gives external handle/id
            shopify_url = None
            ext = p.get("external") or {}
            ext_handle = ext.get("handle")
            if ext_handle:
                shopify_url = f"https://{os.getenv('SHOPIFY_STORE_DOMAIN')}/products/{ext_handle}"
            else:
                # sometimes external id exists; if you store a mapping later, plug it here
                pass

            normalized[pid] = {
                "id": pid,
                "title": title,
                "primary_image": primary_image,
                "published": bool(published),
                "shopify_url": shopify_url,
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
            }
            total += 1

        # pagination end?
        last_page = page_data.get("last_page")
        current_page = page_data.get("current_page") or page
        if last_page and current_page < last_page:
            page += 1
            continue
        # Fallback: if no last_page info, stop after first page unless data == limit
        if not last_page and len(data_list) >= 100:
            page += 1
            continue
        break

    store.replace_collection(PRINTIFY_PRODUCTS_COLLECTION, normalized)
    return jsonify({"count": len(normalized)})


@bp.post("/printify/products/duplicate")
def api_printify_duplicate():
    data = request.get_json(force=True) or {}
    product_id = data.get("product_id") or data.get("template_id")
    new_title = data.get("title") or "New Product"
    new_description = data.get("description") or ""

    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    # 1) Fetch the template product
    template = printify.get_product(product_id=product_id)

    # 2) Create a new product using the “lean” payload (no SKUs, slim variants & print_areas)
    created = printify.duplicate_from_template(
        template=template,
        title=new_title,
        description=new_description,
        tags=template.get("tags", [])
    )

    return jsonify({"ok": True, "created": created}), 201


@bp.post("/api/printify/templates/<product_id>/extract_colors")
def api_printify_extract_colors(product_id: str):
    """
    Load a template product from Printify and persist its color set (with hex)
    to assets/mockups/{blueprint_id}_{print_provider_id}/colors.json
    """
    try:
        tpl = printify.get_product(product_id)
    except Exception as e:
        return jsonify({"error": f"Failed to load product {product_id}", "detail": str(e)}), 400

    bp = tpl.get("blueprint_id")
    pp = tpl.get("print_provider_id")
    options = tpl.get("options") or []

    color_option = None
    for opt in options:
        # Printify uses type: "color" and often name: "Colors"
        if (opt.get("type") == "color") or (opt.get("name", "").lower() == "colors"):
            color_option = opt
            break

    if not color_option:
        return jsonify({"error": "No color option found on template product."}), 404

    values = color_option.get("values") or []

    # Normalize to a compact list of {id, title, hex}
    colors_out = []
    for v in values:
        hexes = v.get("colors") or []
        # Some entries may provide multiple hexes (very rare) – keep the first for UI,
        # but store all in 'hexes' as well.
        primary_hex = (hexes[0] if hexes else None)
        colors_out.append({
            "id": v.get("id"),
            "title": v.get("title"),
            "hex": primary_hex,
            "hexes": hexes
        })

    # Compose write path
    folder_name = f"{bp}_{pp}"
    folder = Config.MOCKUPS_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / "colors.json"

    payload = {
        "blueprint_id": bp,
        "print_provider_id": pp,
        "option_name": color_option.get("name") or "Colors",
        "values": colors_out,
        "generated_from_product_id": tpl.get("id"),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    # Save
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return jsonify({
        "ok": True,
        "message": "Colors extracted.",
        "path": str(out_path.relative_to(Config.BASE_DIR)),
        "count": len(colors_out)
    })

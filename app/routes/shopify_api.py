from flask import Blueprint, request, jsonify, current_app

from ..extensions import store, shopify_client as shopify

bp = Blueprint("shopify_api", __name__)

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


# -----------------------------
# Shopify: upload images + cache product list
# -----------------------------
@bp.post("/shopify/products/<product_id>/images")
def shopify_upload_images(product_id):
    payload = request.json or {}
    image_paths = payload.get("image_paths", [])
    uploaded = shopify.upload_product_images(product_id, image_paths)
    return jsonify({"uploaded": uploaded})


@bp.post("/shopify/products/<product_id>/save")
def api_shopify_save(product_id):
    """
    Update a Shopify product (title, description, tags, status).
    Always returns JSON.
    """
    try:
        body = request.get_json(force=True)
    except Exception as e:
        return jsonify({"error": f"Bad JSON in request: {e}"}), 400

    title = (body.get("title") or "").strip()
    desc = (body.get("description") or "").strip()
    tags = body.get("tags") or []
    status = body.get("status") or "active"

    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    payload = {}
    if title:
        payload["title"] = title
    payload["body_html"] = desc
    payload["tags"] = ", ".join(tags)
    if status in ("active", "draft"):
        payload["status"] = status

    try:
        # Get existing product from cache to preserve fields we're not updating
        existing = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or {}
        
        # Update product on Shopify
        updated = shopify.update_product(product_id, payload)
        
        # Normalize tags from comma-separated string to array for database storage
        updated = _normalize_product_tags(updated)
        
        # Merge only the fields we updated into the existing cached product
        # This preserves images, variants, options, and other complex structures
        merged = existing.copy()
        if updated:
            # Update only the fields we explicitly changed
            if "title" in updated:
                merged["title"] = updated["title"]
            if "body_html" in updated:
                merged["body_html"] = updated["body_html"]
            if "tags" in updated:
                merged["tags"] = updated["tags"]
            if "status" in updated:
                merged["status"] = updated["status"]
            # Update timestamp if provided
            if "updated_at" in updated:
                merged["updated_at"] = updated["updated_at"]
            # Update simple fields if missing, but don't overwrite existing ones
            # (to avoid losing data if Shopify response is incomplete)
            for key in ["handle", "vendor", "product_type"]:
                if key in updated:
                    # Only update if we don't have it, or if it's a simple string update
                    if key not in merged or not merged.get(key):
                        merged[key] = updated[key]
            # created_at should never change, only set if missing
            if "created_at" in updated and "created_at" not in merged:
                merged["created_at"] = updated["created_at"]
            
            # Explicitly preserve complex nested structures from cache
            # These should NOT be overwritten by the update response
            # as they may be incomplete or missing in the update response
            preserve_fields = ["images", "image", "variants", "options", "primary_image"]
            for field in preserve_fields:
                if field in existing:
                    merged[field] = existing[field]
        
        store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), merged)
        return jsonify({"ok": True, "updated": merged})
    except Exception as e:
        current_app.logger.exception("Shopify update failed")
        # Return a real JSON error so frontend `.json()` won't choke
        return jsonify({"error": str(e)}), 500


@bp.post("/shopify/products/<product_id>/refresh")
def api_shopify_refresh(product_id):
    """Fetch latest data from Shopify and refresh cache"""
    try:
        product = shopify.get_product(product_id)
        if product:
            # Normalize tags from comma-separated string to array for database storage
            product = _normalize_product_tags(product)
            store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), product)
            return jsonify({"ok": True, "product": product})
        return jsonify({"error": "Product not found"}), 404
    except Exception as e:
        current_app.logger.exception("Shopify refresh failed")
        return jsonify({"error": str(e)}), 400

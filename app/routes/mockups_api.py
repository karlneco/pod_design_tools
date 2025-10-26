
from flask import Blueprint, request, jsonify

from ..extensions import store, shopify_client

bp = Blueprint("mockups_api", __name__)


# -----------------------------
# Shopify: upload images + cache product list
# -----------------------------
@bp.post("/shopify/products/<product_id>/images")
def shopify_upload_images(product_id):
    payload = request.json or {}
    image_paths = payload.get("image_paths", [])
    uploaded = shopify_client.upload_product_images(product_id, image_paths)
    return jsonify({"uploaded": uploaded})

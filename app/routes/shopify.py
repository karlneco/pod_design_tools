from flask import Blueprint, request, jsonify

from ..extensions import store, shopify_client

bp = Blueprint("shopify_pages", __name__)

PRODUCTS_COLLECTION = "shopify_products"

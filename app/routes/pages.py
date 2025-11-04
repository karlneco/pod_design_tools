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


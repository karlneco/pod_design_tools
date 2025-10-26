# app/extensions.py
from flask_cors import CORS
from pathlib import Path
import os
from dotenv import load_dotenv

from .storage.json_store import JsonStore
from .services.printify_client import PrintifyClient
from .services.shopify_client import ShopifyClient

# Load env just once here
load_dotenv()

# CORS is a real Flask extension (keeps init_app)
cors = CORS()

# Paths
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
MOCKUPS_DIR = ASSETS_DIR / "mockups"

# JsonStore — no wrapper needed
store = JsonStore(DATA_DIR)

# Printify client reads env internally in your implementation
printify_client = PrintifyClient()

# Shopify client needs explicit env values
shopify_client = ShopifyClient(
    store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"),
    admin_token=os.getenv("SHOPIFY_ADMIN_TOKEN"),
    api_version=os.getenv("SHOPIFY_API_VERSION", "2024-10"),
)

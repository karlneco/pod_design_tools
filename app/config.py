import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = BASE_DIR / "data"
    ASSETS_DIR = BASE_DIR / "assets"
    MOCKUPS_DIR = ASSETS_DIR / "mockups"
    GENERATED_MOCKUPS_DIR = BASE_DIR / "generated_mockups"
    SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN")
    SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
    SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-10")
    PRINTIFY_API_TOKEN = os.getenv("PRINTIFY_API_TOKEN")
    PRINTIFY_SHOP_ID = os.getenv("PRINTIFY_SHOP_ID")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

    DEFAULT_FRONT_IMAGE_ID = "68faffc792143382282f3002"

class DevConfig(Config):
    DEBUG = True


class ProdConfig(Config):
    DEBUG = False

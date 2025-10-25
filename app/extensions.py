from flask_cors import CORS
from .storage.json_store import JsonStore
from .services.printify_client import PrintifyClient
from .services.shopify_client import ShopifyClient


class _CORS:
    def init_app(self, app): CORS(app)


cors = _CORS()


class _Store:
    obj: JsonStore | None = None

    def init_app(self, app, data_dir):
        self.obj = JsonStore(data_dir)


store = _Store()


class _Printify:
    obj: PrintifyClient | None = None

    def init_app(self, app): self.obj = PrintifyClient()


printify_client = _Printify()


class _Shopify:
    obj: ShopifyClient | None = None

    def init_app(self, app):
        self.obj = ShopifyClient(
            store_domain=app.config["SHOPIFY_STORE_DOMAIN"],
            admin_token=app.config["SHOPIFY_ADMIN_TOKEN"],
            api_version=app.config["SHOPIFY_API_VERSION"],
        )


shopify_client = _Shopify()

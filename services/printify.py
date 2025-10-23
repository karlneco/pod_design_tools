import os
import httpx

PRINTIFY_API_BASE = "https://api.printify.com/v1"

class PrintifyClient:
    def __init__(self, api_token: str | None, shop_id: str | None):
        from dotenv import load_dotenv
        import os

        load_dotenv()  # ensure .env is loaded
        self.api_token = api_token or os.getenv("PRINTIFY_API_TOKEN")
        self.shop_id = shop_id or os.getenv("PRINTIFY_SHOP_ID")
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    def create_product(self, shop_id: str, product_spec: dict):
        """Create a product draft in Printify.
        product_spec should include blueprint_id, print_provider_id, variants, print_areas, title, description, tags
        """
        url = f"{PRINTIFY_API_BASE}/shops/{shop_id}/products.json"
        with httpx.Client(timeout=60) as client:
            r = client.post(url, headers=self.headers, json=product_spec)
            r.raise_for_status()
            return r.json()

    def publish_to_shopify(self, shop_id: str, product_id: str, publish_details: dict | None = None):
        """Publish a Printify product to connected Shopify store.
        publish_details may include: {"title": True, "description": True, "images": True, "variants": True}
        """
        url = f"{PRINTIFY_API_BASE}/shops/{shop_id}/products/{product_id}/publish.json"
        with httpx.Client(timeout=60) as client:
            r = client.post(url, headers=self.headers,
                            json=publish_details or {"title": True, "description": True, "images": True,
                                                     "variants": True})
            r.raise_for_status()
            return r.json()

    def list_products(self, shop_id: str, page: int = 1, limit: int = 50) -> dict:
        """List products in a Printify shop (paginated).
        Returns JSON with keys like: data (list), current_page, last_page, etc. (shape may vary by API version).
        """
        params = {"page": page, "limit": min(limit, 50)}
        url = f"{PRINTIFY_API_BASE}/shops/{shop_id}/products.json"
        with httpx.Client(timeout=60) as client:
            r = client.get(url, headers=self.headers, params=params)
            r.raise_for_status()
            return r.json()

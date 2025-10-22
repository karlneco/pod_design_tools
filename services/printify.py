import os
import httpx

PRINTIFY_API_BASE = "https://api.printify.com/v1"

class PrintifyClient:
    def __init__(self, api_token: str | None):
        self.api_token = api_token
        self.headers = {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}

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
            r = client.post(url, headers=self.headers, json=publish_details or {"title": True, "description": True, "images": True, "variants": True})
            r.raise_for_status()
            return r.json()

import base64
from pathlib import Path
import httpx
from urllib.parse import urlencode

class ShopifyClient:
    def __init__(self, store_domain: str, admin_token: str, api_version: str = "2024-10"):
        self.domain = store_domain
        self.token = admin_token
        self.api_version = api_version
        self.base = f"https://{self.domain}/admin/api/{self.api_version}"
        self.headers = {
            "X-Shopify-Access-Token": self.token,
            "Content-Type": "application/json",
        }

    def product_url(self, handle: str | None) -> str | None:
        if not handle:
            return None
        return f"https://{self.domain.replace('.myshopify.com','')}.myshopify.com/products/{handle}"

    def upload_product_images(self, product_id: str, image_paths: list[str]):
        uploaded = []
        with httpx.Client(timeout=60) as client:
            for p in image_paths:
                b64 = base64.b64encode(Path(p).read_bytes()).decode("utf-8")
                payload = {"image": {"attachment": b64}}
                url = f"{self.base}/products/{product_id}/images.json"
                r = client.post(url, headers=self.headers, json=payload)
                r.raise_for_status()
                uploaded.append(r.json())
        return uploaded

    def list_all_products(self, limit: int = 250) -> list[dict]:
        """Fetch all products via REST pagination using page_info.
        """
        products = []
        params = {"limit": min(limit, 250), "status": "active"}
        next_page_info = None
        with httpx.Client(timeout=60) as client:
            while True:
                query = params.copy()
                if next_page_info:
                    query = {"limit": params["limit"], "page_info": next_page_info}
                url = f"{self.base}/products.json?{urlencode(query)}"
                r = client.get(url, headers=self.headers)
                r.raise_for_status()
                data = r.json()
                items = data.get("products", [])
                products.extend(items)

                # Parse Link header for rel="next"
                link = r.headers.get("Link")
                if link and "rel=\"next\"" in link:
                    # link format: <...page_info=XYZ>; rel="next"
                    try:
                        start = link.find("page_info=") + len("page_info=")
                        end = link.find(">", start)
                        next_page_info = link[start:end]
                    except Exception:
                        next_page_info = None
                else:
                    break
        return products

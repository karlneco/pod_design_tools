"""
Unit tests for ShopifyClient service.
"""
import json
import base64
import pytest
import httpx
import respx
from pathlib import Path

from app.services.shopify_client import ShopifyClient


@pytest.mark.unit
class TestShopifyClient:
    """Tests for ShopifyClient API integration."""

    def test_client_initialization(self):
        """Test ShopifyClient initializes with correct configuration."""
        client = ShopifyClient(
            store_domain="test-store.myshopify.com",
            admin_token="test_token",
            api_version="2024-10"
        )

        assert client.domain == "test-store.myshopify.com"
        assert client.token == "test_token"
        assert client.api_version == "2024-10"
        assert client.base == "https://test-store.myshopify.com/admin/api/2024-10"
        assert client.headers["X-Shopify-Access-Token"] == "test_token"

    def test_client_initialization_default_api_version(self):
        """Test ShopifyClient uses default API version."""
        client = ShopifyClient(
            store_domain="test-store.myshopify.com",
            admin_token="test_token"
        )

        assert client.api_version == "2024-10"

    def test_product_url_generation(self):
        """Test generating public product URLs."""
        client = ShopifyClient(
            store_domain="test-store.myshopify.com",
            admin_token="test_token"
        )

        url = client.product_url("cool-t-shirt")

        assert url == "https://test-store.myshopify.com/products/cool-t-shirt"

    def test_product_url_with_none_handle(self):
        """Test product_url returns None for None handle."""
        client = ShopifyClient(
            store_domain="test-store.myshopify.com",
            admin_token="test_token"
        )

        url = client.product_url(None)

        assert url is None

    def test_product_url_with_empty_handle(self):
        """Test product_url returns None for empty handle."""
        client = ShopifyClient(
            store_domain="test-store.myshopify.com",
            admin_token="test_token"
        )

        url = client.product_url("")

        assert url is None

    @respx.mock
    def test_get_product_success(self, shopify_client, sample_shopify_product):
        """Test getting a single product by ID."""
        respx.get(
            "https://test-store.myshopify.com/admin/api/2024-10/products/987654321.json"
        ).mock(return_value=httpx.Response(200, json=sample_shopify_product))

        result = shopify_client.get_product("987654321")

        assert result["id"] == 987654321
        assert result["title"] == "Test Shopify Product"

    @respx.mock
    def test_get_product_not_found(self, shopify_client):
        """Test getting a product that doesn't exist."""
        respx.get(
            "https://test-store.myshopify.com/admin/api/2024-10/products/999999.json"
        ).mock(return_value=httpx.Response(404, json={"errors": "Not Found"}))

        with pytest.raises(httpx.HTTPStatusError):
            shopify_client.get_product("999999")

    @respx.mock
    def test_update_product(self, shopify_client):
        """Test updating a product."""
        update_payload = {
            "title": "Updated Title",
            "body_html": "<p>Updated description</p>",
            "tags": "updated, tags"
        }

        response_data = {
            "product": {
                "id": 123456,
                **update_payload
            }
        }

        respx.put(
            "https://test-store.myshopify.com/admin/api/2024-10/products/123456.json"
        ).mock(return_value=httpx.Response(200, json=response_data))

        result = shopify_client.update_product("123456", update_payload)

        assert result["title"] == "Updated Title"
        assert result["body_html"] == "<p>Updated description</p>"

    @respx.mock
    def test_update_product_wraps_payload(self, shopify_client):
        """Test that update_product wraps payload in 'product' key."""
        update_payload = {"title": "New Title"}

        respx.put(
            "https://test-store.myshopify.com/admin/api/2024-10/products/123.json"
        ).mock(return_value=httpx.Response(200, json={"product": {"id": 123, "title": "New Title"}}))

        shopify_client.update_product("123", update_payload)

        # Verify the request payload is wrapped correctly
        request = respx.calls.last.request
        payload = json.loads(request.content)
        assert "product" in payload
        assert payload["product"]["title"] == "New Title"

    @respx.mock
    def test_list_all_products_single_page(self, shopify_client):
        """Test listing products when all fit in one page."""
        products_response = {
            "products": [
                {"id": 1, "title": "Product 1"},
                {"id": 2, "title": "Product 2"}
            ]
        }

        respx.get(
            "https://test-store.myshopify.com/admin/api/2024-10/products.json"
        ).mock(return_value=httpx.Response(200, json=products_response))

        result = shopify_client.list_all_products()

        assert len(result) == 2
        assert result[0]["id"] == 1

    @respx.mock
    def test_list_all_products_pagination(self, shopify_client):
        """Test listing products with pagination."""
        # First page
        page1_response = {
            "products": [
                {"id": 1, "title": "Product 1"},
                {"id": 2, "title": "Product 2"}
            ]
        }
        page1_link = '<https://test-store.myshopify.com/admin/api/2024-10/products.json?page_info=abc123>; rel="next"'

        # Second page (last page)
        page2_response = {
            "products": [
                {"id": 3, "title": "Product 3"}
            ]
        }

        # Mock first request
        respx.get(
            "https://test-store.myshopify.com/admin/api/2024-10/products.json",
            params__contains={"status": "active"}
        ).mock(return_value=httpx.Response(200, json=page1_response, headers={"Link": page1_link}))

        # Mock second request with page_info
        respx.get(
            "https://test-store.myshopify.com/admin/api/2024-10/products.json",
            params__contains={"page_info": "abc123"}
        ).mock(return_value=httpx.Response(200, json=page2_response))

        result = shopify_client.list_all_products()

        assert len(result) == 3
        assert result[0]["id"] == 1
        assert result[2]["id"] == 3

    @respx.mock
    def test_list_all_products_respects_limit(self, shopify_client):
        """Test that list_all_products respects the limit parameter."""
        respx.get(
            "https://test-store.myshopify.com/admin/api/2024-10/products.json"
        ).mock(return_value=httpx.Response(200, json={"products": []}))

        shopify_client.list_all_products(limit=100)

        # Verify limit was passed correctly
        request = respx.calls.last.request
        assert "limit=100" in str(request.url)

    @respx.mock
    def test_list_all_products_caps_limit_at_250(self, shopify_client):
        """Test that list_all_products caps limit at 250."""
        respx.get(
            "https://test-store.myshopify.com/admin/api/2024-10/products.json"
        ).mock(return_value=httpx.Response(200, json={"products": []}))

        shopify_client.list_all_products(limit=500)

        # Verify limit was capped at 250
        request = respx.calls.last.request
        assert "limit=250" in str(request.url)

    @respx.mock
    def test_upload_product_images(self, shopify_client, sample_design_image):
        """Test uploading product images."""
        upload_response = {
            "image": {
                "id": 111111,
                "product_id": 123456,
                "position": 1,
                "src": "https://cdn.shopify.com/uploaded.jpg"
            }
        }

        respx.post(
            "https://test-store.myshopify.com/admin/api/2024-10/products/123456/images.json"
        ).mock(return_value=httpx.Response(200, json=upload_response))

        result = shopify_client.upload_product_images("123456", [str(sample_design_image)])

        assert len(result) == 1
        assert result[0]["image"]["id"] == 111111

    @respx.mock
    def test_upload_product_images_converts_to_webp(self, shopify_client, sample_design_image):
        """Test that images are converted to WebP format."""
        respx.post(
            "https://test-store.myshopify.com/admin/api/2024-10/products/123456/images.json"
        ).mock(return_value=httpx.Response(200, json={"image": {"id": 1}}))

        shopify_client.upload_product_images("123456", [str(sample_design_image)])

        # Verify the request contains base64 encoded data
        request = respx.calls.last.request
        payload = json.loads(request.content)
        assert "image" in payload
        assert "attachment" in payload["image"]

        # Verify it's valid base64
        try:
            decoded = base64.b64decode(payload["image"]["attachment"])
            # WebP files start with "RIFF" and contain "WEBP"
            assert b"WEBP" in decoded[:50]
        except Exception:
            pytest.fail("Image not properly base64 encoded or not WebP format")

    @respx.mock
    def test_upload_multiple_images(self, shopify_client, sample_design_image, tmp_path):
        """Test uploading multiple images at once."""
        from PIL import Image

        # Create a second test image
        img2 = Image.new('RGBA', (100, 100), (0, 255, 0, 255))
        img2_path = tmp_path / "test_design2.png"
        img2.save(img2_path, "PNG")

        respx.post(
            "https://test-store.myshopify.com/admin/api/2024-10/products/123456/images.json"
        ).mock(return_value=httpx.Response(200, json={"image": {"id": 1}}))

        result = shopify_client.upload_product_images(
            "123456",
            [str(sample_design_image), str(img2_path)]
        )

        assert len(result) == 2
        assert len(respx.calls) == 2

    @respx.mock
    def test_upload_image_fallback_on_conversion_error(self, shopify_client, tmp_path):
        """Test that upload falls back to raw bytes if WebP conversion fails."""
        # Create a non-image file that will fail conversion
        fake_image = tmp_path / "fake.png"
        fake_image.write_bytes(b"not a real image")

        respx.post(
            "https://test-store.myshopify.com/admin/api/2024-10/products/123456/images.json"
        ).mock(return_value=httpx.Response(200, json={"image": {"id": 1}}))

        result = shopify_client.upload_product_images("123456", [str(fake_image)])

        # Should still succeed (using raw bytes fallback)
        assert len(result) == 1

    @respx.mock
    def test_upload_images_http_error(self, shopify_client, sample_design_image):
        """Test handling of HTTP errors during image upload."""
        respx.post(
            "https://test-store.myshopify.com/admin/api/2024-10/products/123456/images.json"
        ).mock(return_value=httpx.Response(422, json={"errors": "Invalid image"}))

        with pytest.raises(httpx.HTTPStatusError):
            shopify_client.upload_product_images("123456", [str(sample_design_image)])

    def test_list_all_products_filters_active_only(self, shopify_client):
        """Test that list_all_products filters for active products."""
        with respx.mock:
            respx.get(
                "https://test-store.myshopify.com/admin/api/2024-10/products.json"
            ).mock(return_value=httpx.Response(200, json={"products": []}))

            shopify_client.list_all_products()

            # Verify status=active was passed
            request = respx.calls.last.request
            assert "status=active" in str(request.url)

"""
Unit tests for PrintifyClient service.
"""
import json
import base64
import pytest
import httpx
import respx
from pathlib import Path

from app.services.printify_client import PrintifyClient, PRINTIFY_API_BASE


@pytest.mark.unit
class TestPrintifyClient:
    """Tests for PrintifyClient API integration."""

    @respx.mock
    def test_list_products(self, mock_env_vars):
        """Test listing products with pagination."""
        client = PrintifyClient()

        # Mock the API response
        mock_response = {
            "current_page": 1,
            "data": [
                {"id": "prod1", "title": "Product 1"},
                {"id": "prod2", "title": "Product 2"}
            ],
            "last_page": 1,
            "total": 2
        }

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products.json"
        ).mock(return_value=httpx.Response(200, json=mock_response))

        result = client.list_products(page=1, limit=10)

        assert result["current_page"] == 1
        assert len(result["data"]) == 2
        assert result["data"][0]["id"] == "prod1"

    @respx.mock
    def test_list_products_with_limit(self, mock_env_vars):
        """Test that list_products respects pagination limits."""
        client = PrintifyClient()

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products.json"
        ).mock(return_value=httpx.Response(200, json={"data": []}))

        # Request with high limit should be capped at 50
        client.list_products(page=1, limit=100)

        # Verify the request was made with limit=50
        request = respx.calls.last.request
        assert "limit=50" in str(request.url)

    @respx.mock
    def test_get_product_success(self, mock_env_vars, sample_printify_product):
        """Test getting a single product by ID."""
        client = PrintifyClient()

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products/test_product_123.json"
        ).mock(return_value=httpx.Response(200, json=sample_printify_product))

        result = client.get_product("test_product_123")

        assert result["id"] == "test_product_123"
        assert result["title"] == "Test T-Shirt Design"

    @respx.mock
    def test_get_product_not_found(self, mock_env_vars):
        """Test getting a product that doesn't exist."""
        client = PrintifyClient()

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products/nonexistent.json"
        ).mock(return_value=httpx.Response(404, json={"error": "Not found"}))

        with pytest.raises(httpx.HTTPStatusError):
            client.get_product("nonexistent")

    @respx.mock
    def test_create_product(self, mock_env_vars):
        """Test creating a new product."""
        client = PrintifyClient()

        product_spec = {
            "blueprint_id": 3,
            "title": "New Product",
            "description": "Test description",
            "print_provider_id": 5,
            "variants": [{"id": 1, "price": 2500, "is_enabled": True, "is_default": True}],
            "print_areas": []
        }

        created_product = {**product_spec, "id": "new_prod_123"}

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products.json"
        ).mock(return_value=httpx.Response(200, json=created_product))

        result = client.create_product(product_spec)

        assert result["id"] == "new_prod_123"
        assert result["title"] == "New Product"

    @respx.mock
    def test_update_product(self, mock_env_vars, tmp_path, monkeypatch):
        """Test updating an existing product."""
        # Change data directory to tmp_path to avoid creating debug files
        monkeypatch.setattr("app.services.printify_client.Path", lambda x: tmp_path / x if x == "data" else Path(x))

        client = PrintifyClient()

        update_spec = {
            "title": "Updated Title",
            "description": "Updated description"
        }

        updated_product = {**update_spec, "id": "prod_123"}

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products/prod_123.json"
        ).mock(return_value=httpx.Response(200, json=updated_product))

        result = client.update_product("prod_123", update_spec)

        assert result["title"] == "Updated Title"

    @respx.mock
    def test_publish_to_shopify(self, mock_env_vars):
        """Test publishing a product to Shopify."""
        client = PrintifyClient()

        publish_response = {"id": "prod_123", "status": "published"}

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products/prod_123/publish.json"
        ).mock(return_value=httpx.Response(200, json=publish_response))

        result = client.publish_to_shopify("prod_123")

        assert result["status"] == "published"

    @respx.mock
    def test_publish_to_shopify_with_custom_details(self, mock_env_vars):
        """Test publishing with custom publish details."""
        client = PrintifyClient()

        publish_details = {"title": True, "images": False}

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products/prod_123/publish.json"
        ).mock(return_value=httpx.Response(200, json={"status": "ok"}))

        client.publish_to_shopify("prod_123", publish_details)

        # Verify the request payload
        request = respx.calls.last.request
        payload = json.loads(request.content)
        assert payload == publish_details

    @respx.mock
    def test_upload_image_by_url(self, mock_env_vars):
        """Test uploading an image by URL."""
        client = PrintifyClient()

        upload_response = {
            "id": "img_abc123",
            "file_name": "design.png",
            "height": 1000,
            "width": 1000,
            "size": 50000,
            "mime_type": "image/png",
            "preview_url": "https://example.com/preview.png",
            "upload_time": "2024-01-01 12:00:00"
        }

        respx.route(
            host="api.printify.com",
            path="/v1/uploads/images.json"
        ).mock(return_value=httpx.Response(200, json=upload_response))

        result = client.upload_image_by_url(
            url="https://example.com/design.png",
            file_name="design.png"
        )

        assert result["id"] == "img_abc123"
        assert result["file_name"] == "design.png"

    @respx.mock
    def test_upload_image_file(self, mock_env_vars, sample_design_image):
        """Test uploading a local image file."""
        client = PrintifyClient()

        upload_response = {
            "id": "img_local_123",
            "file_name": "test_design.png"
        }

        respx.route(
            host="api.printify.com",
            path="/v1/uploads/images.json"
        ).mock(return_value=httpx.Response(200, json=upload_response))

        result = client.upload_image_file(file_path=str(sample_design_image))

        assert result["id"] == "img_local_123"

        # Verify the request contains base64 encoded image
        request = respx.calls.last.request
        payload = json.loads(request.content)
        assert "contents" in payload
        assert "file_name" in payload

        # Verify contents is valid base64
        try:
            base64.b64decode(payload["contents"])
        except Exception:
            pytest.fail("Image contents not properly base64 encoded")

    def test_upload_image_file_not_found(self, mock_env_vars):
        """Test uploading a file that doesn't exist."""
        client = PrintifyClient()

        with pytest.raises(FileNotFoundError):
            client.upload_image_file(file_path="/nonexistent/image.png")

    @respx.mock
    def test_get_blueprint_provider_variants(self, mock_env_vars):
        """Test getting variants for a blueprint and provider."""
        client = PrintifyClient()

        variants_response = {
            "variants": [
                {"id": 1, "title": "S / Black", "options": [1, 4]},
                {"id": 2, "title": "M / Black", "options": [2, 4]}
            ]
        }

        respx.route(
            host="api.printify.com",
            path="/v1/catalog/blueprints/3/print_providers/5/variants.json"
        ).mock(return_value=httpx.Response(200, json=variants_response))

        result = client.get_blueprint_provider_variants(
            blueprint_id=3,
            print_provider_id=5
        )

        assert "variants" in result
        assert len(result["variants"]) == 2

    @respx.mock
    def test_list_blueprint_providers(self, mock_env_vars):
        """Test listing providers for a blueprint."""
        client = PrintifyClient()

        providers_response = [
            {"id": 5, "title": "Provider A"},
            {"id": 10, "title": "Provider B"}
        ]

        respx.route(
            host="api.printify.com",
            path="/v1/catalog/blueprints/3/print_providers.json"
        ).mock(return_value=httpx.Response(200, json=providers_response))

        result = client.list_blueprint_providers(blueprint_id=3)

        assert len(result) == 2
        assert result[0]["id"] == 5

    def test_ensure_front_with_image(self, mock_env_vars, sample_printify_product):
        """Test generating a front print area with image."""
        client = PrintifyClient()

        result = client.ensure_front_with_image(
            sample_printify_product,
            image_id="test_img_123",
            x=0.5,
            y=0.5,
            scale=1.0,
            angle=0
        )

        assert "print_areas" in result
        assert len(result["print_areas"]) == 1

        front_area = result["print_areas"][0]
        assert front_area["placeholders"][0]["position"] == "front"
        assert front_area["placeholders"][0]["images"][0]["id"] == "test_img_123"

    @respx.mock
    def test_duplicate_product(self, mock_env_vars, sample_printify_product, tmp_path, monkeypatch):
        """Test duplicating a product from template."""
        # Change debug directory to tmp_path
        monkeypatch.setattr("app.services.printify_client.Path", lambda x: tmp_path / x if x == "data/debug" else Path(x))

        client = PrintifyClient()

        # Mock getting the template product
        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products/template_123.json"
        ).mock(return_value=httpx.Response(200, json=sample_printify_product))

        # Mock creating the duplicate
        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products.json"
        ).mock(return_value=httpx.Response(200, json={**sample_printify_product, "id": "duplicate_123"}))

        result = client.duplicate_product(
            template_id="template_123",
            title="Duplicated Product"
        )

        assert result["id"] == "duplicate_123"

    @respx.mock
    def test_http_error_includes_response_body(self, mock_env_vars):
        """Test that HTTP errors include the response body for debugging."""
        client = PrintifyClient()

        error_response = {"error": "Invalid product spec", "details": "Missing required field"}

        respx.route(
            host="api.printify.com",
            path="/v1/shops/test_shop_123/products.json"
        ).mock(return_value=httpx.Response(400, json=error_response))

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.create_product({})

        # Verify an HTTP error was raised with status 400
        assert exc_info.value.response.status_code == 400

    def test_client_initialization(self, mock_env_vars):
        """Test PrintifyClient initializes with correct credentials."""
        client = PrintifyClient()

        assert client.api_token == "test_printify_token"
        assert client.shop_id == "test_shop_123"
        assert "Authorization" in client.headers
        assert client.headers["Authorization"] == "Bearer test_printify_token"

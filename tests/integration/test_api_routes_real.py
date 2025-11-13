"""
Real integration tests for API routes.

These tests use Flask's test client to make actual HTTP requests to routes,
but mock external dependencies (Printify, Shopify APIs, storage).
"""
import json
import pytest
from unittest.mock import Mock, patch

from app.storage.json_store import JsonStore


@pytest.mark.integration
class TestAPIRoutes:
    """Tests for core API routes in app/routes/api.py"""

    def test_get_products_empty(self, client):
        """Test GET /api/products returns empty list when no products cached."""
        with patch('app.routes.api.store') as mock_store:
            mock_store.list.return_value = []

            response = client.get('/api/products')

            assert response.status_code == 200
            data = response.get_json()
            assert data == []
            mock_store.list.assert_called_once_with('shopify_products')

    def test_get_products_with_data(self, client):
        """Test GET /api/products returns cached products."""
        mock_products = [
            {
                "id": "123",
                "title": "Test Product",
                "url": "https://example.com/product",
                "type": "T-Shirt",
                "tags": ["test"],
            },
            {
                "id": "456",
                "title": "Another Product",
                "url": "https://example.com/product2",
                "type": "Hoodie",
                "tags": ["test", "hoodie"],
            }
        ]

        with patch('app.routes.api.store') as mock_store:
            mock_store.list.return_value = mock_products

            response = client.get('/api/products')

            assert response.status_code == 200
            data = response.get_json()
            assert len(data) == 2
            assert data[0]["title"] == "Test Product"
            assert data[1]["type"] == "Hoodie"

    def test_update_products_cache(self, client):
        """Test POST /api/products/cache/update fetches and caches products."""
        mock_shopify_products = [
            {
                "id": 123456,
                "title": "Shopify Product",
                "handle": "test-product",
                "body_html": "<p>Description</p>",
                "product_type": "T-Shirt",
                "tags": "test, product",
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
                "image": {"src": "https://example.com/image.jpg"},
                "images": [{"id": 1, "src": "https://example.com/image.jpg"}],
                "options": [
                    {"position": 1, "name": "Size"},
                    {"position": 2, "name": "Color"}
                ],
                "variants": [
                    {
                        "id": 111,
                        "title": "S / Black",
                        "sku": "TEST-S-BLK",
                        "option1": "S",
                        "option2": "Black",
                        "price": "25.00",
                        "available": True,
                        "image_id": 1
                    }
                ]
            }
        ]

        with patch('app.routes.api.shopify_client') as mock_shopify, \
             patch('app.routes.api.store') as mock_store:

            mock_shopify.list_all_products.return_value = mock_shopify_products
            mock_shopify.product_url.return_value = "https://example.com/products/test-product"

            response = client.post('/api/products/cache/update')

            assert response.status_code == 200
            data = response.get_json()
            assert data["count"] == 1

            # Verify store.replace_collection was called with normalized data
            mock_store.replace_collection.assert_called_once()
            call_args = mock_store.replace_collection.call_args
            assert call_args[0][0] == 'shopify_products'  # collection name

            normalized = call_args[0][1]  # the data
            assert '123456' in normalized
            product = normalized['123456']
            assert product['title'] == 'Shopify Product'
            assert product['type'] == 'T-Shirt'
            assert len(product['variants']) == 1
            assert product['variants'][0]['color'] == 'Black'
            assert product['variants'][0]['size'] == 'S'

    def test_recommend_colors_missing_product_id(self, client):
        """Test POST /api/recommend/colors returns 400 when product_id missing."""
        response = client.post(
            '/api/recommend/colors',
            data=json.dumps({}),
            content_type='application/json'
        )

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
        assert 'product_id' in data['error'].lower()

    def test_recommend_colors_success(self, client):
        """Test POST /api/recommend/colors with valid product."""
        with patch('app.routes.api.printify_client') as mock_printify, \
             patch('app.routes.api.OpenAI') as mock_openai_class:

            # Mock a valid Printify product with color options
            mock_product = {
                "id": "test_product_123",
                "blueprint_id": 145,
                "print_provider_id": 39,
                "options": [
                    {
                        "name": "Colors",
                        "type": "color",
                        "values": [
                            {"id": 1, "title": "Black", "colors": ["#000000"]},
                            {"id": 2, "title": "White", "colors": ["#FFFFFF"]},
                            {"id": 3, "title": "Navy", "colors": ["#000080"]}
                        ]
                    }
                ]
            }
            mock_printify.get_product.return_value = mock_product

            # Mock OpenAI client response
            mock_openai = Mock()
            mock_openai_class.return_value = mock_openai
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.content = json.dumps({
                "light": [
                    {"title": "Black", "hex": "#000000"},
                    {"title": "Navy", "hex": "#000080"}
                ],
                "dark": [
                    {"title": "White", "hex": "#FFFFFF"}
                ]
            })
            mock_openai.chat.completions.create.return_value = mock_response

            response = client.post(
                '/api/recommend/colors',
                data=json.dumps({"product_id": "test_product_123"}),
                content_type='application/json'
            )

            # Should return color recommendations
            assert response.status_code == 200
            data = response.get_json()
            assert "light" in data
            assert "dark" in data
            assert "available_colors" in data
            assert len(data["light"]) > 0
            assert len(data["dark"]) > 0


@pytest.mark.integration
class TestPageRoutes:
    """Tests for page routes that render templates."""

    def test_homepage(self, client):
        """Test GET / renders homepage."""
        response = client.get('/')

        assert response.status_code == 200
        assert b'<!DOCTYPE html>' in response.data or b'<html' in response.data

    def test_shopify_index(self, client):
        """Test GET /shopify/ renders shopify page."""
        with patch('app.routes.shopify.store') as mock_store:
            mock_store.list.return_value = []

            response = client.get('/shopify/')

            assert response.status_code == 200

    def test_printify_index(self, client):
        """Test GET /printify renders printify page."""
        with patch('app.routes.printify.store') as mock_store:
            mock_store.list.return_value = []

            response = client.get('/printify')

            assert response.status_code == 200


@pytest.mark.integration
class TestErrorHandling:
    """Test error handling in routes."""

    def test_api_endpoint_with_invalid_json(self, client):
        """Test API endpoints handle invalid JSON gracefully."""
        # Mock external APIs since this endpoint doesn't actually use request body
        with patch('app.routes.api.shopify_client') as mock_shopify, \
             patch('app.routes.api.store') as mock_store:

            mock_shopify.list_all_products.return_value = []

            response = client.post(
                '/api/products/cache/update',
                data='not valid json{{{',
                content_type='application/json'
            )

            # Should either succeed (doesn't use body) or return 400
            assert response.status_code in [200, 400, 415]

    def test_get_nonexistent_endpoint(self, client):
        """Test accessing non-existent endpoint returns 404."""
        response = client.get('/api/nonexistent/route')

        assert response.status_code == 404

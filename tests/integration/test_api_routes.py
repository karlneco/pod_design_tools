"""
Integration tests for core API routes.
"""
import json
import pytest
import respx
import httpx

from app.storage.json_store import JsonStore


@pytest.mark.integration
class TestCoreAPIRoutes:
    """Integration tests for core API endpoints."""

    def test_get_products_empty(self, client, mocker):
        """Test GET /api/products with no cached products."""
        mock_store = mocker.Mock(spec=JsonStore)
        mock_store.list.return_value = []

        mocker.patch('app.routes.api.store', mock_store)

        response = client.get('/api/products')

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_get_products_with_cached_data(self, client, mocker):
        """Test GET /api/products with cached products."""
        mock_products = [
            {"id": 1, "title": "Product 1"},
            {"id": 2, "title": "Product 2"}
        ]

        mock_store = mocker.Mock(spec=JsonStore)
        mock_store.list.return_value = mock_products

        mocker.patch('app.routes.api.store', mock_store)

        response = client.get('/api/products')

        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 2
        assert data[0]["title"] == "Product 1"

    @respx.mock
    def test_update_product_cache(self, client, mocker):
        """Test POST /api/products/cache/update."""
        mock_shopify_products = [
            {"id": 123, "title": "Shopify Product"}
        ]

        # Mock the Shopify client
        mock_shopify = mocker.Mock()
        mock_shopify.list_all_products.return_value = mock_shopify_products

        # Mock the store
        mock_store = mocker.Mock(spec=JsonStore)

        mocker.patch('app.routes.api.shopify_client', mock_shopify)
        mocker.patch('app.routes.api.store', mock_store)

        response = client.post('/api/products/cache/update')

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "products_count" in data

        # Verify cache was updated
        mock_store.replace_collection.assert_called_once()


@pytest.mark.integration
class TestPrintifyAPIRoutes:
    """Integration tests for Printify API endpoints."""

    @respx.mock
    def test_get_printify_products(self, client, mocker):
        """Test GET /api/printify/products."""
        mock_products = {
            "data": [
                {"id": "prod1", "title": "Product 1"},
                {"id": "prod2", "title": "Product 2"}
            ]
        }

        # Mock Printify client
        mock_printify = mocker.Mock()
        mock_printify.list_products.return_value = mock_products

        mocker.patch('app.routes.printify_api.printify_client', mock_printify)

        response = client.get('/api/printify/products')

        assert response.status_code == 200
        data = response.get_json()
        assert len(data["data"]) == 2

    @respx.mock
    def test_update_printify_cache(self, client, mocker):
        """Test POST /api/printify/products/cache/update."""
        mock_products = {
            "data": [
                {"id": "prod1", "title": "Product 1"}
            ]
        }

        # Mock Printify client
        mock_printify = mocker.Mock()
        mock_printify.list_products.return_value = mock_products

        # Mock store
        mock_store = mocker.Mock(spec=JsonStore)

        mocker.patch('app.routes.printify_api.printify_client', mock_printify)
        mocker.patch('app.routes.printify_api.store', mock_store)

        response = client.post('/api/printify/products/cache/update')

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "success"


@pytest.mark.integration
class TestShopifyAPIRoutes:
    """Integration tests for Shopify API endpoints."""

    @respx.mock
    def test_save_shopify_product(self, client, mocker, sample_shopify_product):
        """Test POST /api/shopify/products/<id>/save."""
        product_id = "987654321"

        # Mock Shopify client
        mock_shopify = mocker.Mock()
        mock_shopify.update_product.return_value = sample_shopify_product["product"]
        mock_shopify.get_product.return_value = sample_shopify_product["product"]

        # Mock store
        mock_store = mocker.Mock(spec=JsonStore)
        mock_store.get.return_value = sample_shopify_product["product"]

        mocker.patch('app.routes.shopify_api.shopify_client', mock_shopify)
        mocker.patch('app.routes.shopify_api.store', mock_store)

        payload = {
            "title": "Updated Product Title",
            "body_html": "<p>Updated description</p>",
            "tags": "updated, tags"
        }

        response = client.post(
            f'/api/shopify/products/{product_id}/save',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"

    @respx.mock
    def test_upload_shopify_product_images(self, client, mocker, sample_design_image):
        """Test POST /api/shopify/products/<id>/images."""
        product_id = "987654321"

        # Mock Shopify client
        mock_shopify = mocker.Mock()
        mock_shopify.upload_product_images.return_value = [
            {"image": {"id": 111, "src": "https://cdn.shopify.com/image1.jpg"}}
        ]

        mocker.patch('app.routes.shopify_api.shopify_client', mock_shopify)

        # Simulate file upload
        with open(sample_design_image, 'rb') as img_file:
            response = client.post(
                f'/api/shopify/products/{product_id}/images',
                data={'files': (img_file, 'test_design.png')},
                content_type='multipart/form-data'
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "uploaded_count" in data


@pytest.mark.integration
class TestDesignsAPIRoutes:
    """Integration tests for Designs API endpoints."""

    def test_list_designs_empty(self, client, mocker):
        """Test GET /api/designs with no designs."""
        mock_store = mocker.Mock(spec=JsonStore)
        mock_store.list.return_value = []

        mocker.patch('app.routes.designs_api.store', mock_store)

        response = client.get('/api/designs')

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_designs_with_data(self, client, mocker):
        """Test GET /api/designs with existing designs."""
        mock_designs = [
            {"slug": "design-1", "title": "Design 1"},
            {"slug": "design-2", "title": "Design 2"}
        ]

        mock_store = mocker.Mock(spec=JsonStore)
        mock_store.list.return_value = mock_designs

        mocker.patch('app.routes.designs_api.store', mock_store)

        response = client.get('/api/designs')

        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 2

    def test_get_single_design(self, client, mocker):
        """Test GET /api/designs with slug parameter."""
        mock_design = {"slug": "test-design", "title": "Test Design"}

        mock_store = mocker.Mock(spec=JsonStore)
        mock_store.get.return_value = mock_design

        mocker.patch('app.routes.designs_api.store', mock_store)

        response = client.get('/api/designs?slug=test-design')

        assert response.status_code == 200
        data = response.get_json()
        assert data["slug"] == "test-design"

    def test_create_design(self, client, mocker):
        """Test POST /api/designs to create new design."""
        mock_store = mocker.Mock(spec=JsonStore)

        mocker.patch('app.routes.designs_api.store', mock_store)

        payload = {
            "slug": "new-design",
            "title": "New Design",
            "collections": ["Nature", "Travel"]
        }

        response = client.post(
            '/api/designs',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"

        # Verify design was saved
        mock_store.upsert.assert_called_once()


@pytest.mark.integration
class TestPageRoutes:
    """Integration tests for page routes."""

    def test_homepage(self, client):
        """Test GET / renders homepage."""
        response = client.get('/')

        assert response.status_code == 200
        # Check that HTML is returned
        assert b'<!DOCTYPE html>' in response.data or b'<html' in response.data

    def test_shopify_index(self, client, mocker):
        """Test GET /shopify/ renders shopify index."""
        # Mock store to avoid database access
        mock_store = mocker.Mock(spec=JsonStore)
        mock_store.list.return_value = []

        mocker.patch('app.routes.shopify.store', mock_store)

        response = client.get('/shopify/')

        assert response.status_code == 200

    def test_printify_index(self, client, mocker):
        """Test GET /printify renders printify index."""
        mock_store = mocker.Mock(spec=JsonStore)
        mock_store.list.return_value = []

        mocker.patch('app.routes.printify.store', mock_store)

        response = client.get('/printify')

        assert response.status_code == 200


@pytest.mark.integration
class TestColorRecommendations:
    """Integration tests for color recommendation API."""

    @respx.mock
    def test_recommend_colors(self, client, mocker):
        """Test POST /api/recommend/colors."""
        mock_colors = [
            {"name": "Black", "hex": "#000000", "why": "High contrast"},
            {"name": "White", "hex": "#FFFFFF", "why": "Versatile"}
        ]

        # Mock OpenAI service
        mocker.patch('app.routes.api.suggest_colors', return_value=mock_colors)

        # Mock Printify client to return available colors
        mock_printify = mocker.Mock()
        mock_printify.get_blueprint_provider_variants.return_value = {
            "variants": [
                {"id": 1, "title": "S / Black", "options": [{"colors": ["#000000"]}]},
                {"id": 2, "title": "S / White", "options": [{"colors": ["#FFFFFF"]}]}
            ]
        }

        mocker.patch('app.routes.api.printify_client', mock_printify)

        payload = {
            "title": "Mountain Design",
            "collections": ["Nature"],
            "notes": "Outdoor theme",
            "blueprint_id": 3,
            "print_provider_id": 5
        }

        response = client.post(
            '/api/recommend/colors',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = response.get_json()
        assert "recommended" in data
        assert "available" in data

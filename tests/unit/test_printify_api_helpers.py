"""
Unit tests for helper functions in app/routes/printify_api.py.

These functions are not Flask routes but pure utility functions that
should be tested independently.
"""
import pytest
from unittest.mock import patch
from app.routes.printify_api import _to_bool, _normalize_printify_for_cache


class TestToBool:
    """Test the _to_bool helper function."""

    def test_bool_true(self):
        """Test with boolean True."""
        assert _to_bool(True) is True

    def test_bool_false(self):
        """Test with boolean False."""
        assert _to_bool(False) is False

    def test_int_zero(self):
        """Test with integer 0 returns False."""
        assert _to_bool(0) is False

    def test_int_nonzero(self):
        """Test with non-zero integer returns True."""
        assert _to_bool(1) is True
        assert _to_bool(-1) is True
        assert _to_bool(100) is True

    def test_float_zero(self):
        """Test with float 0.0 returns False."""
        assert _to_bool(0.0) is False

    def test_float_nonzero(self):
        """Test with non-zero float returns True."""
        assert _to_bool(1.5) is True
        assert _to_bool(-0.1) is True

    def test_string_true_values(self):
        """Test string values that should return True."""
        assert _to_bool("1") is True
        assert _to_bool("true") is True
        assert _to_bool("TRUE") is True
        assert _to_bool("True") is True
        assert _to_bool("yes") is True
        assert _to_bool("YES") is True
        assert _to_bool("y") is True
        assert _to_bool("Y") is True
        assert _to_bool("on") is True
        assert _to_bool("ON") is True

    def test_string_false_values(self):
        """Test string values that should return False."""
        assert _to_bool("0") is False
        assert _to_bool("false") is False
        assert _to_bool("FALSE") is False
        assert _to_bool("no") is False
        assert _to_bool("off") is False
        assert _to_bool("") is False
        assert _to_bool("random") is False

    def test_string_with_whitespace(self):
        """Test that whitespace is stripped."""
        assert _to_bool("  true  ") is True
        assert _to_bool("  1  ") is True
        assert _to_bool("  false  ") is False

    def test_none_returns_false(self):
        """Test None returns False."""
        assert _to_bool(None) is False

    def test_list_returns_false(self):
        """Test list returns False."""
        assert _to_bool([]) is False
        assert _to_bool([1, 2, 3]) is False

    def test_dict_returns_false(self):
        """Test dict returns False."""
        assert _to_bool({}) is False
        assert _to_bool({"key": "value"}) is False


class TestNormalizePrintifyForCache:
    """Test the _normalize_printify_for_cache helper function."""

    def test_basic_normalization(self):
        """Test basic product normalization."""
        product = {
            "id": "test_123",
            "title": "Test Product",
            "images": [{"src": "https://example.com/image.jpg"}],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z"
        }

        result = _normalize_printify_for_cache(product)

        assert result["id"] == "test_123"
        assert result["title"] == "Test Product"
        assert result["primary_image"] == "https://example.com/image.jpg"
        assert result["created_at"] == "2024-01-01T00:00:00Z"
        assert result["updated_at"] == "2024-01-02T00:00:00Z"

    def test_normalization_with_shopify_external(self):
        """Test normalization extracts Shopify data from external field."""
        product = {
            "id": "prod_456",
            "title": "Shopify Product",
            "images": [{"src": "https://cdn.com/img.png"}],
            "external": {
                "id": "8140887523465",
                "handle": "test-product-handle"
            }
        }

        with patch('app.routes.printify_api.os.getenv') as mock_getenv:
            mock_getenv.return_value = "mystore.myshopify.com"
            result = _normalize_printify_for_cache(product)

        assert result["shopify_product_id"] == "8140887523465"
        assert result["shopify_handle"] == "test-product-handle"
        assert result["shopify_url"] == "https://mystore.myshopify.com/products/test-product-handle"
        assert result["published"] is True

    def test_normalization_without_external(self):
        """Test normalization when no Shopify external data exists."""
        product = {
            "id": "unpub_789",
            "title": "Unpublished Product",
            "images": [{"src": "https://cdn.com/design.jpg"}]
        }

        result = _normalize_printify_for_cache(product)

        assert result["shopify_product_id"] is None
        assert result["shopify_handle"] is None
        assert result["shopify_url"] is None
        assert result["published"] is False

    def test_normalization_uses_name_fallback(self):
        """Test that 'name' field is used if 'title' is missing."""
        product = {
            "id": "test_name",
            "name": "Product Name from name field",
            "images": []
        }

        result = _normalize_printify_for_cache(product)
        assert result["title"] == "Product Name from name field"

    def test_normalization_image_from_url_key(self):
        """Test image extraction from 'url' key."""
        product = {
            "id": "img_test",
            "title": "Image Test",
            "images": [{"url": "https://cdn.com/url-image.jpg"}]
        }

        result = _normalize_printify_for_cache(product)
        assert result["primary_image"] == "https://cdn.com/url-image.jpg"

    def test_normalization_image_from_string(self):
        """Test image when images array contains string."""
        product = {
            "id": "str_img",
            "title": "String Image",
            "images": ["https://direct-url.com/image.png"]
        }

        result = _normalize_printify_for_cache(product)
        assert result["primary_image"] == "https://direct-url.com/image.png"

    def test_normalization_image_from_preview(self):
        """Test fallback to preview when no images."""
        product = {
            "id": "preview_test",
            "title": "Preview Image",
            "images": [],
            "preview": {"src": "https://preview.com/img.jpg"}
        }

        result = _normalize_printify_for_cache(product)
        assert result["primary_image"] == "https://preview.com/img.jpg"

    def test_normalization_preview_as_string(self):
        """Test preview field as string."""
        product = {
            "id": "preview_str",
            "title": "Preview String",
            "images": [],
            "preview": "https://preview-direct.com/img.jpg"
        }

        result = _normalize_printify_for_cache(product)
        assert result["primary_image"] == "https://preview-direct.com/img.jpg"

    def test_normalization_external_as_string_numeric(self):
        """Test external field as numeric string (Shopify ID)."""
        product = {
            "id": "ext_str",
            "title": "External String",
            "images": [],
            "external": "123456789"
        }

        result = _normalize_printify_for_cache(product)
        assert result["shopify_product_id"] == "123456789"

    def test_normalization_external_as_string_handle(self):
        """Test external field as non-numeric string (handle)."""
        product = {
            "id": "ext_handle",
            "title": "External Handle",
            "images": [],
            "external": "my-product-handle"
        }

        result = _normalize_printify_for_cache(product)
        assert result["shopify_handle"] == "my-product-handle"

    def test_normalization_shopify_domain_with_https(self):
        """Test domain normalization strips https://."""
        product = {
            "id": "domain_test",
            "title": "Domain Test",
            "images": [],
            "external": {"handle": "test-handle"}
        }

        with patch('app.routes.printify_api.os.getenv') as mock_getenv:
            mock_getenv.return_value = "https://mystore.myshopify.com/"
            result = _normalize_printify_for_cache(product)

        assert result["shopify_url"] == "https://mystore.myshopify.com/products/test-handle"

    def test_normalization_shopify_domain_with_http(self):
        """Test domain normalization strips http://."""
        product = {
            "id": "http_test",
            "title": "HTTP Test",
            "images": [],
            "external": {"handle": "test-product"}
        }

        with patch('app.routes.printify_api.os.getenv') as mock_getenv:
            mock_getenv.return_value = "http://mystore.com"
            result = _normalize_printify_for_cache(product)

        assert result["shopify_url"] == "https://mystore.com/products/test-product"

    def test_normalization_uses_id_field(self):
        """Test that product id is correctly extracted."""
        product = {
            "id": "primary_id",
            "_id": "backup_id",
            "title": "ID Test",
            "images": []
        }

        result = _normalize_printify_for_cache(product)
        assert result["id"] == "primary_id"

    def test_normalization_uses_underscore_id_fallback(self):
        """Test that _id is used when id is missing."""
        product = {
            "_id": "underscore_id",
            "title": "Underscore ID Test",
            "images": []
        }

        result = _normalize_printify_for_cache(product)
        assert result["id"] == "underscore_id"

    def test_normalization_external_variant_keys(self):
        """Test various key names for external Shopify data."""
        product = {
            "id": "key_var",
            "title": "Key Variants",
            "images": [],
            "external": {
                "product_id": "999888",
                "shopify_handle": "variant-handle"
            }
        }

        result = _normalize_printify_for_cache(product)
        assert result["shopify_product_id"] == "999888"
        assert result["shopify_handle"] == "variant-handle"

    def test_normalization_product_handle_key(self):
        """Test product_handle key variant."""
        product = {
            "id": "handle_var",
            "title": "Handle Variant",
            "images": [],
            "external": {
                "id": "111222",
                "product_handle": "product-handle-name"
            }
        }

        result = _normalize_printify_for_cache(product)
        assert result["shopify_handle"] == "product-handle-name"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

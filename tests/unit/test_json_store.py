"""
Unit tests for JsonStore storage layer.
"""
import json
import pytest
from pathlib import Path

from app.storage.json_store import JsonStore


@pytest.mark.unit
class TestJsonStore:
    """Tests for JsonStore CRUD operations."""

    def test_init_creates_directory(self, tmp_path):
        """Test that JsonStore creates the data directory if it doesn't exist."""
        data_dir = tmp_path / "data"
        assert not data_dir.exists()

        store = JsonStore(str(data_dir))

        assert data_dir.exists()
        assert data_dir.is_dir()

    def test_init_with_existing_directory(self, tmp_path):
        """Test that JsonStore works with an existing directory."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        store = JsonStore(str(data_dir))

        assert data_dir.exists()
        assert store.data_dir == data_dir

    def test_list_empty_collection(self, json_store):
        """Test listing items from a non-existent collection returns empty list."""
        result = json_store.list("products")

        assert result == []

    def test_list_with_items(self, json_store):
        """Test listing items from a collection with data."""
        json_store.upsert("products", "1", {"name": "Product 1"})
        json_store.upsert("products", "2", {"name": "Product 2"})

        result = json_store.list("products")

        assert len(result) == 2
        assert {"name": "Product 1"} in result
        assert {"name": "Product 2"} in result

    def test_get_non_existent_item(self, json_store):
        """Test getting an item that doesn't exist returns None."""
        result = json_store.get("products", "999")

        assert result is None

    def test_get_existing_item(self, json_store):
        """Test getting an existing item returns the correct data."""
        json_store.upsert("products", "123", {"name": "T-Shirt", "price": 25})

        result = json_store.get("products", "123")

        assert result == {"name": "T-Shirt", "price": 25}

    def test_upsert_creates_new_item(self, json_store):
        """Test that upsert creates a new item."""
        json_store.upsert("products", "abc", {"title": "New Product"})

        result = json_store.get("products", "abc")

        assert result == {"title": "New Product"}

    def test_upsert_updates_existing_item(self, json_store):
        """Test that upsert updates an existing item."""
        json_store.upsert("products", "xyz", {"name": "Original"})
        json_store.upsert("products", "xyz", {"name": "Updated", "new_field": "value"})

        result = json_store.get("products", "xyz")

        assert result == {"name": "Updated", "new_field": "value"}

    def test_upsert_persists_to_disk(self, json_store, temp_data_dir):
        """Test that upsert actually writes data to disk."""
        json_store.upsert("products", "123", {"name": "Persisted"})

        # Verify file exists and contains correct data
        file_path = temp_data_dir / "products.json"
        assert file_path.exists()

        with open(file_path) as f:
            data = json.load(f)

        assert "123" in data
        assert data["123"] == {"name": "Persisted"}

    def test_delete_existing_item(self, json_store):
        """Test deleting an existing item."""
        json_store.upsert("products", "del1", {"name": "To Delete"})
        json_store.delete("products", "del1")

        result = json_store.get("products", "del1")

        assert result is None

    def test_delete_non_existent_item(self, json_store):
        """Test deleting a non-existent item doesn't raise an error."""
        # Should not raise an exception
        json_store.delete("products", "nonexistent")

        # Collection should still be empty
        assert json_store.list("products") == []

    def test_delete_persists_to_disk(self, json_store, temp_data_dir):
        """Test that delete actually removes data from disk."""
        json_store.upsert("products", "1", {"name": "Item 1"})
        json_store.upsert("products", "2", {"name": "Item 2"})
        json_store.delete("products", "1")

        # Verify file contains only item 2
        file_path = temp_data_dir / "products.json"
        with open(file_path) as f:
            data = json.load(f)

        assert "1" not in data
        assert "2" in data

    def test_replace_collection(self, json_store):
        """Test replacing an entire collection."""
        # Add some initial data
        json_store.upsert("products", "old1", {"name": "Old 1"})
        json_store.upsert("products", "old2", {"name": "Old 2"})

        # Replace the entire collection
        new_data = {
            "new1": {"name": "New 1"},
            "new2": {"name": "New 2"}
        }
        json_store.replace_collection("products", new_data)

        # Verify old data is gone and new data exists
        assert json_store.get("products", "old1") is None
        assert json_store.get("products", "new1") == {"name": "New 1"}
        assert len(json_store.list("products")) == 2

    def test_replace_collection_with_invalid_type(self, json_store):
        """Test that replace_collection raises TypeError for non-dict input."""
        with pytest.raises(TypeError, match="mapping must be a dict"):
            json_store.replace_collection("products", ["not", "a", "dict"])

    def test_replace_collection_empty(self, json_store):
        """Test replacing a collection with empty dict."""
        json_store.upsert("products", "1", {"name": "Item"})
        json_store.replace_collection("products", {})

        result = json_store.list("products")

        assert result == []

    def test_multiple_collections(self, json_store):
        """Test that multiple collections can coexist independently."""
        json_store.upsert("products", "1", {"type": "product"})
        json_store.upsert("designs", "1", {"type": "design"})

        products = json_store.list("products")
        designs = json_store.list("designs")

        assert len(products) == 1
        assert len(designs) == 1
        assert products[0]["type"] == "product"
        assert designs[0]["type"] == "design"

    def test_upsert_with_nested_data(self, json_store):
        """Test upserting complex nested data structures."""
        complex_data = {
            "id": "123",
            "variants": [
                {"size": "S", "color": "Black"},
                {"size": "M", "color": "White"}
            ],
            "metadata": {
                "tags": ["test", "nested"],
                "created_at": "2024-01-01"
            }
        }

        json_store.upsert("products", "123", complex_data)
        result = json_store.get("products", "123")

        assert result == complex_data
        assert result["variants"][0]["size"] == "S"
        assert "test" in result["metadata"]["tags"]

    def test_unicode_support(self, json_store):
        """Test that JsonStore properly handles Unicode characters."""
        unicode_data = {
            "title": "日本語タイトル",
            "description": "Descripción en español",
            "emoji": "🎨🖌️"
        }

        json_store.upsert("products", "unicode", unicode_data)
        result = json_store.get("products", "unicode")

        assert result == unicode_data
        assert result["title"] == "日本語タイトル"

    def test_json_formatting(self, json_store, temp_data_dir):
        """Test that JSON files are formatted with indentation."""
        json_store.upsert("products", "1", {"name": "Test"})

        file_path = temp_data_dir / "products.json"
        content = file_path.read_text()

        # Should be indented (not minified)
        assert "\n" in content
        assert "  " in content  # Has indentation

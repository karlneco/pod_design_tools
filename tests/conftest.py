"""
Shared test fixtures and configuration for POD Design Tools tests.
"""
import json
import os
from pathlib import Path
from typing import Generator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.storage.json_store import JsonStore


# Test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"
API_RESPONSES_DIR = FIXTURES_DIR / "api_responses"


@pytest.fixture(scope="session")
def app() -> Flask:
    """Create and configure a test Flask application instance."""
    app = create_app()
    app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
    })
    yield app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Create a Flask test client."""
    return app.test_client()


@pytest.fixture
def runner(app: Flask):
    """Create a Flask CLI test runner."""
    return app.test_cli_runner()


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory for JsonStore tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def json_store(temp_data_dir: Path) -> JsonStore:
    """Create a JsonStore instance with temporary directory."""
    return JsonStore(str(temp_data_dir))


@pytest.fixture
def sample_printify_product() -> dict:
    """Load sample Printify product JSON fixture."""
    fixture_path = API_RESPONSES_DIR / "printify_product.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sample_shopify_product() -> dict:
    """Load sample Shopify product JSON fixture."""
    fixture_path = API_RESPONSES_DIR / "shopify_product.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sample_openai_metadata() -> dict:
    """Load sample OpenAI metadata response fixture."""
    fixture_path = API_RESPONSES_DIR / "openai_metadata.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for testing."""
    env_vars = {
        "FLASK_ENV": "testing",
        "PRINTIFY_API_TOKEN": "test_printify_token",
        "PRINTIFY_SHOP_ID": "test_shop_123",
        "SHOPIFY_STORE_DOMAIN": "test-store.myshopify.com",
        "SHOPIFY_ADMIN_TOKEN": "test_shopify_token",
        "SHOPIFY_API_VERSION": "2024-10",
        "OPENAI_API_KEY": "test_openai_key",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return env_vars


@pytest.fixture
def sample_design_image(tmp_path: Path) -> Path:
    """Create a sample design image for testing."""
    from PIL import Image

    # Create a simple test image (100x100 red square with transparency)
    img = Image.new('RGBA', (100, 100), (255, 0, 0, 255))
    img_path = tmp_path / "test_design.png"
    img.save(img_path, "PNG")
    return img_path


@pytest.fixture
def sample_mockup_template(tmp_path: Path) -> Path:
    """Create a sample mockup template for testing."""
    from PIL import Image

    # Create a simple mockup template (200x200 white background)
    img = Image.new('RGB', (200, 200), (255, 255, 255))
    template_path = tmp_path / "mockup_template.png"
    img.save(template_path, "PNG")
    return template_path


@pytest.fixture(autouse=True)
def reset_extensions(monkeypatch):
    """Reset Flask extensions between tests to avoid state pollution."""
    # This prevents extensions from persisting state between tests
    # We'll mock the clients in individual tests as needed
    pass


@pytest.fixture
def printify_client(mock_env_vars):
    """Create a PrintifyClient instance for testing."""
    from app.services.printify_client import PrintifyClient
    return PrintifyClient(
        api_token=mock_env_vars["PRINTIFY_API_TOKEN"],
        shop_id=mock_env_vars["PRINTIFY_SHOP_ID"]
    )


@pytest.fixture
def shopify_client(mock_env_vars):
    """Create a ShopifyClient instance for testing."""
    from app.services.shopify_client import ShopifyClient
    return ShopifyClient(
        store_domain=mock_env_vars["SHOPIFY_STORE_DOMAIN"],
        admin_token=mock_env_vars["SHOPIFY_ADMIN_TOKEN"],
        api_version=mock_env_vars["SHOPIFY_API_VERSION"]
    )


# Helper functions for tests

def load_fixture(filename: str) -> dict:
    """Load a JSON fixture file."""
    fixture_path = API_RESPONSES_DIR / filename
    with open(fixture_path) as f:
        return json.load(f)


def create_test_image(path: Path, width: int = 100, height: int = 100,
                     color: tuple = (255, 0, 0, 255)) -> Path:
    """Create a test image at the specified path."""
    from PIL import Image

    img = Image.new('RGBA', (width, height), color)
    img.save(path, "PNG")
    return path

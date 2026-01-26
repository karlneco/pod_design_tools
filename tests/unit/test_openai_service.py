"""
Unit tests for OpenAI service functions.
"""
import json
import pytest
import httpx
import respx
from pathlib import Path

from app.services import openai_svc


@pytest.mark.unit
class TestOpenAIService:
    """Tests for OpenAI service functions."""

    @respx.mock
    def test_suggest_metadata_success(self, mock_env_vars, tmp_path):
        """Test successful metadata suggestion."""
        # Create mock documentation files
        personas_file = tmp_path / "personas.md"
        personas_file.write_text("Mock personas content")

        principles_file = tmp_path / "principles.txt"
        principles_file.write_text("Mock principles content")

        policies_file = tmp_path / "policies.md"
        policies_file.write_text("Mock policies content")

        docs_paths = {
            "personas_pdf": str(personas_file),
            "principles": str(principles_file),
            "policies": str(policies_file)
        }

        # Mock OpenAI API response
        api_response = {
            "choices": [{
                "message": {
                    "content": (
                        "Product Title: Mountain Sunset T-Shirt\n\n"
                        "Description:\n"
                        "Embrace the beauty of mountain sunsets.\n"
                        "Perfect for outdoor enthusiasts.\n"
                        "Available in multiple colors.\n\n"
                        "Keywords: mountain, sunset, nature, outdoor, hiking, adventure, t-shirt, travel, japan\n\n"
                        "Shopify Tags: nature, outdoor, mountain, travel"
                    )
                }
            }]
        }

        respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=api_response))

        result = openai_svc.suggest_metadata(
            title_hint="Mountain Sunset",
            collections=["Nature", "Travel"],
            notes="Minimalist design",
            docs_paths=docs_paths
        )

        assert result["title"] == "Mountain Sunset T-Shirt"
        assert "mountain" in result["description"].lower()
        assert "mountain" in result["keywords"]
        assert "nature" in result["tags"]

    @respx.mock
    def test_suggest_metadata_parsing_fallback(self, mock_env_vars):
        """Test metadata suggestion with unparseable response."""
        docs_paths = {
            "personas_pdf": "",
            "principles": "",
            "policies": ""
        }

        # Mock response without clear structure
        api_response = {
            "choices": [{
                "message": {
                    "content": "This is an unstructured response without clear markers."
                }
            }]
        }

        respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=api_response))

        result = openai_svc.suggest_metadata(
            title_hint="Test",
            collections=[],
            notes="",
            docs_paths=docs_paths
        )

        # Should fall back to putting entire content in description
        assert result["description"] == "This is an unstructured response without clear markers."

    @respx.mock
    def test_suggest_metadata_api_error(self, mock_env_vars):
        """Test metadata suggestion when API returns error."""
        docs_paths = {"personas_pdf": "", "principles": "", "policies": ""}

        respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(500, json={"error": "API Error"}))

        with pytest.raises(httpx.HTTPStatusError):
            openai_svc.suggest_metadata(
                title_hint="Test",
                collections=[],
                notes="",
                docs_paths=docs_paths
            )

    @respx.mock
    def test_suggest_colors_with_json_response(self, mock_env_vars):
        """Test color suggestions with proper JSON response."""
        api_response = {
            "choices": [{
                "message": {
                    "content": json.dumps([
                        {"name": "Black", "hex": "#000000", "why": "High contrast"},
                        {"name": "Navy", "hex": "#000080", "why": "Professional look"},
                        {"name": "White", "hex": "#FFFFFF", "why": "Clean and versatile"}
                    ])
                }
            }]
        }

        respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=api_response))

        result = openai_svc.suggest_colors(
            design_title="Mountain Design",
            collections=["Nature"],
            notes="Outdoor theme"
        )

        assert len(result) == 3
        assert result[0]["name"] == "Black"
        assert result[0]["hex"] == "#000000"
        assert "why" in result[0]

    @respx.mock
    def test_suggest_colors_with_text_wrapper(self, mock_env_vars):
        """Test color suggestions when JSON is wrapped in text."""
        api_response = {
            "choices": [{
                "message": {
                    "content": (
                        "Here are some color suggestions:\n\n"
                        '[\n'
                        '  {"name": "Forest Green", "hex": "#228B22", "why": "Natural vibe"},\n'
                        '  {"name": "Sky Blue", "hex": "#87CEEB", "why": "Calming effect"}\n'
                        ']'
                    )
                }
            }]
        }

        respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=api_response))

        result = openai_svc.suggest_colors(
            design_title="Nature Design",
            collections=["Outdoor"],
            notes=""
        )

        assert len(result) == 2
        assert result[0]["name"] == "Forest Green"
        assert result[1]["hex"] == "#87CEEB"

    @respx.mock
    def test_suggest_colors_fallback(self, mock_env_vars):
        """Test color suggestions fallback to defaults on parse error."""
        api_response = {
            "choices": [{
                "message": {
                    "content": "Invalid JSON response without proper structure"
                }
            }]
        }

        respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=api_response))

        result = openai_svc.suggest_colors(
            design_title="Test",
            collections=[],
            notes=""
        )

        # Should return fallback colors
        assert len(result) == 2
        assert result[0]["name"] == "Black"
        assert result[1]["name"] == "White"

    @respx.mock
    def test_suggest_colors_api_error(self, mock_env_vars):
        """Test color suggestions when API returns error."""
        respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(429, json={"error": "Rate limit exceeded"}))

        result = openai_svc.suggest_colors(
            design_title="Test",
            collections=[],
            notes=""
        )

        # Should return fallback colors on error
        assert len(result) == 2
        assert result[0]["name"] == "Black"

    def test_read_file_safely_existing_file(self, tmp_path):
        """Test _read_file_safely with existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content")

        result = openai_svc._read_file_safely(str(test_file))

        assert result == "Test content"

    def test_read_file_safely_nonexistent_file(self):
        """Test _read_file_safely with nonexistent file."""
        result = openai_svc._read_file_safely("/nonexistent/file.txt")

        assert result == ""

    def test_read_file_safely_bounds_content(self, tmp_path):
        """Test _read_file_safely bounds content to 120k chars."""
        test_file = tmp_path / "large.txt"
        # Create content larger than 120k chars
        large_content = "x" * 150000
        test_file.write_text(large_content)

        result = openai_svc._read_file_safely(str(test_file))

        assert len(result) == 120000

    @respx.mock
    def test_chat_function_formats_request_correctly(self, mock_env_vars):
        """Test that _chat function formats the API request correctly."""
        api_response = {
            "choices": [{
                "message": {
                    "content": "Test response"
                }
            }]
        }

        route = respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=api_response))

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"}
        ]

        result = openai_svc._chat(messages)

        assert result == "Test response"

        # Verify request was made correctly
        assert route.called
        request = respx.calls.last.request
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-4o-mini"
        assert payload["messages"] == messages
        assert payload["temperature"] == 0.6

    @respx.mock
    def test_custom_openai_base_url(self, mock_env_vars, monkeypatch):
        """Test using custom OpenAI base URL."""
        # Set custom base URL
        monkeypatch.setenv("OPENAI_BASE", "https://custom-api.example.com/v1")
        
        # Need to reload the module to pick up the new env var
        import importlib
        importlib.reload(openai_svc)
        
        api_response = {
            "choices": [{
                "message": {
                    "content": "Response"
                }
            }]
        }

        route = respx.route(
            host="custom-api.example.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=api_response))

        result = openai_svc._chat([{"role": "user", "content": "Test"}])

        assert result == "Response"
        assert route.called
        
        # Reload again to reset
        monkeypatch.setenv("OPENAI_BASE", "https://api.openai.com/v1")
        importlib.reload(openai_svc)

    @respx.mock
    def test_suggest_metadata_includes_docs_in_prompt(self, mock_env_vars, tmp_path):
        """Test that suggest_metadata includes documentation in the prompt."""
        personas_file = tmp_path / "personas.md"
        personas_file.write_text("Target audience: outdoor enthusiasts")

        docs_paths = {
            "personas_pdf": str(personas_file),
            "principles": "",
            "policies": ""
        }

        api_response = {
            "choices": [{
                "message": {
                    "content": "Product Title: Test\n\nDescription: Test desc\n\nKeywords: test\n\nTags: test"
                }
            }]
        }

        route = respx.route(
            host="api.openai.com",
            path="/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=api_response))

        openai_svc.suggest_metadata(
            title_hint="Test",
            collections=[],
            notes="",
            docs_paths=docs_paths
        )

        # Verify the request includes personas content
        assert route.called
        request = respx.calls.last.request
        payload = json.loads(request.content)
        user_message = payload["messages"][1]["content"]
        assert "outdoor enthusiasts" in user_message

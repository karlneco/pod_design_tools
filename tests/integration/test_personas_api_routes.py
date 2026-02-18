import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.integration
class TestPersonasApiRoutes:
    def test_list_personas(self, client):
        with patch("app.routes.personas_api.list_personas") as mock_list:
            mock_list.return_value = [{"id": "a", "label": "A", "image_url": "/assets/personas/a.png"}]
            res = client.get("/api/personas")
            assert res.status_code == 200
            data = res.get_json()
            assert "personas" in data
            assert data["personas"][0]["id"] == "a"

    def test_create_persona_upload(self, client, tmp_path):
        personas_root = tmp_path / "personas"
        personas_root.mkdir(parents=True, exist_ok=True)
        with patch("app.routes.personas_api.personas_dir", return_value=personas_root), \
             patch("app.routes.personas_api.upsert_persona") as mock_upsert:
            mock_upsert.return_value = {"id": "new-persona", "label": "New Persona"}
            res = client.post(
                "/api/personas",
                data={
                    "label": "New Persona",
                    "age_segments": "25-34,35-44",
                    "notes": "test",
                    "photo": (io.BytesIO(b"img"), "persona.png"),
                },
                content_type="multipart/form-data",
            )
            assert res.status_code == 200
            data = res.get_json()
            assert data["ok"] is True
            assert "image_url" in data

    def test_generate_persona(self, client, tmp_path):
        personas_root = tmp_path / "personas"
        personas_root.mkdir(parents=True, exist_ok=True)
        with patch("app.routes.personas_api.personas_dir", return_value=personas_root), \
             patch("app.routes.personas_api.generate_lifestyle_images") as mock_gen, \
             patch("app.routes.personas_api.upsert_persona") as mock_upsert:
            mock_gen.return_value = [{"bytes": b"png", "mime_type": "image/png"}]
            mock_upsert.return_value = {"id": "gen", "label": "Generated"}
            res = client.post(
                "/api/personas/generate",
                data=json.dumps({
                    "label": "Generated",
                    "brief": "male, short black hair, 30s",
                    "age_segments": ["25-34"],
                }),
                content_type="application/json",
            )
            assert res.status_code == 200
            data = res.get_json()
            assert data["ok"] is True
            assert data["persona"]["id"] == "gen"

    def test_update_persona_json(self, client):
        with patch("app.routes.personas_api.store") as mock_store, \
             patch("app.routes.personas_api.upsert_persona") as mock_upsert:
            mock_store.get.return_value = {
                "id": "p1",
                "label": "Persona 1",
                "image_filename": "p1.png",
                "age_segments": ["25-34"],
                "notes": "",
                "source": "upload",
                "active": True,
            }
            mock_upsert.return_value = {"id": "p1", "label": "Persona 1b"}
            res = client.post(
                "/api/personas/p1",
                data=json.dumps({"label": "Persona 1b", "active": False}),
                content_type="application/json",
            )
            assert res.status_code == 200
            assert res.get_json()["ok"] is True

    def test_delete_persona(self, client):
        with patch("app.routes.personas_api.store") as mock_store:
            mock_store.get.return_value = {"id": "p1", "image_filename": "p1.png"}
            res = client.delete("/api/personas/p1")
            assert res.status_code == 200
            assert res.get_json()["ok"] is True


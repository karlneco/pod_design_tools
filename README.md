## POD Design Tools

Flask app for design metadata, mockup generation, and Shopify/Printify workflow support.

## Local run (port 5003)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

App URL: `http://127.0.0.1:5003`  
Health check: `http://127.0.0.1:5003/healthz`

## Docker run

```bash
docker compose up --build
```

Default mapping is `5003:5003`.

## Auto-deploy CI (Gitea)

This repo now follows the same auto-deploy structure as your other pod apps:

- Workflow: `.gitea/workflows/deploy.yml`
- Deploy compose: `docker-compose.deploy.yml`
- Deploy script: `scripts/deploy_compose.sh`

Deployment behavior:

- triggers on pushes to `main` (and manual dispatch)
- loads `.env` from app data path on the server
- rebuilds and deploys container via Docker Compose
- checks `http://127.0.0.1:5003/healthz`
- attempts rollback to previous image on failed health check

## Environment variables

See `.env.example` for required values:

- `SHOPIFY_STORE_DOMAIN`
- `SHOPIFY_ADMIN_TOKEN`
- `PRINTIFY_API_TOKEN`
- `PRINTIFY_SHOP_ID`
- `OPENAI_API_KEY`

## Tests

```bash
./test.sh all
```

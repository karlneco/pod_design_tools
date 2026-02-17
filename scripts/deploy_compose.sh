#!/usr/bin/env bash
set -euo pipefail

NO_CACHE="${1:-false}"
APP_DATA_DIR="${APP_DATA_DIR:-/srv/pod_design_tools/data}"
COMPOSE_FILE="docker-compose.deploy.yml"
SERVICE_NAME="pod-design-tools"
ROLLBACK_TAG="pod-design-tools:rollback"
TARGET_IMAGE="pod-design-tools:local"
HEALTH_URL="http://127.0.0.1:5003/healthz"

export APP_DATA_DIR

if [[ ! -f ".env" ]]; then
  echo "Missing env file in workspace: .env" >&2
  exit 1
fi

mkdir -p "${APP_DATA_DIR}" "${APP_DATA_DIR}/designs" "${APP_DATA_DIR}/debug" "${APP_DATA_DIR}/tmp"

if docker ps --filter "publish=5003" --format '{{.Names}}' | grep -qv '^pod_design_tools-pod-design-tools-1$'; then
  echo "Port 5003 is already in use by another running container:" >&2
  docker ps --filter "publish=5003" --format '  - {{.Names}} ({{.Ports}})' >&2
  echo "Stop the conflicting container or change the app port." >&2
  exit 1
fi

HAS_ROLLBACK_IMAGE=false
CURRENT_CONTAINER_ID="$(docker compose -f "${COMPOSE_FILE}" ps -q "${SERVICE_NAME}" || true)"
if [[ -n "${CURRENT_CONTAINER_ID}" ]]; then
  CURRENT_IMAGE_ID="$(docker inspect -f '{{.Image}}' "${CURRENT_CONTAINER_ID}")"
  if [[ -n "${CURRENT_IMAGE_ID}" ]]; then
    docker image tag "${CURRENT_IMAGE_ID}" "${ROLLBACK_TAG}"
    HAS_ROLLBACK_IMAGE=true
    echo "Captured rollback image (${CURRENT_IMAGE_ID})."
  fi
fi

if [[ "${NO_CACHE}" == "true" ]]; then
  docker compose -f "${COMPOSE_FILE}" build --pull --no-cache
else
  docker compose -f "${COMPOSE_FILE}" build --pull
fi

docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans

for attempt in $(seq 1 30); do
  if curl -fsS "${HEALTH_URL}" >/dev/null; then
    echo "Deploy healthy."
    exit 0
  fi
  sleep 2
done

echo "Health check failed after deploy. Showing logs:" >&2
docker compose -f "${COMPOSE_FILE}" logs --tail=150 "${SERVICE_NAME}" >&2

if [[ "${HAS_ROLLBACK_IMAGE}" == "true" ]]; then
  echo "Attempting rollback to previous image..." >&2
  docker image tag "${ROLLBACK_TAG}" "${TARGET_IMAGE}"
  docker compose -f "${COMPOSE_FILE}" up -d --no-build --remove-orphans "${SERVICE_NAME}"
  for attempt in $(seq 1 30); do
    if curl -fsS "${HEALTH_URL}" >/dev/null 2>&1; then
      echo "Rollback completed and service is healthy." >&2
      exit 1
    fi
    sleep 2
  done
  echo "Rollback attempted but health check is still failing." >&2
else
  echo "No previous running container found. Rollback unavailable." >&2
fi

exit 1

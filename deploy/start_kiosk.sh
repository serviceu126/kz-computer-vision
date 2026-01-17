#!/usr/bin/env bash
# Учительская подсказка: стартуем киоск строго из venv и рабочей директории проекта.
set -euo pipefail

PROJECT_ROOT="/opt/kz-computer-vision"
VENV_DIR="${PROJECT_ROOT}/.venv"

if [[ ! -d "${PROJECT_ROOT}" ]]; then
  echo "Не найдена папка проекта: ${PROJECT_ROOT}" >&2
  exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/uvicorn" ]]; then
  echo "Не найден uvicorn в venv: ${VENV_DIR}/bin/uvicorn" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

# Учительская подсказка: параметры можно менять через Environment в systemd.
HOST="${KZ_KIOSK_HOST:-0.0.0.0}"
PORT="${KZ_KIOSK_PORT:-8000}"
WORKERS="${KZ_KIOSK_WORKERS:-1}"

exec "${VENV_DIR}/bin/uvicorn" service.kiosk_api:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --workers "${WORKERS}"

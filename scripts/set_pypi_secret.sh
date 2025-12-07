#!/usr/bin/env bash
set -euo pipefail

# Prompt for the production PyPI token silently and set it as a GitHub Actions secret
# Usage: ./scripts/set_pypi_secret.sh

read -s -p "Paste production PyPI token: " PROD_PYPI_TOKEN
printf "\n"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found. Please install and authenticate gh before running this script."
  exit 2
fi

# Replace owner/repo if different
REPO_OWNER="GeoAziz"
REPO_NAME="quickshare-py"

gh secret set PYPI_TOKEN --repo "${REPO_OWNER}/${REPO_NAME}" --body "${PROD_PYPI_TOKEN}"

# Clear variable from this shell
unset PROD_PYPI_TOKEN

echo "Repository secret PYPI_TOKEN set for ${REPO_OWNER}/${REPO_NAME}."

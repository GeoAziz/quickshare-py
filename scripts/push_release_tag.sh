#!/usr/bin/env bash
set -euo pipefail

# Create and push an annotated release tag.
# Usage: ./scripts/push_release_tag.sh v1.0.1
# If no tag is provided, defaults to v1.0.1

TAG="${1:-v1.0.1}"

echo "Creating annotated tag ${TAG}..."

git tag -a "${TAG}" -m "Release ${TAG}"

echo "Pushing tag ${TAG} to origin..."

git push origin "${TAG}"

echo "Tag ${TAG} pushed. This should trigger the release workflow if configured."

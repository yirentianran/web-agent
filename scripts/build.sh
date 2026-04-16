#!/usr/bin/env bash
# Build frontend for production deployment.
# Compiles the React app and copies assets to the backend static directory.

set -e

cd "$(dirname "$0")/.."

echo "📦 Building frontend..."
(cd frontend && npm run build)
echo "✅ Build complete. Assets are in src/static/"

#!/usr/bin/env sh
# Runs Gardens of Kadesh with assets extracted by tools/extract-disc-assets.sh
HW_Data="$(cd "$(dirname "$0")" && pwd)/output/game" ./build/homeworld "$@"

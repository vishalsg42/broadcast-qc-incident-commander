#!/usr/bin/env bash
# Fetch the official Grafana MCP server, verifying its published checksum.
#
# Pinned rather than "latest": the agent's toolbox is part of the system's
# behaviour, so it has to be reproducible for anyone rebuilding this months
# later. The checksum is verified because a binary that runs with our Grafana
# credentials is not something to take on trust from a redirect.
set -euo pipefail

VERSION="${MCP_GRAFANA_VERSION:-1.3.0}"
DEST="${1:-bin}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  ASSET="mcp-grafana_Darwin_arm64.tar.gz" ;;
  Darwin-x86_64) ASSET="mcp-grafana_Darwin_x86_64.tar.gz" ;;
  Linux-aarch64) ASSET="mcp-grafana_Linux_arm64.tar.gz" ;;
  Linux-x86_64)  ASSET="mcp-grafana_Linux_x86_64.tar.gz" ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

BASE="https://github.com/grafana/mcp-grafana/releases/download/v${VERSION}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "fetching mcp-grafana ${VERSION} (${ASSET})"
curl -fsSL -o "$TMP/$ASSET"       "${BASE}/${ASSET}"
curl -fsSL -o "$TMP/checksums.txt" "${BASE}/mcp-grafana_${VERSION}_checksums.txt"

EXPECTED="$(grep " ${ASSET}\$" "$TMP/checksums.txt" | awk '{print $1}')"
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL="$(sha256sum "$TMP/$ASSET" | awk '{print $1}')"
else
  ACTUAL="$(shasum -a 256 "$TMP/$ASSET" | awk '{print $1}')"
fi
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "CHECKSUM MISMATCH for ${ASSET}" >&2
  echo "  expected: ${EXPECTED:-<not published>}" >&2
  echo "  actual:   ${ACTUAL}" >&2
  exit 1
fi
echo "  checksum verified"

mkdir -p "$DEST"
tar xzf "$TMP/$ASSET" -C "$TMP"
install -m 0755 "$TMP/mcp-grafana" "$DEST/mcp-grafana"
echo "  installed -> $DEST/mcp-grafana"
"$DEST/mcp-grafana" --help >/dev/null 2>&1 && echo "  binary runs"

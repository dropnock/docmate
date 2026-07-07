#!/bin/sh
# Generates a self-signed cert for local HTTPS dev, covering docmate.local
# and all its subdomains (www, auth, and per-customer subdomains). Browsers
# require the Secure attribute on Keycloak's SameSite=None session cookie to
# actually be served over HTTPS — plain HTTP on a non-localhost hostname
# silently drops that cookie and breaks login.
set -e
cd "$(dirname "$0")"

openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout dev.key -out dev.crt -days 3650 \
  -subj "/CN=docmate.local" \
  -addext "subjectAltName=DNS:docmate.local,DNS:*.docmate.local"

echo "Wrote nginx/certs/dev.crt and nginx/certs/dev.key"

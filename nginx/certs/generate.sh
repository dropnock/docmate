#!/bin/sh
# Generates a self-signed cert covering DOMAIN and all its subdomains (www,
# auth, and per-customer subdomains — a single wildcard SAN covers all of
# them). Browsers require the Secure attribute on Keycloak's SameSite=None
# session cookie to actually be served over HTTPS — plain HTTP on a
# non-localhost hostname silently drops that cookie and breaks login.
#
# Usage: ./generate.sh [domain]   (defaults to $CUSTOMER_DOMAIN, then docmate.local)
set -e
cd "$(dirname "$0")"

DOMAIN="${1:-${CUSTOMER_DOMAIN:-docmate.local}}"

openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout dev.key -out dev.crt -days 3650 \
  -subj "/CN=${DOMAIN}" \
  -addext "subjectAltName=DNS:${DOMAIN},DNS:*.${DOMAIN}"

echo "Wrote nginx/certs/dev.crt and nginx/certs/dev.key for ${DOMAIN} (and *.${DOMAIN})"

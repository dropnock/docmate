#!/bin/sh
# Generates a leaf cert (dev.crt/dev.key) covering DOMAIN and all its
# subdomains, signed by a locally-generated CA (ca.crt/ca.key) instead of
# being self-signed. Browsers require the Secure attribute on Keycloak's
# SameSite=None session cookie to actually be served over HTTPS — plain HTTP
# on a non-localhost hostname silently drops that cookie and breaks login.
#
# Why a CA instead of a plain self-signed cert: a self-signed leaf cert
# requires every client to click through a browser warning for EACH distinct
# hostname (www, auth, s3, per-customer subdomains all count separately, even
# though they share one wildcard cert) — and that exception doesn't cover
# background image/XHR requests at all, only top-level navigation, so tile
# viewers and presigned S3 URLs silently fail with no user-actionable prompt
# (net::ERR_CERT_AUTHORITY_INVALID). Installing ca.crt into each client
# machine's trust store instead makes every hostname signed by it trusted
# automatically, network-wide, with no per-origin click-through — the normal
# way to do this for an internal-only domain (a public CA like Let's Encrypt
# cannot issue for .local domains at all — see RFC 6762 — regardless of who
# controls DNS for it).
#
# The CA is generated once and reused on subsequent runs so re-running this
# script (e.g. because DOMAIN changed) never invalidates a CA already
# installed on client machines — only the leaf cert is reissued.
#
# Usage: ./generate.sh [domain]   (defaults to $CUSTOMER_DOMAIN, then docmate.local)
set -e
cd "$(dirname "$0")"

DOMAIN="${1:-${CUSTOMER_DOMAIN:-docmate.local}}"

if [ ! -f ca.key ] || [ ! -f ca.crt ]; then
  echo "No existing CA found — generating one (ca.key/ca.crt)."
  openssl req -x509 -nodes -newkey rsa:4096 \
    -keyout ca.key -out ca.crt -days 3650 \
    -subj "/CN=DocMate Internal Dev CA" \
    -addext "basicConstraints=critical,CA:true" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"
else
  echo "Reusing existing CA (ca.key/ca.crt) — install it on new client machines if you haven't already."
fi

openssl req -nodes -newkey rsa:2048 \
  -keyout dev.key -out dev.csr \
  -subj "/CN=${DOMAIN}"

printf "subjectAltName=DNS:%s,DNS:*.%s\nbasicConstraints=CA:false\nextendedKeyUsage=serverAuth\n" "${DOMAIN}" "${DOMAIN}" > dev.ext

openssl x509 -req -in dev.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out dev.crt -days 825 \
  -extfile dev.ext

rm -f dev.csr dev.ext

echo "Wrote nginx/certs/dev.crt and nginx/certs/dev.key for ${DOMAIN} (and *.${DOMAIN}), signed by ca.crt."
echo ""
echo "To make every hostname under this cert trusted (no browser warnings, no per-origin"
echo "click-through, and no more silently-failing background image/XHR requests), install"
echo "nginx/certs/ca.crt as a trusted root CA on every client machine — e.g. via Group Policy"
echo "for domain-joined Windows machines, an MDM profile for managed Macs, or manually via"
echo "each OS's certificate trust store. Do NOT distribute ca.key — only ca.crt."

#!/bin/sh
# cAdvisor's per-container filesystem metrics only see each container's own
# writable layer, not the actual data inside a mounted named volume (e.g.
# postgres_data reports ~68KB to cAdvisor while the real directory is tens of
# MB+) — a well-known cAdvisor limitation. This measures the volumes
# directly instead: each one is bind-mounted read-only into this container
# (see docker-compose.yml), and `du` gives its real size. Runs the `du` loop
# in the background and busybox's httpd in the foreground so both the metric
# refresh and the HTTP server that Prometheus scrapes stay alive together.
set -eu

apk add --no-cache busybox-extras >/dev/null 2>&1

mkdir -p /www

(
  while true; do
    {
      echo "# HELP docmate_volume_usage_bytes Size of a docmate docker volume's data (du -sb of the mount)."
      echo "# TYPE docmate_volume_usage_bytes gauge"
      for name in postgres_data minio_data prometheus_data loki_data grafana_data; do
        bytes=$(du -sb "/volumes/$name" 2>/dev/null | cut -f1)
        echo "docmate_volume_usage_bytes{volume=\"$name\"} ${bytes:-0}"
      done
    } > /www/metrics.tmp
    mv /www/metrics.tmp /www/metrics
    sleep 60
  done
) &

exec httpd -f -p 9101 -h /www

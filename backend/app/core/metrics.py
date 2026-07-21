"""Custom Prometheus counters.

HTTP-level metrics (request count/latency by route) come for free from
prometheus_fastapi_instrumentator — see main.py. These are for events that
aren't 1:1 with a single request/response, chosen because they map directly
to real incidents debugged earlier in this project (record-lock conflicts,
the silent stale-checker background job).
"""
from prometheus_client import Counter

lock_conflicts_total = Counter(
    "docmate_lock_conflicts_total",
    "Number of times a record lock acquisition was rejected because another user already held it",
)

stale_tasks_processed_total = Counter(
    "docmate_stale_tasks_processed_total",
    "Number of tasks flagged stale by the background stale-checker job",
)

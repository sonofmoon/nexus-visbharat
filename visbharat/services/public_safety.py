"""Small, process-local guards for unauthenticated public endpoints.

Cloud Run deployments should also enforce edge/API-gateway quotas. These guards
still provide a safe per-instance default and make abuse visible to callers.
"""
from collections import defaultdict, deque
from threading import Lock
from time import monotonic


_LOCK = Lock()
_BUCKETS = defaultdict(deque)


def allow(key, limit, window_seconds):
    now = monotonic()
    cutoff = now - float(window_seconds)
    with _LOCK:
        bucket = _BUCKETS[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= int(limit):
            retry_after = max(1, int(bucket[0] + float(window_seconds) - now) + 1)
            return False, retry_after
        bucket.append(now)
        # Bound memory if a process receives many one-off client addresses.
        if len(_BUCKETS) > 10000:
            for stale_key, stale_bucket in list(_BUCKETS.items())[:1000]:
                while stale_bucket and stale_bucket[0] <= cutoff:
                    stale_bucket.popleft()
                if not stale_bucket:
                    _BUCKETS.pop(stale_key, None)
    return True, 0

from __future__ import annotations

import argparse
import concurrent.futures
import statistics
import time
import urllib.request


def one(url: str, payload: bytes, method: str) -> float:
    started = time.perf_counter()
    req = urllib.request.Request(url, data=(payload if method == "POST" else None), method=method, headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    return time.perf_counter() - started


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000/healthz")
    p.add_argument("--method", choices=["GET", "POST"], default="GET")
    p.add_argument("--n", type=int, default=10)
    a = p.parse_args()
    payload = b"load-test"
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.n) as ex:
        values = list(ex.map(lambda _: one(a.url, payload, a.method), range(a.n)))
    values.sort()
    p50 = statistics.median(values)
    p95 = values[min(len(values) - 1, int(len(values) * .95))]
    print(f"N={a.n} p50={p50:.4f}s p95={p95:.4f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

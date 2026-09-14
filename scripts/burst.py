"""Fire N concurrent requests at a URL and tally HTTP status codes.

Demos API Gateway throttling (M6): a concurrent burst exceeds the stage's
burst/rate limit and the overflow comes back as 429. Sequential requests are
network-bound (~2-3/s) and won't trip a sane limit — throttling shows under
concurrency, which is the point.

Usage: python scripts/burst.py https://<api>/<code> --n 60 --workers 30
"""

from __future__ import annotations

import argparse
import collections
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # don't follow the 302 — we only care about the status code


def _hit(url: str) -> int:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        return opener.open(url, timeout=10).status
    except urllib.error.HTTPError as err:
        return err.code  # 302 / 404 / 429 all arrive here
    except Exception:
        return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--n", type=int, default=60, help="total requests")
    ap.add_argument("--workers", type=int, default=30, help="concurrent workers")
    args = ap.parse_args()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        codes = list(pool.map(lambda _: _hit(args.url), range(args.n)))

    print(f"{args.n} requests, {args.workers} concurrent:")
    for code, count in sorted(collections.Counter(codes).items()):
        print(f"  HTTP {code}: {count}")


if __name__ == "__main__":
    main()

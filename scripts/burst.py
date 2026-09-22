"""Fire N concurrent POSTs at the ingest endpoint and tally HTTP status codes.

Demos the per-user rate limit (M6, ADR-0006): each request costs one token from
the user's hourly counter, and the overflow comes back as 429. Concurrency is the
interesting part — DynamoDB's atomic ADD is what makes the count correct when
requests land at the same instant, so `202 + 429 == n` exactly, with no double
spends and no lost ones.

Every request carries a distinct idempotency key: reusing one would collapse to a
single notification (ADR-0001) but still spend quota, which muddies the tally.

Usage: python scripts/burst.py https://<api>/notifications --user u1 --n 30
"""

from __future__ import annotations

import argparse
import collections
import json
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor


def _hit(url: str, user_id: str) -> str:
    body = json.dumps(
        {
            "user_id": user_id,
            "type": "welcome",
            "payload": {"name": "Burst"},
            "idempotency_key": uuid.uuid4().hex,
        }
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}, method="POST"
    )
    try:
        return str(urllib.request.urlopen(req, timeout=10).status)
    except urllib.error.HTTPError as err:
        return str(err.code)  # 429 arrives here
    except Exception as err:
        # Named, not swallowed into a bare 0: a tally of zeros tells you nothing,
        # and the usual cause is local (no CA bundle -> SSLCertVerificationError),
        # which looks nothing like a rate limit but counts the same.
        return f"ERR {type(err).__name__}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="the POST /notifications endpoint")
    ap.add_argument("--user", default="burst-user", help="user_id to charge")
    ap.add_argument("--n", type=int, default=30, help="total requests")
    ap.add_argument("--workers", type=int, default=15, help="concurrent workers")
    args = ap.parse_args()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        codes = list(pool.map(lambda _: _hit(args.url, args.user), range(args.n)))

    print(f"{args.n} requests as {args.user}, {args.workers} concurrent:")
    for code, count in sorted(collections.Counter(codes).items()):
        print(f"  {code}: {count}")


if __name__ == "__main__":
    main()

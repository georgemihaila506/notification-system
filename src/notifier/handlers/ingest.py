"""POST /notifications — accept a notification request.

M0: a trivial 202 stub that proves the toolchain (Terraform -> API Gateway -> Lambda)
before any real logic. The real ingest — validate, derive notification_id, load prefs,
publish to SNS — lands in M2.
"""

from __future__ import annotations

import json


def handler(event: dict, context: object) -> dict:
    return {
        "statusCode": 202,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"status": "accepted", "note": "M0 stub — real ingest in M2"}),
    }

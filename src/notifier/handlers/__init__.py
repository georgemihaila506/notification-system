"""Lambda entrypoints: `ingest` (API Gateway) and the per-channel SQS `worker`s.

Thin by design — parse the event, delegate to models/db/channels, shape the response.
"""

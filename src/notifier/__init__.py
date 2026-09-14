"""notification-system — a learning notification pipeline on AWS.

Package layout:
  models.py     — Notification, notification_id derivation, TransientError / PermanentError
  db.py         — DynamoDB: prefs + deliveries tables (conditional put = dedup, atomic ADD)
  templates.py  — type -> (subject, body)
  channels/     — email (SES, real); sms + push (simulated, with fault-injection hints)
  handlers/     — ingest (API) and the per-channel SQS workers

Runtime deps are stdlib + boto3 only, so the deploy artifact is a plain zip. See docs/adr/.
"""

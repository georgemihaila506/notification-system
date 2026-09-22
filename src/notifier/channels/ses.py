"""Real email delivery via SES (M4/M5, ADR-0004 and ADR-0009).

The only channel that talks to a real provider. Everything the pipeline does
around it — dedup, opt-out, retry, DLQ — is unchanged; only the final send is
real. What this module adds is *classification*: turning SES's error codes into
the TransientError / PermanentError distinction ADR-0003 is built on.

Sends also carry a configuration set and correlation tags, which is what makes
the inbound half possible: SES publishes delivery, bounce and complaint events
identified by ITS message id, and the tags are how those events find their way
back to our ledger row.

SES stays in its sandbox (200/day, 1/sec, verified recipients only), so the
throttling and rejection paths below are not hypothetical.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ..models import Notification, PermanentError, TransientError
from ..templates import render

logger = logging.getLogger(__name__)

# SES error codes worth another attempt. Note that "transient" here does not mean
# "will succeed within the retry window" — it means "do not throw this away". The
# DLQ is the parking lot for anything needing longer than 3 x 60s, which is why the
# two slow ones below belong here: both recover on their own (a quota resets, a
# sending pause gets lifted), and a DLQ message can be redriven a day later. Calling
# them permanent would delete recoverable mail for a condition that fixes itself.
TRANSIENT_CODES: frozenset[str] = frozenset(
    {
        "Throttling",  # sandbox rate is 1/sec — genuinely likely
        "ThrottlingException",  # same thing, other spelling, depending on the path
        "ServiceUnavailable",
        "InternalFailure",
        "RequestTimeout",
        "LimitExceededException",  # 200/day sandbox quota — resets tomorrow
        "AccountSendingPausedException",  # reputation pause — lifted once resolved
    }
)

# SES error codes that will never succeed with the same message. Sending again
# produces the identical refusal, so a retry only burns quota and delays the DLQ.
PERMANENT_CODES: frozenset[str] = frozenset(
    {
        "MessageRejected",  # unverified recipient, malformed address, blocked content
        "MailFromDomainNotVerifiedException",
        "ConfigurationSetDoesNotExistException",
        "InvalidParameterValue",
    }
)


def ses_sender(
    channel: str, source: str, config_set: str, *, client=None
) -> Callable[[Notification, str], None]:
    """Build the email sender. Returns a `(Notification, address) -> None`.

    `source` is the verified From identity (Terraform passes var.notify_email).
    `config_set` names the SES configuration set whose event destination publishes
    delivery outcomes (ADR-0009); without it SES reports nothing after accepting.
    `channel` exists only to be tagged onto the send: an event carries back whatever
    tags went out, and the ledger key is `notification_id#channel`, so both halves
    have to travel with the message. SES serves only email today, but hardcoding
    that in the event worker would bury the assumption somewhere harder to find.
    `client` is injectable for tests, matching db.Store's `dynamodb=` pattern.
    Worker modules build SENDERS at import time, so the boto3 client below is
    created once per container and reused across invocations.
    """
    ses = client or boto3.client("ses")

    def send(notification: Notification, address: str) -> None:
        """Send one email, or raise the error class that decides its fate.

        Rendering happens first, so an unknown template costs no API call and no
        quota — it is a PermanentError out of render(), same as for any channel.

        A failed send is classified, never re-raised as-is: `deliver()` only
        understands TransientError (keep the SQS message, retry) and
        PermanentError (record `failed`, delete). An unmapped SES code is logged
        and treated as transient — it will exhaust its retries into the DLQ where
        the alarm surfaces it, which is recoverable; guessing permanent would
        delete the notification and tell nobody. A BotoCoreError never reached
        SES at all (connection, timeout — there is no error code to read), and a
        network blip is the definition of retryable.
        """
        subject, body = render(notification.type, notification.payload)
        try:
            resp = ses.send_email(
                Source=source,
                Destination={"ToAddresses": [address]},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {"Text": {"Data": body}},
                },
                ConfigurationSetName=config_set,
                Tags=[
                    {"Name": "notification_id", "Value": notification.notification_id},
                    {"Name": "channel", "Value": channel},
                ],
            )
        except ClientError as err:
            code = err.response["Error"]["Code"]
            if code in PERMANENT_CODES:
                raise PermanentError(f"SES permanent failure: {code}") from err
            if code in TRANSIENT_CODES:
                raise TransientError(f"SES transient failure: {code}") from err
            logger.warning("SES unknown error code: %s", code)
            raise TransientError(f"SES unknown failure: {code}") from err
        except BotoCoreError as err:
            raise TransientError("SES connection/timeout failure") from err
        # user_id, not the address: CloudWatch Logs has its own retention and
        # access rules, and a recipient address is PII. The MessageId is SES's
        # handle for tracing the delivery, and the address is in the prefs table.
        logger.info("SES sent %s for user %s", resp["MessageId"], notification.user_id)

    return send

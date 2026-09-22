"""channels.ses — the real provider integration and its failure classification
(M4, ADR-0003/0004). RED until `ses_sender` is written.

Two styles here on purpose: moto for the real API shape (a genuine send, a
genuine sandbox rejection), and a fake client for error codes moto will not
produce on demand.
"""

from __future__ import annotations

import re

import boto3
import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from moto import mock_aws

from notifier.channels.ses import ses_sender
from notifier.models import Notification, PermanentError, TransientError

SOURCE = "notify@example.com"
CONFIG_SET = "notify-events"
TO = "george@example.com"


def _n(payload: dict | None = None, type_: str = "welcome") -> Notification:
    return Notification(
        user_id="u1", type=type_, payload=payload or {"name": "G"}, idempotency_key="k1"
    )


class _FakeSes:
    """Records the call, or fails it with a given SES error code."""

    def __init__(self, error_code: str | None = None, raises: Exception | None = None):
        self.error_code = error_code
        self.raises = raises
        self.kwargs: dict | None = None

    def send_email(self, **kwargs):
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        if self.error_code:
            raise ClientError(
                {"Error": {"Code": self.error_code, "Message": "simulated"}},
                "SendEmail",
            )
        return {"MessageId": "0100018f-fake"}


@pytest.fixture
def ses():
    with mock_aws():
        client = boto3.client("ses", region_name="eu-north-1")
        client.verify_email_identity(EmailAddress=SOURCE)
        client.verify_email_identity(EmailAddress=TO)
        client.create_configuration_set(ConfigurationSet={"Name": CONFIG_SET})
        yield client


# --- the real API shape ------------------------------------------------------


def test_a_verified_send_succeeds(ses):
    ses_sender("email", SOURCE, CONFIG_SET, client=ses)(_n(), TO)
    assert ses.get_send_quota()["SentLast24Hours"] == 1


def test_unverified_source_is_permanent(ses):
    """SES sandbox refuses an unverified From. Retrying sends the identical
    message to the identical refusal, so it must never be retried."""
    with pytest.raises(PermanentError):
        ses_sender("email", "nobody@example.com", CONFIG_SET, client=ses)(_n(), TO)


# --- what is actually sent ---------------------------------------------------


def test_source_destination_and_rendered_template_are_passed():
    fake = _FakeSes()
    ses_sender("email", SOURCE, CONFIG_SET, client=fake)(_n({"name": "George"}), TO)
    assert fake.kwargs["Source"] == SOURCE
    assert fake.kwargs["Destination"]["ToAddresses"] == [TO]
    assert fake.kwargs["Message"]["Subject"]["Data"] == "Welcome, George!"
    assert fake.kwargs["Message"]["Body"]["Text"]["Data"] == (
        "Hi George, thanks for joining."
    )


def test_send_carries_the_configuration_set_and_correlation_tags():
    """ADR-0009: SES events identify a message by ITS id, not ours. Tags ride out
    with the send and come back on every event under mail.tags, so a bounce
    arrives already carrying the ledger key -- no GSI, no lookup table."""
    fake = _FakeSes()
    n = _n()
    ses_sender("email", SOURCE, CONFIG_SET, client=fake)(n, TO)
    assert fake.kwargs["ConfigurationSetName"] == CONFIG_SET
    tags = {t["Name"]: t["Value"] for t in fake.kwargs["Tags"]}
    assert tags == {"notification_id": n.notification_id, "channel": "email"}


def test_notification_id_is_a_legal_ses_tag_value():
    """SES tag values allow only alphanumerics, - and _, max 256 chars. A sha256
    hex id fits. Pinned here so a future id scheme (a uuid with colons, base64
    with +/=) fails loudly rather than silently breaking correlation."""
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", _n().notification_id)


def test_unknown_template_fails_before_any_api_call():
    fake = _FakeSes()
    with pytest.raises(PermanentError):
        ses_sender("email", SOURCE, CONFIG_SET, client=fake)(_n(type_="no_such_type"), TO)
    assert fake.kwargs is None  # never reached the provider


# --- classification ----------------------------------------------------------


@pytest.mark.parametrize("code", ["Throttling", "ServiceUnavailable", "InternalFailure"])
def test_transient_codes_raise_transient(code):
    with pytest.raises(TransientError):
        ses_sender("email", SOURCE, CONFIG_SET, client=_FakeSes(error_code=code))(_n(), TO)


@pytest.mark.parametrize(
    "code", ["MessageRejected", "MailFromDomainNotVerifiedException"]
)
def test_permanent_codes_raise_permanent(code):
    with pytest.raises(PermanentError):
        ses_sender("email", SOURCE, CONFIG_SET, client=_FakeSes(error_code=code))(_n(), TO)


def test_unmapped_code_defaults_to_transient():
    """An error code nobody has classified must NOT silently drop the mail.
    Transient costs three wasted calls and puts it in the DLQ where the alarm
    finds it; permanent loses the notification and tells no one."""
    with pytest.raises(TransientError):
        ses_sender("email", SOURCE, CONFIG_SET, client=_FakeSes(error_code="SomeFutureAwsError"))(_n(), TO)


def test_connection_failure_is_transient():
    """No HTTP response means no error code at all -- a BotoCoreError subclass.
    A network blip is the definition of retryable."""
    boom = ReadTimeoutError(endpoint_url="https://email.eu-north-1.amazonaws.com")
    with pytest.raises(TransientError):
        ses_sender("email", SOURCE, CONFIG_SET, client=_FakeSes(raises=boom))(_n(), TO)

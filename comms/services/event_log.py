import logging
from typing import Any

from django.db import connection, transaction

from comms.models import Employee, Message, Organization, SystemEvent

logger = logging.getLogger(__name__)


def _write_event(
    event_type: str,
    *,
    organization: Organization | None = None,
    actor: Employee | None = None,
    receiver: Employee | None = None,
    message: Message | None = None,
    source: str = "app",
    status: str = "success",
    payload: dict[str, Any] | None = None,
    error_message: str = "",
) -> SystemEvent | None:
    if organization is None and message is not None:
        organization = message.organization
    if actor is None and message is not None:
        actor = message.sender
    if receiver is None and message is not None:
        receiver = message.receiver

    return SystemEvent.objects.create(
        organization=organization,
        actor=actor,
        receiver=receiver,
        message=message,
        event_type=event_type,
        source=source,
        status=status,
        payload=payload or {},
        error_message=error_message,
    )


def log_event(
    event_type: str,
    *,
    organization: Organization | None = None,
    actor: Employee | None = None,
    receiver: Employee | None = None,
    message: Message | None = None,
    source: str = "app",
    status: str = "success",
    payload: dict[str, Any] | None = None,
    error_message: str = "",
) -> SystemEvent | None:
    try:
        if connection.in_atomic_block:
            transaction.on_commit(
                lambda: log_event(
                    event_type,
                    organization=organization,
                    actor=actor,
                    receiver=receiver,
                    message=message,
                    source=source,
                    status=status,
                    payload=payload,
                    error_message=error_message,
                ),
                robust=True,
            )
            return None

        return _write_event(
            event_type,
            organization=organization,
            actor=actor,
            receiver=receiver,
            message=message,
            source=source,
            status=status,
            payload=payload,
            error_message=error_message,
        )
    except Exception:
        logger.exception("Failed to write system event: %s", event_type)
        return None

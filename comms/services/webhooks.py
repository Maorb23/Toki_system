import hashlib
import hmac
import json
import logging

import requests

from comms.models import SystemEvent, WebhookDelivery, WebhookSubscription

logger = logging.getLogger(__name__)


def sign_webhook_payload(payload: dict, secret: str) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _event_payload(event: SystemEvent) -> dict:
    return {
        "event_id": event.id,
        "event_type": event.event_type,
        "organization_id": event.organization_id,
        "source": event.source,
        "status": event.status,
        "created_at": event.created_at.isoformat(),
        "payload": event.payload or {},
    }


def deliver_event_to_webhooks(event: SystemEvent) -> list[WebhookDelivery]:
    deliveries = []
    try:
        if not event.organization_id:
            return deliveries

        subscriptions = WebhookSubscription.objects.filter(
            organization=event.organization,
            is_active=True,
        ).order_by("id")
        payload = _event_payload(event)

        for subscription in subscriptions:
            event_types = subscription.event_types or []
            if event.event_type not in event_types:
                continue

            signature = sign_webhook_payload(payload, subscription.secret)
            try:
                response = requests.post(
                    subscription.target_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-ReceiverAware-Event": event.event_type,
                        "X-ReceiverAware-Signature": signature,
                    },
                    timeout=3,
                )
                status = WebhookDelivery.Status.SUCCESS if 200 <= response.status_code < 300 else WebhookDelivery.Status.FAILED
                delivery = WebhookDelivery.objects.create(
                    subscription=subscription,
                    event=event,
                    status=status,
                    request_payload=payload,
                    response_status_code=response.status_code,
                    response_body=response.text[:2000],
                    error_message="" if status == WebhookDelivery.Status.SUCCESS else f"HTTP {response.status_code}",
                )
            except Exception as exc:
                logger.warning("Webhook delivery failed for subscription %s and event %s: %s", subscription.id, event.id, exc)
                delivery = WebhookDelivery.objects.create(
                    subscription=subscription,
                    event=event,
                    status=WebhookDelivery.Status.FAILED,
                    request_payload=payload,
                    error_message=str(exc),
                )
            deliveries.append(delivery)
    except Exception:
        logger.exception("Webhook delivery orchestration failed for event %s", getattr(event, "id", None))
    return deliveries


def delivery_summary(deliveries: list[WebhookDelivery]) -> str:
    success = sum(1 for delivery in deliveries if delivery.status == WebhookDelivery.Status.SUCCESS)
    failed = sum(1 for delivery in deliveries if delivery.status == WebhookDelivery.Status.FAILED)
    skipped = sum(1 for delivery in deliveries if delivery.status == WebhookDelivery.Status.SKIPPED)
    return f"webhooks delivered: {success}, failed: {failed}, skipped: {skipped}"

import json
import os
from urllib.parse import urlparse

from flask import current_app
from . import db
from .models import PushSubscription


def push_configured():
    return bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY") and os.getenv("VAPID_SUBJECT"))


def send_push(user_id, title, body, url="/notifications"):
    if not push_configured():
        return
    from pywebpush import webpush, WebPushException
    private_key = os.environ["VAPID_PRIVATE_KEY"].replace("\\n", "\n")
    subject = os.environ["VAPID_SUBJECT"]
    payload = json.dumps({"title": title, "body": body[:200], "url": url}, ensure_ascii=False)
    for subscription in PushSubscription.query.filter_by(user_id=user_id).all():
        try:
            webpush(
                subscription_info={"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
                timeout=4,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                db.session.delete(subscription)
                db.session.commit()
            else:
                current_app.logger.warning("No se pudo enviar aviso push: HTTP %s", status)
        except Exception:
            current_app.logger.exception("Error al enviar aviso push")


def valid_subscription(data):
    if not isinstance(data, dict):
        return False
    endpoint = data.get("endpoint")
    keys = data.get("keys")
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or not isinstance(keys, dict):
        return False
    parsed = urlparse(endpoint)
    allowed = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com", "wns.windows.com", "push.services.mozilla.com")
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
        return False
    return all(isinstance(keys.get(k), str) and 20 <= len(keys[k]) <= 512 for k in ("p256dh", "auth"))

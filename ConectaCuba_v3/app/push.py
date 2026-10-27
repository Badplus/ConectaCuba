import json
import os
import tempfile
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
    private_key = os.environ["VAPID_PRIVATE_KEY"].strip().replace("\\n", "\n")
    subject = os.environ["VAPID_SUBJECT"]
    payload = json.dumps({"title": title, "body": body[:200], "url": url}, ensure_ascii=False)
    for subscription in PushSubscription.query.filter_by(user_id=user_id).all():
        try:
            # pywebpush expects a VAPID key file path (or its encoded key format),
            # not the literal PEM content stored in Render's environment variable.
            with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", encoding="utf-8") as key_file:
                key_file.write(private_key)
                key_file.flush()
                webpush(
                subscription_info={"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
                data=payload,
                vapid_private_key=key_file.name,
                vapid_claims={"sub": subject},
                timeout=4,
                )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                db.session.delete(subscription)
                db.session.commit()
            else:
                current_app.logger.warning("PUSH_DIAG: error de servicio push; HTTP=%s; tipo=%s", status, type(exc).__name__)
        except Exception as exc:
            # Diagnose key validity without logging any private or public key material.
            key_valid = False
            public_matches = False
            try:
                import base64
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric import ec
                loaded = serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
                key_valid = isinstance(loaded, ec.EllipticCurvePrivateKey) and isinstance(loaded.curve, ec.SECP256R1)
                if key_valid:
                    expected = loaded.public_key().public_bytes(
                        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                    )
                    configured = os.environ["VAPID_PUBLIC_KEY"].strip()
                    configured_bytes = base64.urlsafe_b64decode(configured + "=" * (-len(configured) % 4))
                    public_matches = expected == configured_bytes
            except Exception:
                pass
            current_app.logger.error(
                "PUSH_DIAG: tipo=%s; private_key_valid=%s; public_key_matches=%s; subject_mailto=%s",
                type(exc).__name__, key_valid, public_matches, subject.startswith("mailto:")
            )


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

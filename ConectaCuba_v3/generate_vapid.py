"""Run locally: python generate_vapid.py. Never commit the generated private key."""
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

private = ec.generate_private_key(ec.SECP256R1())
pem = private.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
public = private.public_key().public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)
print("VAPID_PUBLIC_KEY=" + base64.urlsafe_b64encode(public).rstrip(b"=").decode())
print("VAPID_PRIVATE_KEY=" + pem.replace("\n", "\\n"))
print("VAPID_SUBJECT=mailto:tu-correo-de-contacto@ejemplo.com (reemplazar con un correo real)")

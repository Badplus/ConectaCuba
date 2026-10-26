"""Pruebas aisladas: SQLite temporal, sin acceso a Supabase."""
import os
import tempfile
import unittest
from pathlib import Path

from app import create_app, db
from app.models import User, Listing


class MarketplaceSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(self.tmp.name) / "test.db")
        os.environ["SECRET_KEY"] = "testing-only-secret"
        os.environ["ADMIN_PASSWORD"] = "testing-only-admin-password"
        os.environ.pop("TURNSTILE_SITE_KEY", None)
        os.environ.pop("TURNSTILE_SECRET_KEY", None)
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.tmp.cleanup()

    def csrf(self):
        self.client.get("/register")
        with self.client.session_transaction() as session:
            return session["_csrf_token"]

    def register(self, phone, role, municipality="Holguín"):
        return self.client.post("/register", data={
            "_csrf": self.csrf(), "phone": phone, "full_name": "Prueba",
            "password": "PruebaSegura123!", "role": role,
            "municipality": municipality, "address": "Dirección de prueba"
        }, follow_redirects=False)

    def test_public_pages(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/register").status_code, 200)
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_csrf_rejects_missing_token(self):
        self.assertEqual(self.client.post("/register", data={}).status_code, 400)

    def test_buyer_autoapproved_cannot_publish(self):
        self.assertEqual(self.register("55500001", "comprador").status_code, 302)
        with self.app.app_context():
            user = User.query.filter_by(phone="55500001").one()
            self.assertTrue(user.approved)
            self.assertEqual(user.province, "Holguín")
        self.assertEqual(self.client.get("/publish").status_code, 403)

    def test_seller_pending_and_approved_publish(self):
        self.assertEqual(self.register("55500002", "vendedor").status_code, 302)
        with self.app.app_context():
            user = User.query.filter_by(phone="55500002").one()
            self.assertFalse(user.approved)
            user.approved = True
            db.session.commit()
        response = self.client.post("/publish", data={
            "_csrf": self.csrf(), "kind": "oferta", "product": "Arroz",
            "quantity": "10", "price": "150", "unit": "kg",
            "municipality": "Holguín", "coverage": "municipio",
            "description": "Producto de prueba"
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            listing = Listing.query.one()
            self.assertEqual(listing.kind, "oferta")
            self.assertEqual(listing.province, "Holguín")

    def test_logout_requires_post(self):
        self.register("55500003", "comprador")
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.post("/logout", data={"_csrf": self.csrf()}).status_code, 302)

    def test_chat_requires_authentication(self):
        self.assertEqual(self.client.get("/chat/123").status_code, 302)


if __name__ == "__main__":
    unittest.main()

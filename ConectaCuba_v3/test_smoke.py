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

    def test_admin_approves_seller(self):
        self.register("55500004", "vendedor")
        self.client.post("/logout", data={"_csrf": self.csrf()})
        response = self.client.post("/admin/login", data={
            "_csrf": self.csrf(), "password": "testing-only-admin-password"
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            user_id = User.query.filter_by(phone="55500004").one().id
        response = self.client.post(f"/admin/user/{user_id}/toggle", data={
            "_csrf": self.csrf()
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertTrue(db.session.get(User, user_id).approved)

    def test_admin_rejects_missing_csrf(self):
        self.assertEqual(self.client.post("/admin/login", data={
            "password": "testing-only-admin-password"
        }).status_code, 400)

    def test_chat_message_and_notification(self):
        self.register("55500005", "comprador")
        with self.app.app_context():
            first_id = User.query.filter_by(phone="55500005").one().id
        self.client.post("/logout", data={"_csrf": self.csrf()})
        self.register("55500006", "comprador")
        with self.app.app_context():
            second_id = User.query.filter_by(phone="55500006").one().id
        response = self.client.post(f"/chat/{first_id}", data={
            "_csrf": self.csrf(), "body": "Hola, prueba de mensaje"
        })
        self.assertEqual(response.status_code, 302)
        from app.models import Message, Notification
        with self.app.app_context():
            msg = Message.query.filter_by(sender_id=second_id, receiver_id=first_id).one()
            self.assertEqual(msg.body, "Hola, prueba de mensaje")
            self.assertIsNotNone(Notification.query.filter_by(user_id=first_id).first())

    def test_chat_rejects_long_message(self):
        self.register("55500007", "comprador")
        with self.app.app_context():
            first_id = User.query.filter_by(phone="55500007").one().id
        self.client.post("/logout", data={"_csrf": self.csrf()})
        self.register("55500008", "comprador")
        response = self.client.post(f"/chat/{first_id}", data={
            "_csrf": self.csrf(), "body": "x" * 5001
        })
        self.assertEqual(response.status_code, 400)

    def test_turnstile_rejects_missing_response_when_enabled(self):
        self.app.config["TURNSTILE_SITE_KEY"] = "test-site-key"
        self.app.config["TURNSTILE_SECRET_KEY"] = "test-secret-key"
        response = self.client.post("/login", data={
            "_csrf": self.csrf(), "phone": "55500009", "password": "anything"
        })
        self.assertEqual(response.status_code, 400)

    def test_admin_cannot_toggle_protected_admin(self):
        self.register("55500010", "comprador")
        self.client.post("/logout", data={"_csrf": self.csrf()})
        with self.app.app_context():
            user = User.query.filter_by(phone="55500010").one()
            user.is_admin = True
            user_id = user.id
            db.session.commit()
        self.client.post("/admin/login", data={
            "_csrf": self.csrf(), "password": "testing-only-admin-password"
        })
        response = self.client.post(f"/admin/user/{user_id}/toggle", data={
            "_csrf": self.csrf()
        })
        self.assertEqual(response.status_code, 403)

    def test_commission_rejects_nan(self):
        self.client.post("/admin/login", data={
            "_csrf": self.csrf(), "password": "testing-only-admin-password"
        })
        response = self.client.post("/admin/commission", data={
            "_csrf": self.csrf(), "commission": "nan"
        })
        self.assertEqual(response.status_code, 400)

    def test_chat_requires_authentication(self):
        self.assertEqual(self.client.get("/chat/123").status_code, 302)


if __name__ == "__main__":
    unittest.main()

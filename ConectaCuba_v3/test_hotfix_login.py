"""Regression checks for production login hotfix; never contacts Supabase."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ["SECRET_KEY"] = "hotfix-test-only"
os.environ["ADMIN_PASSWORD"] = "hotfix-test-admin"
os.environ.pop("TURNSTILE_SITE_KEY", None)
os.environ.pop("TURNSTILE_SECRET_KEY", None)

from app import create_app, db


class LoginHotfixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(self.temp.name) / "test.db")
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def test_login_page_renders(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="_csrf"', response.data)

    def test_admin_login_page_renders(self):
        response = self.client.get("/admin/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="_csrf"', response.data)

    def test_other_post_forms_render(self):
        self.assertEqual(self.client.get("/register").status_code, 200)

    def test_post_without_csrf_is_rejected(self):
        self.assertEqual(self.client.post("/login", data={"phone": "1", "password": "bad"}).status_code, 400)

    def test_post_with_csrf_reaches_login_handler(self):
        self.client.get("/login")
        with self.client.session_transaction() as session:
            token = session["_csrf_token"]
        response = self.client.post("/login", data={
            "_csrf": token, "phone": "notregistered", "password": "bad"
        })
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()

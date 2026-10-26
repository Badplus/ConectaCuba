import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import create_app, db
from app.models import User

class IntegrationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  os.environ['DATABASE_URL']='sqlite:///'+str(Path(self.tmp.name)/'test.db')
  os.environ['SECRET_KEY']='integration-tests-only'
  os.environ['ADMIN_PASSWORD']='test-admin-secret'
  os.environ['TURNSTILE_SITE_KEY']='test-site'
  os.environ['TURNSTILE_SECRET_KEY']='test-secret'
  self.app=create_app()
  self.app.config['TESTING']=True
  self.client=self.app.test_client()
  with self.app.app_context():
   user=User(full_name='Admin',phone='12345678',role='comprador',municipality='Santiago de Cuba',address='test',approved=True,is_admin=True)
   user.set_password('password-test-1234')
   db.session.add(user);db.session.commit()
 def tearDown(self):
  with self.app.app_context():
   db.session.remove();db.engine.dispose()
  self.tmp.cleanup()
 def test_admin_login_and_logout(self):
  self.client.get('/login')
  with self.client.session_transaction() as session: csrf=session['_csrf_token']
  with patch('app.routes.turnstile_valid',return_value=True):
   response=self.client.post('/login',data={'_csrf':csrf,'phone':'12345678','password':'password-test-1234'})
  self.assertEqual(response.status_code,302)
  self.assertIn('/admin',response.headers['Location'])
  self.assertEqual(self.client.get('/admin').status_code,200)
  self.client.get('/logout')
  self.assertEqual(self.client.get('/admin').status_code,302)
 def test_turnstile_missing_token_rejected(self):
  self.client.get('/login')
  with self.client.session_transaction() as session: csrf=session['_csrf_token']
  response=self.client.post('/login',data={'_csrf':csrf,'phone':'12345678','password':'password-test-1234'})
  self.assertEqual(response.status_code,400)
 def test_widget_present(self):
  for path in ('/login','/register','/admin/login'):
   response=self.client.get(path)
   self.assertEqual(response.status_code,200)
   self.assertIn(b'cf-turnstile',response.data)
if __name__=='__main__': unittest.main()

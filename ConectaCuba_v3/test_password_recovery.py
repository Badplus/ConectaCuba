import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from app import create_app, db
from app.models import User, PasswordResetRequest

class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  os.environ['DATABASE_URL']='sqlite:///'+str(Path(self.tmp.name)/'test.db')
  os.environ['SECRET_KEY']='recovery-tests-only'
  os.environ['ADMIN_PASSWORD']='recovery-admin-tests-only'
  self.app=create_app()
  self.app.config['TESTING']=True
  self.turnstile_patch=patch('app.routes.turnstile_valid',return_value=True)
  self.turnstile_patch.start()
  self.client=self.app.test_client()
  with self.app.app_context():
   u=User(full_name='Test',phone='12345678',role='comprador',municipality='Santiago de Cuba',address='test',approved=True)
   u.set_password('old-password-1234')
   db.session.add(u);db.session.commit()
 def tearDown(self):
  self.turnstile_patch.stop()
  with self.app.app_context():
   db.session.remove();db.engine.dispose()
  self.tmp.cleanup()
 def csrf(self):
  self.client.get('/forgot-password')
  with self.client.session_transaction() as s: return s['_csrf_token']
 def test_request_and_single_use(self):
  token=self.csrf()
  self.assertEqual(self.client.post('/forgot-password',data={'_csrf':token,'phone':'12345678'}).status_code,302)
  with self.app.app_context():
   record=PasswordResetRequest.query.first()
   self.assertIsNotNone(record)
   reset_id=record.id
  self.client.get('/admin/login')
  self.client.post('/admin/login',data={'_csrf':token,'password':'recovery-admin-tests-only'})
  response=self.client.post('/admin/password-resets/'+str(reset_id)+'/approve',data={'_csrf':token})
  self.assertEqual(response.status_code,200)
  import re,html
  link=html.unescape(re.search(r'https?://[^<\s]+',response.get_data(as_text=True)).group(0))
  from urllib.parse import urlparse
  path=urlparse(link).path
  self.assertEqual(self.client.get(path).status_code,200)
  self.assertEqual(self.client.post(path,data={'_csrf':token,'new_password':'new-password-1234'}).status_code,302)
  self.assertEqual(self.client.get(path).status_code,404)
  with self.app.app_context():
   self.assertTrue(User.query.filter_by(phone='12345678').first().check_password('new-password-1234'))
 def test_unauthorized_admin_action(self):
  self.assertIn(self.client.post('/admin/password-resets/1/approve',data={'_csrf':self.csrf()}).status_code,(302,403))
if __name__=='__main__':unittest.main()

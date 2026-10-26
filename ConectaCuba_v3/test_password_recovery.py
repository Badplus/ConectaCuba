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
  import re
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
  pin=re.search(r'letter-spacing:.3em">(\\d{5})</p>',response.get_data(as_text=True)).group(1)
  self.assertEqual(self.client.post('/verify-recovery-pin',data={'_csrf':token,'phone':'12345678','pin':pin}).status_code,302)
  self.assertEqual(self.client.get('/new-password').status_code,200)
  self.assertEqual(self.client.post('/new-password',data={'_csrf':token,'new_password':'new-password-1234','confirm_password':'different-password'}).status_code,200)
  self.assertEqual(self.client.post('/new-password',data={'_csrf':token,'new_password':'new-password-1234','confirm_password':'new-password-1234'}).status_code,302)
  self.assertNotEqual(self.client.get('/new-password').status_code,200)
  with self.app.app_context():
   self.assertTrue(User.query.filter_by(phone='12345678').first().check_password('new-password-1234'))

 def test_five_wrong_attempts_lock_pin(self):
  import re
  token=self.csrf()
  self.client.post('/forgot-password',data={'_csrf':token,'phone':'12345678'})
  with self.app.app_context(): reset_id=PasswordResetRequest.query.first().id
  self.client.post('/admin/login',data={'_csrf':token,'password':'recovery-admin-tests-only'})
  response=self.client.post('/admin/password-resets/'+str(reset_id)+'/approve',data={'_csrf':token})
  pin=re.search(r'letter-spacing:.3em">(\\d{5})</p>',response.get_data(as_text=True)).group(1)
  wrong='00000' if pin!='00000' else '99999'
  for _ in range(5):
   self.client.post('/verify-recovery-pin',data={'_csrf':token,'phone':'12345678','pin':wrong})
  self.client.post('/verify-recovery-pin',data={'_csrf':token,'phone':'12345678','pin':pin})
  self.assertNotEqual(self.client.get('/new-password').status_code,200)
  with self.app.app_context():
   record=PasswordResetRequest.query.first()
   self.assertEqual(record.pin_attempts,5)
   self.assertIsNotNone(record.used_at)

 def test_unauthorized_admin_action(self):
  self.assertIn(self.client.post('/admin/password-resets/1/approve',data={'_csrf':self.csrf()}).status_code,(302,403))
if __name__=='__main__':unittest.main()

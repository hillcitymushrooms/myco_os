"""Integration tests run against a disposable SQLite database."""
import os, sys, tempfile, unittest
from pathlib import Path
from fastapi.testclient import TestClient

_DB_DIR = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(_DB_DIR.name) / 'test.sqlite3')
os.environ['APP_ENV'] = 'development'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app, DB, Farm

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.a = TestClient(app)
        self.b = TestClient(app)

    def signup(self, client, idx):
        r=client.post('/api/auth/signup', json={'name':'Grower '+idx,'email':f'test-{idx}@test.example','password':'longpassword123','farm_name':'Farm '+idx})
        self.assertEqual(r.status_code, 200,r.text)
        return r.json()['farm_id'],r.json()['csrf']

    def test_farm_isolation_and_flow(self):
        fid,csrf=self.signup(self.a,'a'+os.urandom(5).hex())
        fid2,csrf2=self.signup(self.b,'b'+os.urandom(5).hex())
        a={'X-CSRF-Token':csrf};b={'X-CSRF-Token':csrf2}
        r=self.a.post(f'/api/farms/{fid}/records',json={'kind':'batch','data':{'name':'Lions mane #1','species':'Lion’s mane','blocks':25,'dry_lb':2.4}},headers=a)
        self.assertEqual(r.status_code,200,r.text)
        batch=r.json()
        self.assertEqual(self.b.get(f'/api/farms/{fid}/records').status_code,404)
        self.assertEqual(self.b.post(f'/api/farms/{fid}/harvests',json={'batch_id':batch['id'],'weight_lb':2},headers=b).status_code,404)
        self.assertEqual(self.a.post(f'/api/farms/{fid}/harvests',json={'batch_id':batch['id'],'weight_lb':8.5,'flush':1},headers={}).status_code,403)
        r=self.a.post(f'/api/farms/{fid}/harvests',json={'batch_id':batch['id'],'weight_lb':8.5,'flush':1},headers=a)
        self.assertEqual(r.status_code,200,r.text)
        lot=r.json()['lot_id']
        self.assertAlmostEqual(self.a.get(f'/api/farms/{fid}/summary').json()['stock_lb'],8.5)
        self.assertEqual(self.b.post(f'/api/farms/{fid2}/orders',json={'customer':'Not our lot','lines':[{'lot_id':lot,'quantity_lb':1,'unit_price_cents':1500}]},headers=b).status_code,400)
        r=self.a.post(f'/api/farms/{fid}/orders',json={'customer':'Restaurant','lines':[{'lot_id':lot,'quantity_lb':5,'unit_price_cents':1300}]},headers=a)
        self.assertEqual(r.status_code,200,r.text)
        order_id=r.json()['id']
        self.assertEqual(self.a.post(f'/api/farms/{fid}/orders/{order_id}/fulfill',headers=a).status_code,200)
        self.assertEqual(self.a.post(f'/api/farms/{fid}/orders/{order_id}/fulfill',headers=a).status_code,409)
        summ=self.a.get(f'/api/farms/{fid}/summary').json()
        self.assertAlmostEqual(summ['stock_lb'],3.5)
        self.assertEqual(summ['revenue_cents'],6500)
        exp=self.a.get(f'/api/farms/{fid}/export').json()
        self.assertEqual(len(exp['harvests']),1)
        self.assertEqual(exp['orders'][0]['status'],'fulfilled')
        self.assertEqual(self.b.get(f'/api/farms/{fid}/export').status_code,404)
        self.assertEqual(self.a.put(f'/api/farms/{fid}/records/{batch["id"]}',json={'revision':99,'data':batch['data']},headers=a).status_code,409)

    def test_free_seats_and_security(self):
        fid,csrf=self.signup(self.a,'seat'+os.urandom(5).hex());hdr={'X-CSRF-Token':csrf}
        x=self.a.post(f'/api/farms/{fid}/invites',json={'email':'helper@test.example','role':'technician'},headers=hdr)
        self.assertEqual(x.status_code,200,x.text)
        self.assertIn('invite_url',x.json())
        self.assertEqual(self.a.post(f'/api/farms/{fid}/records',json={'kind':'batch','data':{'name':'X','species':'Oyster','blocks':1}}).status_code,403)
        self.assertEqual(self.a.get('/api/farms/nonexistent/summary').status_code,404)
        self.assertEqual(self.a.post(f'/api/farms/{fid}/billing/checkout',json={'plan':'pro'},headers=hdr).status_code,503)

    def test_employee_invite_and_role_permissions(self):
        uid=os.urandom(5).hex()
        fid,owner_csrf=self.signup(self.a,'owner'+uid)
        fid2,employee_csrf=self.signup(self.b,'employee'+uid)
        # Employees can be invited even on the free plan.
        invited_email='wrong-'+uid+'@test.example'
        r=self.a.post(f'/api/farms/{fid}/invites',json={'email':invited_email,'role':'technician'},headers={'X-CSRF-Token':owner_csrf})
        self.assertEqual(r.status_code,200,r.text)
        from urllib.parse import parse_qs,urlsplit
        token=parse_qs(urlsplit(r.json()['invite_url']).query)['invite'][0]
        self.assertEqual(self.b.post('/api/invites/accept',json={'token':token},headers={'X-CSRF-Token':employee_csrf}).status_code,403)
        # Change the invited email to the second test account to verify successful acceptance.
        from app import Invite
        from sqlalchemy import select
        with DB() as db:
            inv=db.scalar(select(Invite).where(Invite.farm_id==fid))
            inv.email='test-employee'+uid+'@test.example'
            # The second account uses test-employee{uid}@test.example, matching invitation.
            db.commit()
        accepted=self.b.post('/api/invites/accept',json={'token':token},headers={'X-CSRF-Token':employee_csrf})
        self.assertEqual(accepted.status_code,200,accepted.text)
        headers={'X-CSRF-Token':employee_csrf}
        # Allowed to track cultivation but cannot view audit logs or edit owner-only financial records.
        self.assertEqual(self.b.post(f'/api/farms/{fid}/records',json={'kind':'batch','data':{'name':'New batch','species':'Blue Oyster','blocks':25}},headers=headers).status_code,200)
        self.assertEqual(self.b.post(f'/api/farms/{fid}/records',json={'kind':'expense','data':{'name':'Private costs','amount':100}},headers=headers).status_code,403)
        self.assertEqual(self.b.get(f'/api/farms/{fid}/audit').status_code,403)
        self.assertEqual(self.b.get(f'/api/farms/{fid2}/records').status_code,200)

    def test_password_reset_one_time_and_revoke_sessions(self):
        from app import User, OneTimeToken, SessionToken, digest, NOW, ph
        from sqlalchemy import select
        import secrets
        fid,csrf=self.signup(self.a,'reset'+os.urandom(5).hex())
        email=self.a.get('/api/auth/me').json()['user']['email']
        # The real forgot endpoint doesn't expose a token. Verify generic response.
        other=self.a.post('/api/auth/password/forgot',json={'email':'nonexistent-'+os.urandom(4).hex()+'@example.org'})
        found=self.a.post('/api/auth/password/forgot',json={'email':email})
        self.assertEqual(other.status_code,200,other.text)
        self.assertEqual(found.status_code,200,found.text)
        self.assertEqual(other.json(),found.json())
        # Simulate possession of an emailed one-time link; actual token is never stored in cleartext.
        token=secrets.token_urlsafe(48)
        with DB() as db:
            user=db.scalar(select(User).where(User.email==email))
            db.add(OneTimeToken(user_id=user.id,purpose='reset',token_hash=digest(token),expires_at=NOW()+__import__('datetime').timedelta(minutes=30)))
            db.commit()
        bad=self.b.post('/api/auth/password/reset',json={'token':token,'password':'short'})
        self.assertEqual(bad.status_code,422,bad.text)
        good=self.b.post('/api/auth/password/reset',json={'token':token,'password':'replacement passphrase 2026!'})
        self.assertEqual(good.status_code,200,good.text)
        self.assertEqual(self.a.get('/api/auth/me').status_code,401)
        self.assertEqual(self.b.post('/api/auth/password/reset',json={'token':token,'password':'second longer password'}).status_code,400)
        self.assertEqual(self.b.post('/api/auth/login',json={'email':email,'password':'longpassword123'}).status_code,401)
        self.assertEqual(self.b.post('/api/auth/login',json={'email':email,'password':'replacement passphrase 2026!'}).status_code,200)

    def test_change_password_revokes_other_sessions(self):
        uid='pw'+os.urandom(5).hex()
        fid,csrf=self.signup(self.a,uid)
        me=self.a.get('/api/auth/me').json()
        email=me['user']['email'];csrf=me['csrf']
        self.assertEqual(self.b.post('/api/auth/login',json={'email':email,'password':'longpassword123'}).status_code,200)
        self.assertEqual(self.b.get('/api/auth/me').status_code,200)
        wrong=self.a.post('/api/auth/password/change',json={'current_password':'wrong','new_password':'strong replacement passphrase'},headers={'X-CSRF-Token':csrf})
        self.assertEqual(wrong.status_code,403)
        changed=self.a.post('/api/auth/password/change',json={'current_password':'longpassword123','new_password':'strong replacement passphrase'},headers={'X-CSRF-Token':csrf})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(self.b.get('/api/auth/me').status_code,401)
        self.assertEqual(self.a.get('/api/auth/me').status_code,200)

    def test_members_free_and_owner_role_controls(self):
        from app import Membership, User
        from sqlalchemy import select,func
        fid,csrf=self.signup(self.a,'free'+os.urandom(6).hex())
        # no billing gate; multiple invitations, free.
        for i in range(6):
            r=self.a.post(f'/api/farms/{fid}/invites',json={'email':f'crew-{i}-{fid}@example.org','role':'technician'},headers={'X-CSRF-Token':csrf})
            self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.a.get(f'/api/farms/{fid}/summary').json()['member_limit'],None)
        self.assertEqual(self.a.get(f'/api/farms/{fid}/reports/performance.csv').status_code,403)
        created=self.a.post('/api/farms',json={'name':'Second farm'},headers={'X-CSRF-Token':csrf})
        self.assertEqual(created.status_code,200,created.text)
        self.assertEqual(len(self.a.get('/api/auth/me').json()['farms']),2)
        me=self.a.get('/api/auth/me').json()
        ownerid=me['user']['id'];csrf=me['csrf']
        self.assertEqual(self.a.patch(f'/api/farms/{fid}/members/{ownerid}',json={'role':'viewer'},headers={'X-CSRF-Token':csrf}).status_code,400)
        self.assertEqual(self.a.delete(f'/api/farms/{fid}/members/{ownerid}',headers={'X-CSRF-Token':csrf}).status_code,400)

    def test_rate_limiting_and_security_headers(self):
        from app import rate_limit
        from fastapi import HTTPException
        from starlette.requests import Request
        user='auth-limit-'+os.urandom(5).hex()
        for i in range(3):
            r=self.a.post('/api/auth/login',json={'email':f'{user}@example.org','password':'incorrectpassword123'})
            self.assertEqual(r.status_code,401,r.text)
        hdr=self.a.get('/api/health').headers
        self.assertEqual(hdr['x-content-type-options'],'nosniff')
        self.assertIn("default-src 'self'",hdr['content-security-policy'])
        self.assertEqual(self.a.post('/api/auth/login',json={'email':'test@example.org','password':'nope'},headers={'Origin':'https://malicious.example'}).status_code,403)
        oversize=self.a.post('/api/auth/login',content=b'{}',headers={'Content-Length':'4000000'})
        self.assertEqual(oversize.status_code,413)
        account='bruteforce-'+os.urandom(5).hex()+'@example.com'
        responses=[self.b.post('/api/auth/login',json={'email':account,'password':'bad password'}) for _ in range(9)]
        self.assertEqual([r.status_code for r in responses][:8],[401]*8)
        self.assertEqual(responses[-1].status_code,429)

if __name__=='__main__': unittest.main()

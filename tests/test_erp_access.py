import os
import re
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from jg.core import Store, DomainError
from jg.erp_auth import Auth
from jg.erp_access import Access
from jg.personal import create_personal_app

ADMIN = 'admin@example.com'


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 't.db'); self.auth = Auth(self.store)
        self.access = Access(self.store, Path(self.tmp.name) / 'secrets', self.auth)
        self.sent = []
        patcher = patch.object(Access, 'send_mail', lambda _self, to, subject, body: self.sent.append((to, subject, body)))
        patcher.start(); self.addCleanup(patcher.stop)
        self.access.save_mail(ADMIN, 'envios@example.com', 'abcd efgh ijkl mnop', 'JG')

    def code(self):
        return re.search(r'\b(\d{6})\b', self.sent[-1][2]).group(1)

    def test_mail_settings_private(self):
        status = self.access.mail_status()
        self.assertEqual((status['configured'], status['password_configured']), (True, True))
        self.assertNotIn('abcdefghijklmnop', str(status))
        f = Path(self.tmp.name) / 'secrets' / 'mail.json'
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        for bad in [dict(admin_email='no-correo'), dict(app_password='corta')]:
            with self.assertRaises(DomainError):
                self.access.save_mail(**bad)

    def test_code_goes_to_admin_is_single_use_and_creates_worker(self):
        r = self.access.request('Persona@Example.com', '100.64.0.9')
        self.assertTrue(r['to_admin'])
        to, subject, body = self.sent[-1]
        self.assertEqual(to, ADMIN)  # el código llega al propietario, no a quien lo pide
        self.assertIn('persona@example.com solicita acceso', body)
        code = self.code(); self.assertIn(code, subject)
        session = self.access.verify('persona@example.com', code)
        self.assertEqual((session['user']['username'], session['user']['role'], session['user']['projects']), ('persona@example.com', 'worker', []))
        self.assertEqual(self.auth.authenticate(session['token'])['username'], 'persona@example.com')
        with self.assertRaises(DomainError):
            self.access.verify('persona@example.com', code)  # un solo uso

    def test_admin_login_and_role(self):
        r = self.access.request(ADMIN)
        self.assertFalse(r['to_admin'])
        self.assertEqual(self.access.verify(ADMIN, self.code())['user']['role'], 'admin')

    def test_expiry_attempts_and_replacement(self):
        self.access.request('a@example.com'); first = self.code()
        self.access.request('a@example.com'); second = self.code()
        with self.assertRaises(DomainError):
            self.access.verify('a@example.com', first)  # el código anterior queda anulado (cuenta como intento fallido)
        for _ in range(3):
            with self.assertRaisesRegex(DomainError, 'incorrecto'):
                self.access.verify('a@example.com', '000000' if second != '000000' else '111111')
        with self.assertRaisesRegex(DomainError, 'agotaron'):
            self.access.verify('a@example.com', '000000' if second != '000000' else '111111')
        with self.assertRaises(DomainError):
            self.access.verify('a@example.com', second)  # bloqueado tras 5 intentos
        self.access.request('b@example.com'); code = self.code()
        with patch('jg.erp_access.time.time', return_value=time.time() + 601):
            with self.assertRaisesRegex(DomainError, 'vencido'):
                self.access.verify('b@example.com', code)

    def test_codes_never_repeat_and_rate_limit(self):
        with patch('jg.erp_access.secrets.randbelow', side_effect=[123456, 123456, 222222]):
            self.access.request('c@example.com'); c1 = self.code()
            self.access.request('d@example.com'); c2 = self.code()
        self.assertEqual((c1, c2), ('223456', '322222'))  # el repetido se descartó
        self.access.request('c@example.com'); self.access.request('c@example.com')
        with self.assertRaisesRegex(DomainError, 'Demasiadas'):
            self.access.request('c@example.com')

    def test_deactivated_user_blocked(self):
        self.access.request('e@example.com'); s = self.access.verify('e@example.com', self.code())
        admin = {'id': 'x', 'role': 'admin', 'active': True, 'projects': []}
        self.auth.update_user(admin, s['user']['id'], active=False)
        self.access.request('e@example.com')
        with self.assertRaisesRegex(DomainError, 'desactivado'):
            self.access.verify('e@example.com', self.code())

    def test_installers(self):
        with self.assertRaises(DomainError):
            self.access.installer('linux')
        with self.assertRaises(DomainError):
            self.access.save_tailscale('no-es-clave')
        self.access.save_tailscale('tskey-auth-ABC123-xyz7890', '100.64.0.10', 8765)
        self.assertNotIn('ABC123', str(self.access.tailscale_status()))
        for system, ext in [('linux', '.sh'), ('windows', '.cmd'), ('macos', '.command')]:
            name, text, _ = self.access.installer(system)
            self.assertTrue(name.endswith(ext))
            self.assertIn('http://100.64.0.10:8765/', text); self.assertIn('tskey-auth-ABC123-xyz7890', text)
            self.assertNotIn('{{', text)
        self.assertIn('\r\n', self.access.installer('windows')[1])
        for system in ('linux', 'macos'):
            path = Path(self.tmp.name) / f'{system}.sh'; path.write_text(self.access.installer(system)[1])
            self.assertEqual(subprocess.run(['bash', '-n', str(path)], capture_output=True).returncode, 0, system)
        with self.assertRaises(DomainError):
            self.access.installer('android')


class RemoteRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'JG_ALLOWED_HOSTS': '100.64.0.10'}); self.env.start(); self.addCleanup(self.env.stop)
        self.app = create_personal_app(Path(self.tmp.name) / 'p.db')
        self.local = TestClient(self.app, base_url='http://127.0.0.1:8765')
        self.remote = TestClient(self.app, base_url='http://100.64.0.10:8765')
        self.sent = []
        patcher = patch.object(Access, 'send_mail', lambda _self, to, subject, body: self.sent.append(body))
        patcher.start(); self.addCleanup(patcher.stop)

    def test_remote_requires_email_code(self):
        owner = self.local.get('/session').json()['token']
        h = {'Authorization': 'Bearer ' + owner}
        self.local.put('/erp/mail/config', json={'admin_email': ADMIN, 'sender': 's@example.com', 'app_password': 'abcdefghijklmnop'}, headers=h)
        self.assertEqual(self.remote.get('/session').status_code, 403)          # sin sesión automática a distancia
        self.assertEqual(self.remote.get('/legacy').status_code, 403)            # panel anterior no accesible
        self.assertEqual(self.remote.get('/erp/records/clients', headers=h).status_code, 403)  # el token local no sirve remoto
        self.assertEqual(TestClient(self.app, base_url='http://evil.example:8765').get('/').status_code, 403)
        self.assertEqual(self.remote.get('/').status_code, 200)
        self.assertEqual(self.remote.post('/erp/auth/request', json={'email': 'p@example.com'}).status_code, 200)
        code = re.search(r'\b(\d{6})\b', self.sent[-1]).group(1)
        session = self.remote.post('/erp/auth/verify', json={'email': 'p@example.com', 'code': code}).json()
        me = self.remote.get('/erp/auth/me', headers={'Authorization': 'Bearer ' + session['token']}).json()
        self.assertEqual((me['username'], me['role']), ('p@example.com', 'worker'))
        self.assertGreaterEqual(self.remote.get('/erp/mail/config', headers={'Authorization': 'Bearer ' + session['token']}).status_code, 400)  # sin permisos de configuración


if __name__ == '__main__':
    unittest.main()

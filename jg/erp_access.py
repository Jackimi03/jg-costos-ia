"""Acceso por correo con código de un solo uso, envío de correo por Gmail e instaladores con Tailscale.

Flujo: la persona escribe su correo → el servidor genera un código numérico de 6 dígitos que nunca se repite
(se guarda solo su huella HMAC, única en la tabla) → el código se envía al correo del ADMINISTRADOR (El propietario) →
el propietario se lo comunica a la persona → al escribirlo, se crea la sesión. Si quien entra es el propio administrador,
el código le llega a él mismo. Secretos (contraseña de aplicación de Gmail, clave de Tailscale, sal de códigos) en
<carpeta de la base>/secrets/*.json con permisos 0600; nunca se devuelven al navegador.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from .core import DomainError

EMAIL_RE = re.compile(r'[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}')
CODE_TTL = 600            # 10 minutos
MAX_ATTEMPTS = 5          # intentos por código
PER_EMAIL_WINDOW = 900    # 15 minutos
PER_EMAIL_MAX = 3         # solicitudes por correo en la ventana
GLOBAL_HOURLY_MAX = 30    # solicitudes totales por hora
PANAMA = timezone(timedelta(hours=-5))


def _email(value):
    value = str(value or '').strip().lower()
    if not EMAIL_RE.fullmatch(value) or len(value) > 254:
        raise DomainError('Escriba un correo válido')
    return value


class Access:
    def __init__(self, store, secrets_dir, auth):
        self.store, self.auth, self.dir = store, auth, Path(secrets_dir)
        with store.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS erp_access_codes(id TEXT PRIMARY KEY,email TEXT NOT NULL,code_hash TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL,expires_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,used_at REAL,origin TEXT)''')
            c.execute('CREATE INDEX IF NOT EXISTS erp_access_email ON erp_access_codes(email,created_at)')

    # ---- secretos -------------------------------------------------------------------------------
    def _read(self, name):
        f = self.dir / f'{name}.json'
        try:
            return json.loads(f.read_text()) if f.is_file() else {}
        except (OSError, ValueError):
            return {}

    def _write(self, name, data):
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        f = self.dir / f'{name}.json'
        tmp = f.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out)
        os.replace(tmp, f)
        os.chmod(f, 0o600)

    def _pepper(self):
        data = self._read('access')
        if not data.get('pepper'):
            data['pepper'] = secrets.token_hex(32)
            self._write('access', data)
        return bytes.fromhex(data['pepper'])

    def _hash(self, code):
        return hmac.new(self._pepper(), code.encode(), hashlib.sha256).hexdigest()

    # ---- correo -----------------------------------------------------------------------------------
    def mail_status(self):
        m = self._read('mail')
        return {'admin_email': m.get('admin_email', ''), 'sender': m.get('sender', ''), 'sender_name': m.get('sender_name', 'JG Costos IA'),
                'password_configured': bool(m.get('app_password')), 'configured': bool(m.get('admin_email') and m.get('sender') and m.get('app_password'))}

    def save_mail(self, admin_email=None, sender=None, app_password=None, sender_name=None):
        m = self._read('mail')
        if admin_email is not None:
            m['admin_email'] = _email(admin_email)
        if sender is not None:
            m['sender'] = _email(sender)
        if app_password:
            password = re.sub(r'\s+', '', str(app_password))
            if not re.fullmatch(r'[A-Za-z0-9]{16}', password):
                raise DomainError('La contraseña de aplicación de Google tiene 16 letras (puede pegarla con o sin espacios)')
            m['app_password'] = password
        if sender_name is not None:
            m['sender_name'] = str(sender_name).strip()[:80] or 'JG Costos IA'
        self._write('mail', m)
        return self.mail_status()

    def send_mail(self, to, subject, body):
        m = self._read('mail')
        if not (m.get('sender') and m.get('app_password')):
            raise DomainError('Configure el correo de envío en Configuración → Correo y acceso')
        msg = EmailMessage()
        msg['From'] = f'{m.get("sender_name") or "JG Costos IA"} <{m["sender"]}>'
        msg['To'] = to
        msg['Subject'] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP_SSL(m.get('smtp_host', 'smtp.gmail.com'), int(m.get('smtp_port', 465)), context=ssl.create_default_context(), timeout=20) as smtp:
                smtp.login(m['sender'], m['app_password'])
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise DomainError('Gmail rechazó la cuenta o la contraseña de aplicación') from None
        except (OSError, smtplib.SMTPException):
            raise DomainError('No se pudo enviar el correo; revise la conexión del servidor y la cuenta de Gmail') from None

    def test_mail(self):
        admin = self.mail_status()['admin_email']
        if not admin:
            raise DomainError('Indique el correo del administrador')
        self.send_mail(admin, 'Prueba de correo · JG Costos IA', 'El servidor de JG Costos IA puede enviar correos. Desde ahora los códigos de acceso llegarán a esta dirección.')
        return {'sent_to': admin}

    # ---- códigos ----------------------------------------------------------------------------------
    def request(self, email, origin=''):
        email = _email(email)
        admin = self.mail_status()['admin_email']
        if not admin:
            raise DomainError('El acceso por correo aún no está configurado')
        now = time.time()
        with self.store.connect() as c:
            recent = c.execute('SELECT COUNT(*) FROM erp_access_codes WHERE email=? AND created_at>?', (email, now - PER_EMAIL_WINDOW)).fetchone()[0]
            total = c.execute('SELECT COUNT(*) FROM erp_access_codes WHERE created_at>?', (now - 3600,)).fetchone()[0]
        if recent >= PER_EMAIL_MAX or total >= GLOBAL_HOURLY_MAX:
            raise DomainError('Demasiadas solicitudes. Espere unos minutos e intente de nuevo')
        ident = secrets.token_hex(16)
        for _ in range(50):  # el código nunca se repite: la huella es única en toda la historia
            code = f'{secrets.randbelow(900000) + 100000}'
            try:
                with self.store.connect() as c:
                    c.execute("UPDATE erp_access_codes SET used_at=? WHERE email=? AND used_at IS NULL", (now, email))  # anula códigos anteriores
                    c.execute('INSERT INTO erp_access_codes(id,email,code_hash,created_at,expires_at,origin) VALUES(?,?,?,?,?,?)',
                              (ident, email, self._hash(code), now, now + CODE_TTL, str(origin)[:100]))
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise DomainError('No se pudo generar un código nuevo; intente otra vez')
        when = datetime.fromtimestamp(now, PANAMA).strftime('%d/%m/%Y %H:%M')
        is_admin = email == admin
        body = (f'Código de acceso a JG Costos IA: {code}\n\n'
                + ('Usted solicitó entrar al ERP.' if is_admin else f'{email} solicita acceso al ERP. Si autoriza su ingreso, comuníquele este código.')
                + f'\n\nSolicitud: {when} (hora de Panamá){" desde " + origin if origin else ""}.\nEl código vale 10 minutos y se puede usar una sola vez.'
                + '\n\nSi no reconoce esta solicitud, ignore este correo: sin el código nadie puede entrar.')
        try:
            self.send_mail(admin, f'Código de acceso {code} · JG Costos IA', body)
        except DomainError:
            with self.store.connect() as c:
                c.execute('DELETE FROM erp_access_codes WHERE id=?', (ident,))
            raise
        return {'sent': True, 'expires_in': CODE_TTL, 'to_admin': not is_admin}

    def verify(self, email, code):
        email = _email(email)
        code = re.sub(r'\D', '', str(code or ''))
        if len(code) != 6:
            raise DomainError('El código tiene 6 dígitos')
        now = time.time()
        expected = self._hash(code)
        failure = None
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM erp_access_codes WHERE email=? AND used_at IS NULL AND expires_at>? ORDER BY created_at DESC LIMIT 1', (email, now)).fetchone()
            if not row:
                raise DomainError('Código vencido o inexistente. Solicite uno nuevo')
            attempts = row['attempts'] + 1
            if hmac.compare_digest(row['code_hash'], expected):
                c.execute('UPDATE erp_access_codes SET attempts=?,used_at=? WHERE id=?', (attempts, now, row['id']))
            else:
                # El intento fallido se confirma ANTES de responder: lanzar dentro de la transacción la desharía
                # y permitiría probar códigos sin límite.
                c.execute('UPDATE erp_access_codes SET attempts=?,used_at=CASE WHEN ?>=? THEN ? ELSE used_at END WHERE id=?',
                          (attempts, attempts, MAX_ATTEMPTS, now, row['id']))
                failure = 'Código incorrecto' + (' · se agotaron los intentos, solicite otro' if attempts >= MAX_ATTEMPTS else '')
        if failure:
            raise DomainError(failure)
        admin = self.mail_status()['admin_email']
        user = self.auth.ensure_email_user(email, 'admin' if email == admin else 'worker')
        if not user['active']:
            raise DomainError('Su acceso está desactivado; contacte al administrador')
        return self.auth.issue_session(user['id'])

    # ---- instaladores -------------------------------------------------------------------------------
    def tailscale_status(self):
        t = self._read('tailscale')
        key = t.get('auth_key', '')
        return {'server_address': t.get('server_address', ''), 'port': t.get('port', 8765), 'key_configured': bool(key),
                'key_hint': ('…' + key[-4:]) if key else '', 'configured': bool(key and t.get('server_address'))}

    def save_tailscale(self, auth_key=None, server_address=None, port=None):
        t = self._read('tailscale')
        if auth_key:
            auth_key = str(auth_key).strip()
            if not re.fullmatch(r'tskey-[A-Za-z0-9-]{10,200}', auth_key):
                raise DomainError('La clave de Tailscale empieza por tskey- (Admin console → Settings → Keys)')
            t['auth_key'] = auth_key
        if server_address is not None:
            server_address = str(server_address).strip()
            if server_address and not re.fullmatch(r'[A-Za-z0-9.-]{3,253}', server_address):
                raise DomainError('Dirección del servidor inválida: use la IP de Tailscale (100.x.x.x) o su nombre')
            t['server_address'] = server_address
        if port is not None:
            port = int(port)
            if not 1 <= port <= 65535:
                raise DomainError('Puerto inválido')
            t['port'] = port
        self._write('tailscale', t)
        return self.tailscale_status()

    def installer(self, system):
        t = self._read('tailscale')
        if not (t.get('auth_key') and t.get('server_address')):
            raise DomainError('Configure la clave de Tailscale y la dirección del servidor antes de descargar instaladores')
        url = f'http://{t["server_address"]}:{int(t.get("port", 8765))}/'
        stamp = datetime.now(PANAMA).strftime('%Y-%m-%d %H:%M')
        values = {'URL': url, 'KEY': t['auth_key'], 'STAMP': stamp, 'ICON': ICON_SVG}
        template, filename, media = {'linux': (LINUX, 'instalar-jg-costos-ia-linux.sh', 'text/x-shellscript'),
                                     'windows': (WINDOWS, 'instalar-jg-costos-ia-windows.cmd', 'application/octet-stream'),
                                     'macos': (MACOS, 'instalar-jg-costos-ia-mac.command', 'text/x-shellscript')}.get(system, (None, None, None))
        if not template:
            raise DomainError('Sistema no soportado')
        text = template
        for k, v in values.items():
            text = text.replace('{{' + k + '}}', v)
        if system == 'windows':
            text = text.replace('\r\n', '\n').replace('\n', '\r\n')
        return filename, text, media


ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#fff"/><text x="32" y="43" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-size="30" font-weight="700"><tspan fill="#1F3556">J</tspan><tspan fill="#6A1F35">G</tspan></text></svg>'

LINUX = r'''#!/usr/bin/env bash
# Instalador de JG Costos IA para Linux · generado {{STAMP}}
# Instala Tailscale (red privada), conecta este equipo al servidor de JG y crea el icono del ERP.
# Uso: bash instalar-jg-costos-ia-linux.sh   (pedirá la contraseña del equipo para instalar)
# Este archivo contiene una clave de acceso a la red privada: compártalo solo con personas autorizadas.
set -euo pipefail
URL='{{URL}}'
KEY='{{KEY}}'
say(){ printf '\n==> %s\n' "$1"; }
SUDO=''; [ "$(id -u)" -ne 0 ] && SUDO='sudo'
ok(){ curl -fsS --max-time 5 "${URL}health" >/dev/null 2>&1; }
if ! command -v curl >/dev/null 2>&1; then echo 'Falta curl: instálelo (ej. sudo apt install curl) y repita.'; exit 1; fi
if ! command -v tailscale >/dev/null 2>&1; then
  say 'Instalando Tailscale (red privada)…'
  curl -fsSL https://tailscale.com/install.sh | $SUDO sh
fi
$SUDO systemctl enable --now tailscaled >/dev/null 2>&1 || true
if ok; then
  say 'Este equipo ya llega al servidor de JG.'
else
  say 'Conectando este equipo a la red privada de JG…'
  NAME="jg-$(hostname | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-' | cut -c1-40)"
  $SUDO tailscale up --authkey="$KEY" --hostname="$NAME" || {
    echo 'Tailscale ya estaba conectado a otra cuenta. Ejecute: sudo tailscale logout  y vuelva a correr este instalador.'; exit 1; }
fi
for _ in $(seq 1 30); do ok && break; sleep 2; done
ok || { echo 'No se pudo llegar al servidor. Revise internet y vuelva a ejecutar el instalador.'; exit 1; }
say 'Creando el icono de JG Costos IA…'
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"; ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons"
mkdir -p "$APPS" "$ICONS"
cat > "$ICONS/jg-costos-ia.svg" <<'SVG'
{{ICON}}
SVG
cat > "$APPS/jg-costos-ia-remoto.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=JG Costos IA
Comment=ERP de JG Soluciones PA
Exec=xdg-open ${URL}
Icon=${ICONS}/jg-costos-ia.svg
Terminal=false
Categories=Office;Finance;
EOF
chmod +x "$APPS/jg-costos-ia-remoto.desktop"
update-desktop-database "$APPS" >/dev/null 2>&1 || true
say 'Listo. Abra "JG Costos IA" desde el menú de aplicaciones y entre con su correo.'
xdg-open "$URL" >/dev/null 2>&1 || true
'''

WINDOWS = r'''<# :
@echo off
rem Instalador de JG Costos IA para Windows - generado {{STAMP}}. Doble clic para instalar.
net session >nul 2>&1 || (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([IO.File]::ReadAllText('%~f0'))"
pause
exit /b
#>
# Instala Tailscale (red privada), conecta este equipo al servidor de JG y crea el icono del ERP.
# Este archivo contiene una clave de acceso a la red privada: compartalo solo con personas autorizadas.
$ErrorActionPreference = 'Stop'
$Url = '{{URL}}'
$Key = '{{KEY}}'
function Test-Erp { try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 ($Url + 'health')).StatusCode -eq 200 } catch { $false } }
$ts = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path $ts)) {
  Write-Host '==> Instalando Tailscale (red privada)...'
  $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
  $msi = Join-Path $env:TEMP 'tailscale-setup.msi'
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -UseBasicParsing "https://pkgs.tailscale.com/stable/tailscale-setup-latest-$arch.msi" -OutFile $msi
  Start-Process msiexec.exe -Wait -ArgumentList "/i `"$msi`" /quiet /norestart TS_UNATTENDEDMODE=always"
  for ($i = 0; $i -lt 30 -and -not (Test-Path $ts); $i++) { Start-Sleep 2 }
}
if (Test-Erp) { Write-Host '==> Este equipo ya llega al servidor de JG.' }
else {
  Write-Host '==> Conectando este equipo a la red privada de JG...'
  $name = 'jg-' + (($env:COMPUTERNAME -replace '[^A-Za-z0-9-]', '').ToLower())
  & $ts up --authkey=$Key --unattended --hostname=$name
}
$ok = $false; for ($i = 0; $i -lt 30; $i++) { if (Test-Erp) { $ok = $true; break }; Start-Sleep 2 }
if (-not $ok) { Write-Host 'No se pudo llegar al servidor. Revise internet y vuelva a ejecutar el instalador.'; exit 1 }
Write-Host '==> Creando el icono de JG Costos IA...'
$link = "[InternetShortcut]`r`nURL=$Url`r`n"
$desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
$menu = Join-Path ([Environment]::GetFolderPath('CommonStartMenu')) 'Programs'
foreach ($dir in @($desktop, $menu)) { Set-Content -Path (Join-Path $dir 'JG Costos IA.url') -Value $link -Encoding ASCII }
Write-Host '==> Listo. Abra "JG Costos IA" desde el escritorio o el menu Inicio y entre con su correo.'
Start-Process $Url
'''

MACOS = r'''#!/bin/bash
# Instalador de JG Costos IA para Mac · generado {{STAMP}}
# Doble clic (si macOS lo bloquea: clic derecho → Abrir). Pedirá la contraseña del Mac.
# Instala Tailscale (red privada), conecta este Mac al servidor de JG y crea la app del ERP.
# Este archivo contiene una clave de acceso a la red privada: compártalo solo con personas autorizadas.
set -euo pipefail
URL='{{URL}}'
KEY='{{KEY}}'
say(){ printf '\n==> %s\n' "$1"; }
ok(){ curl -fsS --max-time 5 "${URL}health" >/dev/null 2>&1; }
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
if [ ! -x "$TS" ]; then
  say 'Instalando Tailscale (red privada)…'
  PKG="$(mktemp -d)/Tailscale.pkg"
  curl -fsSL -o "$PKG" https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg
  sudo installer -pkg "$PKG" -target /
fi
open -a Tailscale || true
say 'Si macOS pide permitir la configuración de VPN de Tailscale, pulse "Permitir".'
for _ in $(seq 1 30); do "$TS" status >/dev/null 2>&1 && break; "$TS" version >/dev/null 2>&1 && break; sleep 2; done
if ok; then
  say 'Este Mac ya llega al servidor de JG.'
else
  say 'Conectando este Mac a la red privada de JG…'
  NAME="jg-$(scutil --get ComputerName 2>/dev/null | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-' | cut -c1-40)"
  "$TS" up --authkey="$KEY" --hostname="$NAME"
fi
for _ in $(seq 1 30); do ok && break; sleep 2; done
ok || { echo 'No se pudo llegar al servidor. Abra Tailscale, acepte los permisos y vuelva a ejecutar el instalador.'; exit 1; }
say 'Creando la app JG Costos IA…'
APP="$HOME/Applications/JG Costos IA.app"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/MacOS/JG Costos IA" <<EOF
#!/bin/sh
open "${URL}"
EOF
chmod +x "$APP/Contents/MacOS/JG Costos IA"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>JG Costos IA</string>
<key>CFBundleExecutable</key><string>JG Costos IA</string>
<key>CFBundleIdentifier</key><string>pa.jgsoluciones.costos-ia</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
PLIST
say 'Listo. Abra "JG Costos IA" desde Aplicaciones (en su carpeta personal) y entre con su correo.'
open "$URL"
'''

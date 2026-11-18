#!/usr/bin/env python3
"""Small configuration web UI for postfix-365.

Standard library only, so the image needs nothing beyond python3.

Features: edit the relay settings (saved to SETTINGS_FILE and applied with
configure.sh + postfix reload), create the Microsoft 365 OAuth2 token,
show Postfix status and queue, send a test message and read the mail log.

Protected by HTTP Basic auth (WEBUI_USER / WEBUI_PASSWORD) and served over
HTTPS with the container certificate unless WEBUI_TLS=false.
"""

import base64
import hmac
import html
import json
import os
import pwd
import re
import secrets
import shlex
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SETTINGS_FILE = os.environ.get("SETTINGS_FILE", "/config/settings.env")
TOKENS_DIR = os.environ.get("TOKENS_DIR", "/etc/tokens")
CERTS_DIR = os.environ.get("CERTS_DIR", "/ssl_certs")
MAILLOG = os.environ.get("MAILLOG", "/var/log/maillog")
CONFIGURE_CMD = os.environ.get("CONFIGURE_CMD", "/scripts/configure.sh")
SBIN = os.environ.get("POSTFIX_SBIN", "/usr/sbin")

WEBUI_USER = os.environ.get("WEBUI_USER", "admin")
WEBUI_PASSWORD = os.environ.get("WEBUI_PASSWORD", "")
WEBUI_PORT = int(os.environ.get("WEBUI_PORT", "8080"))
WEBUI_BIND = os.environ.get("WEBUI_BIND", "0.0.0.0")
WEBUI_TLS = os.environ.get("WEBUI_TLS", "true").lower() in ("1", "true", "yes")

# Override both for national clouds, e.g. https://login.microsoftonline.us and
# "openid offline_access https://outlook.office365.us/SMTP.Send".
LOGIN_BASE = os.environ.get("MS_LOGIN_BASE", "https://login.microsoftonline.com").rstrip("/")
REDIRECT_URI = LOGIN_BASE + "/common/oauth2/nativeclient"
SCOPE = os.environ.get("MS_SCOPE", "openid offline_access https://outlook.office.com/SMTP.Send")

RE_HOST = r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?"
RE_EMAIL = r"[A-Za-z0-9._%+-]+@" + RE_HOST
RE_BOOL = r"(?i)true|false|yes|no|0|1"

# key, label, kind, regex, required, default, help
FIELDS = [
    ("General", [
        ("SMTP_HOSTNAME", "SMTP host name", "text", RE_HOST, True, "",
         "Name this relay announces, e.g. smtp.example.com"),
        ("DOMAIN_NAME", "Domain", "text", RE_HOST, True, "", "e.g. example.com"),
        ("MY_NETWORK", "Trusted networks", "text", r"[][0-9A-Fa-f:./, ]*", False, "",
         "Clients allowed to relay without authentication (CIDR, comma separated)"),
        ("MESSAGE_SIZE_LIMIT", "Max message size (bytes)", "text", r"[0-9]+", True, "10240000", ""),
        ("TIMEZONE", "Time zone", "text", r"[A-Za-z0-9_+/-]+", True, "UTC", "e.g. Europe/Rome"),
    ]),
    ("Microsoft 365 relay", [
        ("RELAY_HOST", "Relay host", "text", RE_HOST, True, "smtp.office365.com", ""),
        ("RELAY_HOST_PORT", "Relay port", "text", r"[0-9]{1,5}", True, "587", ""),
        ("AUTH_USER", "Mailbox", "text", RE_EMAIL, True, "",
         "Microsoft 365 account used to send mail"),
        ("TENANT_ID", "Tenant ID", "text", r"[A-Za-z0-9.-]+", True, "",
         "Directory (tenant) ID of the Entra ID app"),
        ("CLIENT_ID", "Client ID", "text", r"[A-Za-z0-9-]+", True, "",
         "Application (client) ID"),
        ("CLIENT_SECRET", "Client secret", "secret", r"[^\r\n]*", False, "",
         "Secret value; leave empty to keep the current one"),
    ]),
    ("Incoming authentication", [
        ("SMTPD_USERS", "SMTP users", "secret", r"[^\r\n]*", False, "",
         "user@domain:password, comma separated. Leave empty to keep the current list"),
        ("DISABLE_SMTP_AUTH_ON_PORT_25", "Disable authentication on port 25", "bool", RE_BOOL, False, "false",
         "Port 25 then accepts only trusted networks; 587/465 still authenticate"),
    ]),
    ("Diagnostics", [
        ("SASL_XOAUTH2_DEBUG", "Verbose sasl-xoauth2 logging", "bool", RE_BOOL, False, "false",
         "Logs full OAuth2 traces, which can contain tokens. Turn off when done"),
    ]),
]
FIELD_INDEX = {f[0]: f for _, group in FIELDS for f in group}

CSRF_TOKEN = secrets.token_urlsafe(32)
OAUTH_STATE = {"value": None}


# --- settings ----------------------------------------------------------------

def read_settings_file():
    """Parse the KEY='value' file written by save_settings()."""
    values = {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                try:
                    parts = shlex.split(raw)
                except ValueError:
                    continue
                values[key.strip()] = parts[0] if parts else ""
    except FileNotFoundError:
        pass
    return values


def effective_settings():
    """Value used by configure.sh: settings file, then environment, then default."""
    saved = read_settings_file()
    result = {}
    for key, field in FIELD_INDEX.items():
        default = field[5]
        env = os.environ.get(key)
        if key == "SMTP_HOSTNAME" and not env:
            env = os.environ.get("HOSTNAME")
        result[key] = saved[key] if key in saved else (env if env is not None else default)
    return result


def validate(values):
    errors = []
    for key, value in values.items():
        _, label, kind, regex, required, _, _ = FIELD_INDEX[key]
        if required and not value:
            errors.append(f"{label} is required")
        elif value and not re.fullmatch(regex, value):
            errors.append(f"{label}: invalid value")
    return errors


def save_settings(values):
    os.makedirs(os.path.dirname(SETTINGS_FILE) or ".", exist_ok=True)
    lines = ["# Written by the postfix-365 web UI. Overrides docker-compose.yml values.\n"]
    lines += [f"{k}={shlex.quote(v)}\n" for k, v in values.items()]
    atomic_write(SETTINGS_FILE, "".join(lines))


def atomic_write(path, content, owner=None):
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, 0o600)
        if owner:
            try:
                pw = pwd.getpwnam(owner)
                os.chown(tmp, pw.pw_uid, pw.pw_gid)
            except (KeyError, PermissionError):
                pass
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def run(cmd, stdin=None, timeout=60):
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def apply_settings(values):
    """Save, regenerate the configuration and reload Postfix; roll back on failure."""
    backup = None
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            backup = fh.read()
    save_settings(values)
    code, out = run([CONFIGURE_CMD])
    if code != 0:
        if backup is None:
            os.unlink(SETTINGS_FILE)
        else:
            atomic_write(SETTINGS_FILE, backup)
        run([CONFIGURE_CMD])
        return False, "Configuration rejected, previous settings restored.\n\n" + out
    if postfix_running():
        code2, out2 = run([f"{SBIN}/postfix", "reload"])
    else:
        code2, out2 = run(["supervisorctl", "restart", "postfix"])
    return code2 == 0, out + "\n\n" + out2


# --- status ------------------------------------------------------------------

def postfix_running():
    return run([f"{SBIN}/postfix", "status"], timeout=10)[0] == 0


def queue_summary():
    code, out = run([f"{SBIN}/postqueue", "-p"], timeout=20)
    if code != 0:
        return "unavailable"
    last = out.strip().splitlines()[-1] if out.strip() else ""
    return "empty" if "empty" in last.lower() else last.strip("- ")


def token_status(user):
    path = os.path.join(TOKENS_DIR, user)
    if not user or not os.path.isfile(path):
        return False, "missing: create it on the Token page"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False, "unreadable or not valid JSON"
    if not data.get("refresh_token"):
        return False, "no refresh_token in file"
    updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
    return True, f"present, last updated {updated}"


def tail(path, lines=200):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            data = fh.read().decode("utf-8", "replace")
    except OSError as exc:
        return f"cannot read {path}: {exc}"
    return "\n".join(data.splitlines()[-lines:])


# --- OAuth2 token --------------------------------------------------------------

def authorize_url(settings):
    OAUTH_STATE["value"] = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode({
        "client_id": settings["CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "response_mode": "query",
        "scope": SCOPE,
        "login_hint": settings["AUTH_USER"],
        "state": OAUTH_STATE["value"],
    })
    return f"{LOGIN_BASE}/{settings['TENANT_ID']}/oauth2/v2.0/authorize?{query}"


def exchange_code(settings, pasted):
    """Same exchange as `sasl-xoauth2-tool get-token outlook`."""
    pasted = pasted.strip()
    if not pasted.startswith(REDIRECT_URI):
        raise ValueError(f"the pasted URL must start with {REDIRECT_URI}")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
    if "error" in params:
        raise ValueError(params.get("error_description", params["error"])[0])
    if "code" not in params:
        raise ValueError("no authorization code in the pasted URL")
    state = params.get("state", [None])[0]
    if OAUTH_STATE["value"] is None or state != OAUTH_STATE["value"]:
        raise ValueError("this login link is stale: open the link on this page again")
    OAUTH_STATE["value"] = None

    form = {
        "client_id": settings["CLIENT_ID"],
        "scope": SCOPE,
        "code": params["code"][0],
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    if settings["CLIENT_SECRET"]:
        form["client_secret"] = settings["CLIENT_SECRET"]
    request = urllib.request.Request(
        f"{LOGIN_BASE}/{settings['TENANT_ID']}/oauth2/v2.0/token",
        data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            content = json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("error_description", "")
        except ValueError:
            detail = ""
        raise ValueError(f"Microsoft returned HTTP {exc.code}: {detail.splitlines()[0] if detail else ''}")
    if "refresh_token" not in content:
        raise ValueError("no refresh_token in the response (is offline_access granted?)")

    os.makedirs(TOKENS_DIR, exist_ok=True)
    token = {"access_token": content["access_token"],
             "refresh_token": content["refresh_token"],
             "expiry": 0}
    atomic_write(os.path.join(TOKENS_DIR, settings["AUTH_USER"]),
                 json.dumps(token, indent=4), owner="postfix")


def send_test(settings, recipient):
    msg = EmailMessage()
    msg["From"] = settings["AUTH_USER"]
    msg["To"] = recipient
    msg["Subject"] = "postfix-365 test message"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings["DOMAIN_NAME"] or None)
    msg.set_content(f"Test message sent from the postfix-365 web UI on {settings['SMTP_HOSTNAME']}.")
    return run([f"{SBIN}/sendmail", "-i", "-f", settings["AUTH_USER"], recipient],
               stdin=msg.as_string(), timeout=30)


# --- HTML ----------------------------------------------------------------------

CSS = """
:root{--bg:#f6f7f9;--fg:#1d232b;--muted:#5c6773;--card:#fff;--line:#dde2e8;--accent:#0b5cad;
--ok:#1a7f37;--bad:#c62828;--code:#eef1f4}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--fg:#e6e9ed;--muted:#9aa5b1;--card:#1c2026;
--line:#2d333b;--accent:#5aa2f0;--ok:#4cc26a;--bad:#f06a6a;--code:#11141a}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;
flex-wrap:wrap;gap:16px;align-items:center}header b{font-size:17px;margin-right:auto}
nav a{color:var(--fg);text-decoration:none;padding:6px 10px;border-radius:6px}
nav a.on{background:var(--accent);color:#fff}main{max-width:900px;margin:0 auto;padding:16px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
h2{font-size:16px;margin:0 0 12px}label{display:block;font-weight:600;margin-top:12px}
small{display:block;color:var(--muted)}input[type=text],input[type=password],input[type=email],textarea{
width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
button{margin-top:16px;padding:8px 16px;border:0;border-radius:6px;background:var(--accent);color:#fff;
font:inherit;cursor:pointer}pre{background:var(--code);padding:12px;border-radius:6px;overflow:auto;
white-space:pre-wrap;word-break:break-all;font-size:13px}.ok{color:var(--ok)}.bad{color:var(--bad)}
.msg{border-left:4px solid var(--accent)}.msg.err{border-color:var(--bad)}
table{border-collapse:collapse;width:100%}td{padding:6px 0;border-bottom:1px solid var(--line);vertical-align:top}
td:first-child{color:var(--muted);width:40%}a{color:var(--accent);word-break:break-all}
.check{display:flex;gap:8px;align-items:center;font-weight:600;margin-top:12px}
"""

PAGES = [("/", "Status"), ("/settings", "Settings"), ("/token", "Token"),
         ("/test", "Test mail"), ("/logs", "Logs")]

e = html.escape


def layout(path, body, message=None, error=False):
    nav = "".join(f'<a href="{p}" class="{"on" if p == path else ""}">{t}</a>' for p, t in PAGES)
    note = ""
    if message:
        note = f'<section class="msg{" err" if error else ""}"><pre>{e(message)}</pre></section>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>postfix-365</title>
<style>{CSS}</style></head><body><header><b>postfix-365</b><nav>{nav}</nav></header>
<main>{note}{body}</main></body></html>"""


def form(action, inner, button):
    return (f'<form method="post" action="{action}"><input type="hidden" name="csrf" value="{CSRF_TOKEN}">'
            f'{inner}<button type="submit">{e(button)}</button></form>')


def page_status():
    s = effective_settings()
    running = postfix_running()
    tok_ok, tok_msg = token_status(s["AUTH_USER"])
    rows = [
        ("Postfix", f'<span class="{"ok" if running else "bad"}">{"running" if running else "not running"}</span>'),
        ("Relay", e(f"[{s['RELAY_HOST']}]:{s['RELAY_HOST_PORT']}")),
        ("Mailbox", e(s["AUTH_USER"] or "not set")),
        ("Token", f'<span class="{"ok" if tok_ok else "bad"}">{e(tok_msg)}</span>'),
        ("Queue", e(queue_summary())),
        ("Settings file", e(SETTINGS_FILE if os.path.exists(SETTINGS_FILE) else "not used (docker-compose values)")),
    ]
    table = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    flush = form("/flush", "", "Retry queued mail now")
    return f"<section><h2>Status</h2><table>{table}</table>{flush}</section>"


def page_settings():
    s = effective_settings()
    out = []
    for title, group in FIELDS:
        out.append(f"<section><h2>{e(title)}</h2>")
        for key, label, kind, _, required, _, hint in group:
            value = s.get(key, "")
            if kind == "bool":
                checked = "checked" if value.lower() in ("true", "yes", "1") else ""
                out.append(f'<label class="check"><input type="checkbox" name="{key}" value="true" {checked}>'
                           f'{e(label)}</label><small>{e(hint)}</small>')
                continue
            if kind == "secret":
                state = "set" if value else "not set"
                field = (f'<input type="password" name="{key}" autocomplete="new-password" '
                         f'placeholder="currently {state}">')
                clear = (f'<label class="check" style="font-weight:400"><input type="checkbox" '
                         f'name="{key}__clear" value="1">Remove the current value</label>') if value else ""
            else:
                field = (f'<input type="text" name="{key}" value="{e(value)}"'
                         f'{" required" if required else ""}>')
                clear = ""
            out.append(f'<label>{e(label)}</label>{field}<small>{e(hint)}</small>{clear}')
        out.append("</section>")
    return form("/settings", "".join(out), "Save and apply")


def page_token(link=None):
    s = effective_settings()
    tok_ok, tok_msg = token_status(s["AUTH_USER"])
    missing = [k for k in ("TENANT_ID", "CLIENT_ID", "AUTH_USER") if not s[k]]
    if missing:
        return ('<section><h2>Microsoft 365 token</h2><p class="bad">Fill in '
                f'{", ".join(missing)} on the Settings page first.</p></section>')
    link = link or authorize_url(s)
    steps = f"""<section><h2>Microsoft 365 token</h2>
<p>Current token for <b>{e(s['AUTH_USER'])}</b>: <span class="{'ok' if tok_ok else 'bad'}">{e(tok_msg)}</span></p>
<ol>
<li>Open this link, preferably in a private window, and sign in as <b>{e(s['AUTH_USER'])}</b>
(not as a global administrator):<br><a href="{e(link)}" target="_blank" rel="noopener noreferrer">{e(link)}</a></li>
<li>Microsoft then shows a blank page. Copy the full address from the browser bar
(it starts with <code>{e(REDIRECT_URI)}?code=</code>) and paste it below.</li>
</ol>"""
    inner = ('<label>Resulting URL</label><textarea name="url" rows="4" required></textarea>')
    return steps + form("/token", inner, "Create token") + "</section>"


def page_test():
    s = effective_settings()
    inner = (f'<p>Sends a message from <b>{e(s["AUTH_USER"] or "(mailbox not set)")}</b> through the relay. '
             'Check the result on the Logs page.</p>'
             '<label>Recipient</label><input type="email" name="to" required>')
    return f"<section><h2>Send a test message</h2>{form('/test', inner, 'Send')}</section>"


def page_logs():
    return (f'<section><h2>Mail log <small style="display:inline">(last 200 lines, newest last)</small></h2>'
            f'<pre>{e(tail(MAILLOG))}</pre><a href="/logs">Refresh</a></section>')


# --- HTTP ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "postfix-365-webui"
    sys_version = ""

    def log_message(self, fmt, *args):
        print(f"[webui] {self.address_string()} {fmt % args}", flush=True)

    def authorized(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except ValueError:
            return False
        return (hmac.compare_digest(user.encode(), WEBUI_USER.encode())
                & hmac.compare_digest(password.encode(), WEBUI_PASSWORD.encode()))

    def send_html(self, content, status=200):
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def guard(self):
        if self.authorized():
            return True
        time.sleep(1)  # slows down password guessing
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="postfix-365", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self.guard():
            return
        path = urllib.parse.urlparse(self.path).path
        pages = {"/": page_status, "/settings": page_settings, "/token": page_token,
                 "/test": page_test, "/logs": page_logs}
        if path not in pages:
            return self.send_html(layout(path, "<section>Not found</section>"), 404)
        self.send_html(layout(path, pages[path]()))

    def do_POST(self):
        if not self.guard():
            return
        path = urllib.parse.urlparse(self.path).path
        length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
        data = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"),
                                     keep_blank_values=True)
        field = lambda name: data.get(name, [""])[0]
        if not hmac.compare_digest(field("csrf"), CSRF_TOKEN):
            return self.send_html(layout(path, "", "Form expired, reload the page and try again.", True), 400)

        if path == "/settings":
            current = effective_settings()
            values = {}
            for key, (_, _, kind, *_rest) in FIELD_INDEX.items():
                if kind == "bool":
                    values[key] = "true" if field(key) else "false"
                elif kind == "secret":
                    new = field(key)
                    values[key] = "" if field(key + "__clear") else (new if new else current[key])
                else:
                    values[key] = field(key).strip()
            errors = validate(values)
            if errors:
                return self.send_html(layout(path, page_settings(), "\n".join(errors), True), 400)
            ok, out = apply_settings(values)
            msg = ("Settings saved and Postfix reloaded.\n\n" if ok else "") + out
            return self.send_html(layout(path, page_settings(), msg, not ok))

        if path == "/token":
            settings = effective_settings()
            try:
                exchange_code(settings, field("url"))
            except (ValueError, OSError) as exc:
                return self.send_html(layout(path, page_token(), f"Token not created: {exc}", True))
            run([f"{SBIN}/postqueue", "-f"])
            return self.send_html(layout(path, page_token(),
                                         "Token created. Queued mail is being retried; check the Logs page."))

        if path == "/test":
            to = field("to").strip()
            if not re.fullmatch(RE_EMAIL, to):
                return self.send_html(layout(path, page_test(), "Invalid recipient address", True))
            code, out = send_test(effective_settings(), to)
            msg = "Message queued. Check the Logs page for delivery." if code == 0 else f"sendmail failed:\n{out}"
            return self.send_html(layout(path, page_test(), msg, code != 0))

        if path == "/flush":
            code, out = run([f"{SBIN}/postqueue", "-f"])
            return self.send_html(layout("/", page_status(), out or "Queue flush requested.", code != 0))

        self.send_html(layout(path, "<section>Not found</section>"), 404)


def main():
    if not WEBUI_PASSWORD:
        raise SystemExit("WEBUI_PASSWORD is not set; refusing to start the web UI")
    server = ThreadingHTTPServer((WEBUI_BIND, WEBUI_PORT), Handler)
    scheme = "http"
    if WEBUI_TLS:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(os.path.join(CERTS_DIR, "cert.pem"), os.path.join(CERTS_DIR, "key.pem"))
        # Handshake in the request thread, so one slow client cannot block accept().
        server.socket = context.wrap_socket(server.socket, server_side=True,
                                            do_handshake_on_connect=False)
        scheme = "https"
    print(f"[webui] listening on {scheme}://{WEBUI_BIND}:{WEBUI_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

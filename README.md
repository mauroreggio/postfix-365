# postfix-365

A Postfix relay in Docker that sends mail through **Microsoft 365** using **OAuth2**
(no basic authentication, no app passwords). Devices and applications on your network
(printers, scanners, NAS, scripts…) send plain SMTP to this container, and it relays
the mail to `smtp.office365.com` authenticated as a Microsoft 365 mailbox.

### USE IT AT YOUR OWN RISK

Adapted from https://github.com/takeyamajp/docker-postfix, with
[sasl-xoauth2](https://github.com/tarickb/sasl-xoauth2) providing the OAuth2 support.
The image is based on AlmaLinux 9.

## Quick start

1. Register an app in Microsoft Entra ID (see [Azure app registration](#azure-app-registration)).
2. Copy `docker-compose.yml`, fill in the values and set a `WEBUI_PASSWORD`.
3. Start it:
   ```bash
   docker compose up -d
   ```
4. Open the web UI at **https://localhost:8080** (self-signed certificate) and use the
   **Token** page to sign in with the Microsoft 365 mailbox. That creates the OAuth2
   token in `./tokens`. You only do this once: sasl-xoauth2 refreshes it automatically.
5. Use the **Test mail** page, then point your devices to this host on port 25.

Logs: `docker compose logs -f` (Postfix and sasl-xoauth2 messages).

## Configuration

Settings come from the `environment:` section of `docker-compose.yml`. Settings saved
from the web UI are stored in `./config/settings.env` and **take precedence** over
`docker-compose.yml`. Delete that file to go back to the compose values.

| Variable | Default | Description |
|---|---|---|
| `SMTP_HOSTNAME` | (none) | Host name of the relay, e.g. `smtp.example.com`. (`HOSTNAME` is still accepted but Docker sets it to the container id, so prefer `SMTP_HOSTNAME`.) |
| `DOMAIN_NAME` | (none) | Your mail domain, e.g. `example.com` |
| `MY_NETWORK` | (none) | Networks allowed to relay without authentication, comma separated (CIDR) |
| `MESSAGE_SIZE_LIMIT` | `10240000` | Maximum message size in bytes |
| `TIMEZONE` | `UTC` | e.g. `Europe/Rome` |
| `AUTH_USER` | (none) | Microsoft 365 mailbox used to send. Messages should use it (or an address it can *Send As*) as sender, otherwise Microsoft rejects them |
| `RELAY_HOST` / `RELAY_HOST_PORT` | `smtp.office365.com` / `587` | Microsoft 365 SMTP endpoint |
| `TENANT_ID` | (none) | Directory (tenant) ID |
| `CLIENT_ID` | (none) | Application (client) ID of the Entra ID app |
| `CLIENT_SECRET` | (empty) | Client secret, needed when the app is registered as a *Web* application |
| `SMTPD_USERS` | (empty) | Optional SMTP AUTH accounts for clients outside `MY_NETWORK`: `user@domain:password,other@domain:password` (passwords cannot contain commas). Authentication is only offered over TLS |
| `DISABLE_SMTP_AUTH_ON_PORT_25` | `false` | `true` = port 25 accepts only `MY_NETWORK`; ports 587/465 still authenticate |
| `SASL_XOAUTH2_DEBUG` | `false` | Full OAuth2 traces in the log (they can contain tokens: turn it off afterwards) |
| `WEBUI_ENABLED` | `false` | Start the web UI |
| `WEBUI_USER` / `WEBUI_PASSWORD` | `admin` / (none) | Web UI login. The UI does not start without a password |
| `WEBUI_PORT` | `8080` | Web UI port inside the container |
| `WEBUI_TLS` | `true` | Serve the UI over HTTPS with the certificate in `./certs` |

Volumes:

| Host | Container | Content |
|---|---|---|
| `./certs` | `/ssl_certs` | `cert.pem` and `key.pem` for incoming TLS (and the web UI). A self-signed pair is generated if missing |
| `./tokens` | `/etc/tokens` | OAuth2 token file, named after `AUTH_USER` |
| `./config` | `/config` | `settings.env` saved by the web UI |

These folders contain secrets and are ignored by git.

Ports: 25 (SMTP), 587 (submission, STARTTLS) and 465 (SMTPS) are configured inside
the container; publish 587/465 in `docker-compose.yml` only if you use `SMTPD_USERS`.

## Web UI

Enable it with `WEBUI_ENABLED: "true"` and a `WEBUI_PASSWORD`. Pages:

- **Status**: Postfix state, token state, queue, "retry queued mail now"
- **Settings**: edit the configuration; saving validates it, regenerates the Postfix
  configuration and reloads Postfix. If the new settings are rejected, the previous
  ones are restored
- **Token**: guided creation of the Microsoft 365 OAuth2 token (replaces running
  `sasl-xoauth2-tool` inside the container)
- **Test mail**: send a test message
- **Logs**: last lines of the mail log

The UI has full control of the relay. The default compose file publishes it on
`127.0.0.1` only. To reach it from other machines, change the port mapping and keep a
strong password, or put it behind a VPN or reverse proxy.

## Creating the token without the web UI

You can still use the sasl-xoauth2 tool inside the container:

```bash
docker exec -it postfix bash
```

```bash
sasl-xoauth2-tool get-token outlook /etc/tokens/example@example.com \
    --tenant=YOUR_TENANT_ID --client-id=YOUR_CLIENT_ID
```

Sign in with the mailbox account (`AUTH_USER`), **not** with a global administrator.
See https://github.com/tarickb/sasl-xoauth2#initial-access-token-2.

## Azure app registration

Follow https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app.

- Use any name you like.
- Select the appropriate account type ("organizational directory" for Microsoft 365).
- Add the platform "Web" with this redirect URI:
  `https://login.microsoftonline.com/common/oauth2/nativeclient`

<img width="949" height="422" alt="Redirect URI" src="https://github.com/user-attachments/assets/bd34cd18-898f-4c3d-a456-d9e72468a543" />

- Then adjust the app settings as follows:

<img width="1004" height="643" alt="App settings" src="https://github.com/user-attachments/assets/65cdb6dc-f50c-4c11-9118-e00c40627a4a" />

- Create a client secret (*Certificates & secrets*) and put its **value** in `CLIENT_SECRET`.
- Add the API permission **SMTP.Send**: *API permissions* → *Add a permission* →
  *Microsoft Graph* → *Delegated* → search "SMTP.Send" → check it.
- The mailbox must have **Authenticated SMTP** enabled (Microsoft 365 admin center →
  user → *Mail* → *Manage email apps*).

## Build and publish the image

The workflow **Build and publish Docker image** (`.github/workflows/docker-publish.yml`)
only runs manually: GitHub → *Actions* → *Build and publish Docker image* → *Run workflow*.
It asks for:

- **version**, e.g. `1.2.0` (a leading `v` is removed). The image is pushed as
  `ghcr.io/mauroreggio/postfix-365:<version>`
- **tag_latest**: also tag it as `latest`
- **platforms**: `linux/amd64`, `linux/arm64` or both
- **overwrite**: allow replacing an existing version (off by default)

To build locally:

```bash
docker build -t localhost/postfix-365:dev .
```

## Debug

```bash
docker exec -it postfix bash
```

Inside the container:

```bash
postfix status
mailq
postqueue -f
supervisorctl status
```

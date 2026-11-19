#!/bin/bash
# shellcheck disable=SC2016  # $mydomain, ${data_directory} are Postfix variables
# Generates the Postfix and sasl-xoauth2 configuration from the environment.
#
# Settings come from the container environment (docker-compose.yml) and, when
# present, from $SETTINGS_FILE (written by the web UI), which takes precedence.
#
# The script is idempotent: the entrypoint runs it at start-up and the web UI
# runs it again after settings are saved, followed by "postfix reload".
set -euo pipefail

SETTINGS_FILE="${SETTINGS_FILE:-/config/settings.env}"
TOKENS_DIR="${TOKENS_DIR:-/etc/tokens}"
CERTS_DIR="${CERTS_DIR:-/ssl_certs}"
XOAUTH2_CONF="${XOAUTH2_CONF:-/etc/sasl-xoauth2.conf}"

log()  { echo "[configure] $*"; }
warn() { echo "[configure] WARNING: $*" >&2; }
die()  { echo "[configure] ERROR: $*" >&2; exit 1; }

if [ -f "$SETTINGS_FILE" ]; then
  log "loading settings from $SETTINGS_FILE (overrides container environment)"
  set -a
  # shellcheck disable=SC1090
  . "$SETTINGS_FILE"
  set +a
fi

# --- defaults ---------------------------------------------------------------
# SMTP_HOSTNAME is preferred: Docker always sets HOSTNAME to the container id,
# so relying on it silently produces a random myhostname when it is not set.
SMTP_HOSTNAME="${SMTP_HOSTNAME:-${HOSTNAME:-}}"
TIMEZONE="${TIMEZONE:-UTC}"
DOMAIN_NAME="${DOMAIN_NAME:-}"
MY_NETWORK="${MY_NETWORK:-}"
MESSAGE_SIZE_LIMIT="${MESSAGE_SIZE_LIMIT:-10240000}"
AUTH_USER="${AUTH_USER:-}"
RELAY_HOST="${RELAY_HOST:-smtp.office365.com}"
RELAY_HOST_PORT="${RELAY_HOST_PORT:-587}"
CLIENT_ID="${CLIENT_ID:-}"
CLIENT_SECRET="${CLIENT_SECRET:-}"
TENANT_ID="${TENANT_ID:-}"
SMTPD_USERS="${SMTPD_USERS:-}"
DISABLE_SMTP_AUTH_ON_PORT_25="${DISABLE_SMTP_AUTH_ON_PORT_25:-false}"
SASL_XOAUTH2_DEBUG="${SASL_XOAUTH2_DEBUG:-false}"

# --- validation -------------------------------------------------------------
# Values end up in Postfix maps, a JSON file and file names: reject anything
# that could break those formats instead of writing a broken configuration.
check() { # check NAME REGEX DESCRIPTION
  local value="${!1}"
  [[ "$value" =~ $2 ]] || die "$1='$value' is not valid: expected $3"
}
is_true() { [ "${1,,}" = "true" ] || [ "${1,,}" = "yes" ] || [ "$1" = "1" ]; }

re_host='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'
re_email='^[A-Za-z0-9._%+-]+@[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'
re_bool='^([Tt][Rr][Uu][Ee]|[Ff][Aa][Ll][Ss][Ee]|[Yy][Ee][Ss]|[Nn][Oo]|0|1)$'

check SMTP_HOSTNAME "$re_host" "a host name such as smtp.example.com"
[[ "$SMTP_HOSTNAME" == *.* ]] || warn "SMTP_HOSTNAME='$SMTP_HOSTNAME' is not a fully qualified name; set SMTP_HOSTNAME"
check DOMAIN_NAME "$re_host" "a domain such as example.com"
check AUTH_USER "$re_email" "the Microsoft 365 mailbox, e.g. user@example.com"
check RELAY_HOST "$re_host" "a host name"
check RELAY_HOST_PORT '^[0-9]{1,5}$' "a port number"
check MESSAGE_SIZE_LIMIT '^[0-9]+$' "a size in bytes"
check MY_NETWORK '^[][0-9A-Fa-f:./, ]*$' "networks in CIDR notation, comma or space separated"
check CLIENT_ID '^[A-Za-z0-9-]+$' "the Application (client) ID"
check TENANT_ID '^[A-Za-z0-9.-]+$' "the Directory (tenant) ID or tenant domain"
check DISABLE_SMTP_AUTH_ON_PORT_25 "$re_bool" "true or false"
check SASL_XOAUTH2_DEBUG "$re_bool" "true or false"
[[ "$CLIENT_SECRET" != *$'\n'* ]] || die "CLIENT_SECRET must be a single line"

# --- time zone --------------------------------------------------------------
if [ -e "/usr/share/zoneinfo/$TIMEZONE" ]; then
  ln -fs "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
else
  warn "unknown TIMEZONE '$TIMEZONE', using UTC"
  ln -fs /usr/share/zoneinfo/UTC /etc/localtime
fi

# --- TLS certificate for incoming connections ---------------------------------
mkdir -p "$CERTS_DIR"
if [ ! -s "$CERTS_DIR/cert.pem" ] || [ ! -s "$CERTS_DIR/key.pem" ]; then
  log "no certificate in $CERTS_DIR, generating a self-signed one for $SMTP_HOSTNAME"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj "/CN=$SMTP_HOSTNAME" \
    -keyout "$CERTS_DIR/key.pem" -out "$CERTS_DIR/cert.pem" 2>/dev/null
fi
chmod 600 "$CERTS_DIR/key.pem"

# --- main.cf ----------------------------------------------------------------
# postconf -e edits the stock main.cf in place, keeping the distribution
# defaults and writing each value as one argument (no shell/echo quoting).
networks="127.0.0.0/8"
[ -n "${MY_NETWORK// /}" ] && networks="$networks, $MY_NETWORK"

postconf -e \
  "myhostname = $SMTP_HOSTNAME" \
  "mydomain = $DOMAIN_NAME" \
  'myorigin = $mydomain' \
  'mydestination = $myhostname' \
  "mynetworks = $networks" \
  "inet_interfaces = all" \
  "inet_protocols = ipv4" \
  "disable_vrfy_command = yes" \
  "smtpd_helo_required = yes" \
  'smtpd_banner = $myhostname ESMTP' \
  "message_size_limit = $MESSAGE_SIZE_LIMIT" \
  "alias_database = hash:/etc/aliases" \
  "alias_maps = hash:/etc/aliases" \
  "relayhost = [$RELAY_HOST]:$RELAY_HOST_PORT" \
  "smtpd_relay_restrictions = permit_mynetworks, permit_sasl_authenticated, defer_unauth_destination" \
  "smtp_sasl_auth_enable = yes" \
  "smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd" \
  "smtp_sasl_security_options =" \
  "smtp_sasl_mechanism_filter = xoauth2" \
  "smtp_tls_security_level = encrypt" \
  "smtp_tls_CAfile = /etc/pki/tls/certs/ca-bundle.crt" \
  'smtp_tls_session_cache_database = btree:${data_directory}/smtp_scache' \
  "smtpd_tls_cert_file = $CERTS_DIR/cert.pem" \
  "smtpd_tls_key_file = $CERTS_DIR/key.pem" \
  "smtpd_tls_security_level = may" \
  "smtpd_tls_auth_only = yes" \
  'smtpd_tls_session_cache_database = btree:${data_directory}/smtpd_scache'

newaliases

# --- outgoing authentication (Microsoft 365 via XOAUTH2) ----------------------
# The "password" field is the path of the token file used by sasl-xoauth2.
umask 077
printf '[%s]:%s %s:%s/%s\n' "$RELAY_HOST" "$RELAY_HOST_PORT" \
  "$AUTH_USER" "$AUTH_USER" "$TOKENS_DIR" > /etc/postfix/sasl_passwd
postmap /etc/postfix/sasl_passwd
umask 022

# sasl-xoauth2 refreshes the access token and rewrites the file, so the
# directory must be writable by the postfix user.
mkdir -p "$TOKENS_DIR"
chown -R postfix:postfix "$TOKENS_DIR"
chmod 700 "$TOKENS_DIR"
find "$TOKENS_DIR" -type f -exec chmod 600 {} +
if [ ! -s "$TOKENS_DIR/$AUTH_USER" ]; then
  warn "no token file $TOKENS_DIR/$AUTH_USER: mail will stay queued until you create it"
  warn "(web UI \"Token\" page, or sasl-xoauth2-tool get-token outlook, see README)"
fi

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\t'/\\t}"; s="${s//$'\r'/\\r}"
  printf '%s' "$s"
}
debug=no
is_true "$SASL_XOAUTH2_DEBUG" && debug=yes
cat > "$XOAUTH2_CONF.tmp" <<EOF
{
  "client_id": "$(json_escape "$CLIENT_ID")",
  "client_secret": "$(json_escape "$CLIENT_SECRET")",
  "token_endpoint": "https://login.microsoftonline.com/$TENANT_ID/oauth2/v2.0/token",
  "log_to_syslog_on_failure": "yes",
  "log_full_trace_on_failure": "$debug",
  "always_log_to_syslog": "$debug"
}
EOF
# Read by the smtp process, which runs as postfix; keep the secret away from others.
chown root:postfix "$XOAUTH2_CONF.tmp"
chmod 640 "$XOAUTH2_CONF.tmp"
mv -f "$XOAUTH2_CONF.tmp" "$XOAUTH2_CONF"

# --- incoming authentication (optional) -----------------------------------------
# SMTPD_USERS="user@example.com:password,other@example.com:password"
# Without users, only clients in MY_NETWORK can relay.
SASLDB=/etc/sasldb2
rm -f "$SASLDB"
smtpd_auth=no
if [ -n "${SMTPD_USERS// /}" ]; then
  IFS=',' read -ra entries <<< "$SMTPD_USERS"
  for entry in "${entries[@]}"; do
    entry="${entry#"${entry%%[![:space:]]*}"}"   # trim leading spaces
    entry="${entry%"${entry##*[![:space:]]}"}"   # trim trailing spaces
    [ -z "$entry" ] && continue
    user="${entry%%:*}"; pass="${entry#*:}"
    [[ "$entry" == *:* && -n "$pass" ]] || die "SMTPD_USERS entry '$user' has no password (format user@domain:password)"
    [[ "$user" =~ $re_email ]] || die "SMTPD_USERS user '$user' must be in the form user@domain"
    printf '%s' "$pass" | saslpasswd2 -f "$SASLDB" -p -c -u "${user#*@}" "${user%@*}"
  done
  chown root:postfix "$SASLDB"
  chmod 640 "$SASLDB"
  printf 'pwcheck_method: auxprop\nauxprop_plugin: sasldb\nsasldb_path: %s\nmech_list: PLAIN LOGIN\n' "$SASLDB" > /etc/sasl2/smtpd.conf
  smtpd_auth=yes
fi

port25_auth="$smtpd_auth"
is_true "$DISABLE_SMTP_AUTH_ON_PORT_25" && port25_auth=no
postconf -e \
  "smtpd_sasl_auth_enable = $port25_auth" \
  "smtpd_sasl_type = cyrus" \
  "smtpd_sasl_path = smtpd" \
  "smtpd_sasl_security_options = noanonymous"

# --- master.cf: submission (587, STARTTLS) and smtps (465, implicit TLS) --------
# They only matter if the ports are published in docker-compose.yml.
postconf -M "submission/inet=submission inet n - n - - smtpd"
postconf -M "smtps/inet=smtps inet n - n - - smtpd"
for svc in submission smtps; do
  postconf -P \
    "$svc/inet/syslog_name=postfix/$svc" \
    "$svc/inet/smtpd_sasl_auth_enable=$smtpd_auth" \
    "$svc/inet/smtpd_relay_restrictions=permit_sasl_authenticated,permit_mynetworks,reject"
done
postconf -P "submission/inet/smtpd_tls_security_level=encrypt"
postconf -P "smtps/inet/smtpd_tls_wrappermode=yes"

postfix check
log "configuration ready: relay [$RELAY_HOST]:$RELAY_HOST_PORT as $AUTH_USER, incoming auth: $smtpd_auth (port 25: $port25_auth)"

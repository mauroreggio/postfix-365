# syntax=docker/dockerfile:1
# AlmaLinux major version: rebuilding picks up the latest 9.x security updates.
ARG ALMALINUX_VERSION=9
FROM almalinux:${ALMALINUX_VERSION}

ARG VERSION=dev
LABEL org.opencontainers.image.title="postfix-365" \
      org.opencontainers.image.description="Postfix relay for Microsoft 365 with OAuth2 (sasl-xoauth2)" \
      org.opencontainers.image.source="https://github.com/mauroreggio/postfix-365" \
      org.opencontainers.image.version="${VERSION}"

# One layer: Postfix, SASL, sasl-xoauth2 (EPEL), rsyslog, supervisor, python3 (web UI).
RUN dnf -y install epel-release dnf-plugins-core \
 && dnf config-manager --set-enabled crb \
 && dnf -y install --setopt=install_weak_deps=False \
      postfix cyrus-sasl cyrus-sasl-plain cyrus-sasl-md5 sasl-xoauth2 \
      rsyslog supervisor openssl python3 tzdata ca-certificates \
 && dnf clean all && rm -rf /var/cache/dnf \
 # rsyslog: listen on /dev/log, no journald inside a container
 && sed -i 's/\(SysSock\.Use\)="off"/\1="on"/' /etc/rsyslog.conf \
 && sed -i 's/^\(module.*load.*imjournal.*\)/#\1/' /etc/rsyslog.conf \
 && sed -i 's/^\(.*StateFile.*imjournal.*\)/#\1/' /etc/rsyslog.conf \
 # supervisor runs as root by design (it starts postfix and rsyslog)
 && sed -i 's/^;\(user\)=chrism/\1=root/' /etc/supervisord.conf \
 && mkdir -p /ssl_certs /etc/tokens /config

COPY rootfs/ /
COPY scripts/ /scripts/
COPY webui/ /webui/
RUN chmod 755 /scripts/*.sh /webui/app.py

# 25 SMTP, 587 submission, 465 smtps, 8080 web UI (only if WEBUI_ENABLED=true)
EXPOSE 25 587 465 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD /usr/sbin/postfix status >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/scripts/entrypoint.sh"]
CMD ["supervisord", "-n", "-c", "/etc/supervisord.conf"]

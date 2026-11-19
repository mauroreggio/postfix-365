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

---

## Guida completa all'installazione (italiano)

Segui le fasi **nell'ordine indicato**: ogni fase dà per scontato che le precedenti siano completate.
Nei comandi sostituisci i valori di esempio (`example.com`, `192.168.0.10`, ecc.) con i tuoi.

### Fase 1 – Prerequisiti del server

- **Sistema**: un server o una VM Linux (Debian, Ubuntu, AlmaLinux, Rocky…) sempre acceso, con un **indirizzo IP fisso** nella rete locale (es. `192.168.0.10`). Docker Desktop su Windows/macOS funziona per delle prove, ma per l'uso in produzione è sconsigliato (vedi Fase 12, indirizzo IP dei client).
- **Docker Engine e plugin Compose**: installa Docker dalla documentazione ufficiale (https://docs.docker.com/engine/install/) e verifica:
  ```bash
  docker version
  ```
  ```bash
  docker compose version
  ```
  Serve **Compose v2** (comando `docker compose`, con lo spazio). Il vecchio `docker-compose` v1 non è più supportato.
- **Permessi**: esegui i comandi come root oppure aggiungi il tuo utente al gruppo `docker` (`sudo usermod -aG docker $USER`, poi esci e rientra). Il gruppo `docker` equivale di fatto ai permessi di root: dallo solo a chi amministra il server.
- **Porta 25 libera sull'host**: molte distribuzioni installano già un server di posta (Postfix, Exim, Sendmail) che occupa la porta 25. Controlla:
  ```bash
  sudo ss -ltnp | grep ':25 '
  ```
  Se compare un processo, fermalo e disabilitalo (es. `sudo systemctl disable --now postfix` oppure `exim4`), altrimenti il container non parte con l'errore `port is already allocated` / `address already in use`.
- **Rete in uscita**: il server deve poter raggiungere `smtp.office365.com` sulla porta **587** e `login.microsoftonline.com` sulla porta **443**. Se c'è un firewall o un proxy aziendale, apri queste destinazioni. La porta 25 **in uscita** non serve.
- **Orologio**: data e ora del server devono essere corrette (NTP attivo), altrimenti TLS e OAuth2 falliscono. Controlla con `timedatectl`.
- **Host con SELinux attivo** (RHEL, AlmaLinux, Rocky, Fedora): i volumi montati devono avere l'etichetta `:Z`, altrimenti il container non può leggerli né scriverli (errori `Permission denied`). Nel `docker-compose.yml` scrivi ad esempio `./tokens:/etc/tokens:Z` (vale per tutti e tre i volumi).

### Fase 2 – Preparare Microsoft 365 (casella di posta)

- **Scegli la casella che invierà la posta** (es. `relay@example.com`). Deve essere una casella Exchange Online con licenza, oppure una casella condivisa abilitata all'accesso. Sarà il valore di `AUTH_USER`.
- **Abilita "SMTP autenticato" sulla casella**: interfaccia di amministrazione di Microsoft 365 → *Utenti* → *Utenti attivi* → seleziona la casella → scheda *Posta* → *Gestisci le app di posta elettronica* → spunta **SMTP autenticato** → *Salva*. Il cambiamento può richiedere fino a un'ora.
- **Controlla che SMTP AUTH non sia disabilitato per tutto il tenant**: se è disabilitato a livello di organizzazione, l'impostazione della singola casella vince solo se è esplicitamente attiva. Da PowerShell Exchange Online puoi verificarlo con `Get-TransportConfig | Format-List SmtpClientAuthenticationDisabled`.
- **Criteri di accesso condizionale**: se nel tenant ci sono regole di *Accesso condizionale* (es. MFA obbligatoria da posizioni sconosciute, blocco di paesi), verifica che non blocchino l'accesso della casella dall'IP pubblico del server. L'accesso iniziale per creare il token (Fase 9) avviene invece dal tuo browser.
- **Indirizzo del mittente**: Microsoft 365 accetta solo messaggi il cui mittente è `AUTH_USER` oppure un indirizzo su cui la casella ha il permesso **Invia come** (*Send As*). Se stampanti o applicazioni usano altri mittenti, assegna il permesso *Invia come* alla casella oppure configura i dispositivi con `AUTH_USER` come mittente. In caso contrario la posta viene rifiutata con `554 5.2.252 SendAsDenied`.

### Fase 3 – Registrare l'applicazione in Microsoft Entra ID

- Vai su https://entra.microsoft.com → *Identità* → *Applicazioni* → *Registrazioni app* → **Nuova registrazione**.
  - **Nome**: quello che preferisci (es. `postfix-365`).
  - **Tipi di account supportati**: *Solo account in questa directory organizzativa*.
  - **URI di reindirizzamento**: piattaforma **Web**, valore esatto `https://login.microsoftonline.com/common/oauth2/nativeclient`.
  - Clicca *Registra*.
- Dalla pagina *Panoramica* dell'app copia:
  - **ID applicazione (client)** → sarà `CLIENT_ID`;
  - **ID della directory (tenant)** → sarà `TENANT_ID`.
- **Segreto client**: *Certificati e segreti* → *Segreti client* → **Nuovo segreto client** → scegli la scadenza → *Aggiungi*. Copia subito il campo **Valore** (non l'*ID segreto*): dopo aver lasciato la pagina non è più visibile. Sarà `CLIENT_SECRET`.
  - **Annota la data di scadenza** del segreto: quando scade, il rinnovo automatico del token smette di funzionare e la posta resta in coda (errore `AADSTS7000222`). Prima della scadenza crea un nuovo segreto e aggiornalo dalla pagina *Settings* dell'interfaccia web.
- **Autorizzazioni API**: *Autorizzazioni API* → *Aggiungi un'autorizzazione* → *Microsoft Graph* → *Autorizzazioni delegate* → cerca `SMTP.Send` → spuntala → *Aggiungi autorizzazioni*. Aggiungi allo stesso modo anche `offline_access` (necessaria per ottenere il token di rinnovo).
- **Consenso amministratore**: sempre in *Autorizzazioni API*, clicca **Concedi consenso amministratore per <tenant>** (serve un amministratore). Senza consenso il primo accesso può fallire con `AADSTS65001`.
- Se nelle impostazioni *Autenticazione* dell'app vedi l'opzione **Consenti flussi client pubblici**, lasciala su *No*: questo progetto usa il flusso con segreto client.

### Fase 4 – Pubblicare l'immagine Docker (una volta per ogni nuova versione)

- **Abilita GitHub Actions** nel repository: *Settings* → *Actions* → *General* → **Allow all actions and reusable workflows** → *Save*. Senza questo passaggio la scheda *Actions* non compare.
- **Dai al workflow il permesso di scrivere il pacchetto** (necessario se il pacchetto `postfix-365` esisteva già, ad esempio perché pubblicato a mano):
  - apri https://github.com/users/mauroreggio/packages/container/postfix-365/settings;
  - nel riquadro **Manage Actions access** clicca **Add Repository**, scegli `postfix-365` e conferma;
  - nella riga del repository cambia il ruolo da *Read* a **Write** (si salva da solo).
  Senza questo passaggio il workflow fallisce con `permission_denied: write_package`.
- **Avvia il workflow**: scheda *Actions* → *Build and publish Docker image* → **Run workflow** → inserisci la versione (es. `2.0.0`), lascia *tag_latest* attivo → *Run workflow*. Attendi la spunta verde.
- **Visibilità del pacchetto**: nella stessa pagina delle impostazioni del pacchetto, in fondo (*Danger Zone* → *Change visibility*), decidi se l'immagine è **pubblica** o **privata**. Se è privata, il server deve autenticarsi prima di scaricarla (Fase 7).
- **In alternativa** puoi costruire l'immagine direttamente sul server (vedi Fase 6, opzione B), senza usare GitHub.

### Fase 5 – Preparare la cartella sul server

- Scegli una cartella di lavoro, ad esempio `/opt/postfix-365`, e crea le sottocartelle per i dati persistenti:
  ```bash
  sudo mkdir -p /opt/postfix-365/certs /opt/postfix-365/tokens /opt/postfix-365/config
  ```
  ```bash
  sudo chmod 700 /opt/postfix-365/certs /opt/postfix-365/tokens /opt/postfix-365/config
  ```
- Contenuto delle cartelle (tutte contengono **segreti**: non condividerle e non metterle in un repository git):
  - `certs/`: `cert.pem` e `key.pem` usati per TLS in ingresso e dall'interfaccia web. Se mancano, al primo avvio viene creato un certificato autofirmato. Se hai un certificato valido per `SMTP_HOSTNAME` (es. da Let's Encrypt), copialo qui con questi due nomi prima del primo avvio.
  - `tokens/`: il file del token OAuth2, con lo stesso nome di `AUTH_USER`. Il container ne cambia automaticamente proprietario e permessi.
  - `config/`: `settings.env`, scritto dall'interfaccia web.
- Scarica il file `docker-compose.yml` del repository nella cartella:
  ```bash
  cd /opt/postfix-365 && sudo curl -fsSLO https://raw.githubusercontent.com/mauroreggio/postfix-365/main/docker-compose.yml
  ```
  (Oppure clona tutto il repository con `git clone`, utile se vuoi costruire l'immagine in locale.)

### Fase 6 – Compilare `docker-compose.yml`

- **Immagine**:
  - **opzione A (consigliata)**: usa l'immagine pubblicata e **indica una versione precisa** al posto di `latest`, ad esempio `image: ghcr.io/mauroreggio/postfix-365:2.0.0`. Così un riavvio non installa mai una versione nuova senza che tu lo decida;
  - **opzione B**: costruisci l'immagine sul server. Clona il repository, commenta la riga `image:` e togli il commento a `build: .`.
- **Porte** (`ports:`), nel formato `IP_HOST:PORTA_HOST:PORTA_CONTAINER`:
  - `25:25` serve sempre. Per esporla solo sulla rete interna scrivi l'IP del server: `"192.168.0.10:25:25"`;
  - `587:587` e `465:465` togli il commento solo se usi `SMTPD_USERS` (client esterni con utente e password);
  - `127.0.0.1:8080:8080` è l'interfaccia web, raggiungibile solo dal server stesso. Lasciala così e usa un tunnel SSH (Fase 8).
- **Variabili** (`environment:`), da compilare tutte prima del primo avvio:
  - `SMTP_HOSTNAME`: nome completo del relay (es. `smtp.example.com`). Non usare `HOSTNAME`: Docker lo imposta da solo all'ID del container;
  - `DOMAIN_NAME`: il tuo dominio (es. `example.com`);
  - `MY_NETWORK`: le reti che possono inviare **senza password**, separate da virgola (es. `192.168.0.0/24, 10.10.0.0/16`). Metti solo le reti dei tuoi dispositivi, **mai** `0.0.0.0/0`: diventeresti un open relay;
  - `TIMEZONE`: es. `Europe/Rome`;
  - `MESSAGE_SIZE_LIMIT`: dimensione massima in byte. Microsoft 365 accetta al massimo circa 35 MB per messaggio, ma i limiti della casella possono essere più bassi;
  - `AUTH_USER`, `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`: i valori raccolti nelle Fasi 2 e 3;
  - `SMTPD_USERS`: lascialo vuoto se tutti i dispositivi sono in `MY_NETWORK`. Altrimenti scrivi `utente@dominio:password`, separando più utenti con la virgola (la password non può contenere virgole né spazi iniziali o finali);
  - `DISABLE_SMTP_AUTH_ON_PORT_25`: `true` se vuoi che sulla porta 25 entrino solo le reti di `MY_NETWORK`;
  - `SASL_XOAUTH2_DEBUG`: lascialo `false`. Attivalo solo per diagnosticare un problema e poi disattivalo, perché scrive i token nei log;
  - `WEBUI_ENABLED: "true"` e `WEBUI_PASSWORD`: una password lunga e casuale. Senza password l'interfaccia web non parte.
- **Volumi**: se la cartella non è quella del file compose, usa percorsi assoluti (`/opt/postfix-365/tokens:/etc/tokens`). Su host con SELinux aggiungi `:Z` (Fase 1).
- **Proteggi il file**: contiene il segreto client e la password dell'interfaccia web:
  ```bash
  sudo chmod 600 /opt/postfix-365/docker-compose.yml
  ```
  In alternativa puoi spostare i valori sensibili in un file `.env` nella stessa cartella (anch'esso con `chmod 600`) e richiamarli nel compose con `${CLIENT_SECRET}`.
- **Controlla la sintassi** prima di avviare:
  ```bash
  cd /opt/postfix-365 && docker compose config --quiet && echo OK
  ```

### Fase 7 – Primo avvio

- Se il pacchetto è **privato**, accedi prima al registro con un *Personal access token* (classico) di GitHub con il solo permesso `read:packages`:
  ```bash
  docker login ghcr.io -u mauroreggio
  ```
  (alla richiesta della password incolla il token).
- Scarica l'immagine e avvia il container in background:
  ```bash
  cd /opt/postfix-365 && docker compose pull && docker compose up -d
  ```
  (con l'opzione B usa `docker compose up -d --build`).
- Controlla i messaggi di avvio:
  ```bash
  docker compose logs -f
  ```
  Devi vedere `[configure] configuration ready…` e l'avviso che manca il file del token: è normale, lo crei nella Fase 9. Se invece compare `[configure] ERROR: …`, il messaggio indica quale variabile correggere. Correggila e ripeti `docker compose up -d`. Esci dai log con `Ctrl+C` (il container continua a funzionare).
- Controlla lo stato: dopo circa un minuto la colonna *STATUS* deve indicare `healthy`.
  ```bash
  docker compose ps
  ```

### Fase 8 – Accedere all'interfaccia web

- Dal **server stesso**: apri `https://127.0.0.1:8080`.
- Da **un altro computer** (caso tipico) crea un tunnel SSH. Lascia aperto questo comando sul tuo PC:
  ```bash
  ssh -L 8080:127.0.0.1:8080 utente@192.168.0.10
  ```
  poi apri nel browser del PC `https://localhost:8080`.
- Il browser segnala che il certificato non è attendibile perché è autofirmato: è previsto. Scegli *Avanzate* → *Procedi*.
- Accedi con `WEBUI_USER` (predefinito `admin`) e `WEBUI_PASSWORD`.
- Sconsigliato: per rendere l'interfaccia raggiungibile direttamente dalla rete, cambia la porta in `"192.168.0.10:8080:8080"`. Fallo solo in una rete fidata e con una password robusta: chi accede all'interfaccia controlla completamente il relay.

### Fase 9 – Creare il token OAuth2 (una sola volta)

- Nell'interfaccia apri la pagina **Settings** e verifica che `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET` e `AUTH_USER` siano corretti.
- Apri la pagina **Token** e clicca sul link mostrato, possibilmente in una **finestra in incognito**, così non usi per errore l'account con cui sei già collegato.
- Accedi con la **casella di `AUTH_USER`**, non con un amministratore globale. Se richiesto, accetta le autorizzazioni.
- Alla fine Microsoft mostra una **pagina bianca**: è normale. Copia l'indirizzo completo dalla barra del browser. Inizia con `https://login.microsoftonline.com/common/oauth2/nativeclient?code=…`.
- Incollalo nella pagina **Token** e clicca **Create token** **entro pochi minuti**: il codice scade in fretta e si può usare una volta sola. Se ricevi un errore, riapri il link dalla pagina (il link vecchio non vale più) e ripeti.
- Messaggi di errore frequenti:
  - `AADSTS50011`: l'URI di reindirizzamento dell'app non corrisponde (Fase 3);
  - `AADSTS7000215` o `AADSTS7000222`: il segreto client è sbagliato (hai copiato l'*ID* invece del *Valore*) oppure è scaduto;
  - `AADSTS65001`: manca il consenso dell'amministratore.
- Dopo la creazione la pagina **Status** mostra *Token: present*. Da qui in poi sasl-xoauth2 rinnova il token automaticamente. Ripeti questa fase solo se il token viene revocato, se cambi `AUTH_USER` o se resti senza invii per molto tempo (il token di rinnovo scade dopo circa 90 giorni di inattività).

### Fase 10 – Verificare l'invio

- Pagina **Test mail**: invia un messaggio a un tuo indirizzo, anche esterno all'organizzazione.
- Pagina **Logs**, oppure `docker compose logs -f`: cerca una riga con `status=sent (250 2.0.0 OK …)`.
- Errori frequenti nei log:
  - `535 5.7.139 Authentication unsuccessful`: SMTP autenticato non è abilitato sulla casella o sul tenant (Fase 2), oppure manca il consenso all'autorizzazione `SMTP.Send`;
  - `554 5.2.252 SendAsDenied`: il mittente non è `AUTH_USER` e la casella non ha il permesso *Invia come* (Fase 2);
  - `SASL authentication failed` con un problema di token: ricrea il token (Fase 9) e, se serve, attiva per poco tempo `SASL_XOAUTH2_DEBUG`.
- Dopo aver corretto un problema, clicca **Retry queued mail now** nella pagina **Status** per inviare subito la posta rimasta in coda.

### Fase 11 – Configurare i dispositivi e le applicazioni

- **Server SMTP**: l'IP o il nome DNS del server Docker (es. `192.168.0.10`), **porta 25**.
- **Autenticazione**: nessuna, se il dispositivo è in `MY_NETWORK`. In caso contrario usa porta **587** con STARTTLS (o **465** con SSL/TLS) e un utente di `SMTPD_USERS`.
- **Crittografia**: sulla porta 25 è facoltativa (STARTTLS disponibile). I dispositivi più vecchi possono non accettare il certificato autofirmato: in quel caso disattiva il controllo del certificato sul dispositivo oppure installa in `certs/` un certificato valido.
- **Mittente**: usa `AUTH_USER` oppure un indirizzo per cui la casella ha il permesso *Invia come* (Fase 2).
- Se lo vuoi, crea nel DNS interno un record `A` per `SMTP_HOSTNAME` che punta al server, così i dispositivi possono usare il nome invece dell'IP.

### Fase 12 – Considerazioni su Docker e sicurezza

- **Docker scavalca il firewall dell'host**: le porte pubblicate con `ports:` vengono aperte direttamente da Docker con regole iptables/nftables proprie e **non sono filtrate da `ufw` né, in molti casi, da `firewalld`**. Per limitare l'esposizione indica sempre l'IP su cui pubblicare (es. `"192.168.0.10:25:25"`) e filtra a monte (router, firewall di rete). **Non esporre mai la porta 25 di questo container su Internet.**
- **Indirizzo IP dei client**: Postfix decide chi può inviare in base all'IP sorgente. Su Linux, con la configurazione predefinita di Docker, l'IP reale del client viene mantenuto. Se nei log vedi invece connessioni provenienti da un indirizzo `172.x.x.x` (il gateway Docker), la connessione passa dal proxy di Docker: succede con Docker Desktop, con alcune connessioni IPv6 o con la rete in modalità *rootless*. In questi casi **non** aggiungere la rete `172.x` a `MY_NETWORK`, altrimenti chiunque raggiunga la porta potrebbe inviare. Usa invece `network_mode: "host"` (solo Linux, togliendo la sezione `ports:`) oppure `SMTPD_USERS`.
- **Precedenza delle impostazioni**: dopo il primo salvataggio dall'interfaccia web, i valori in `config/settings.env` **hanno la precedenza** su quelli di `docker-compose.yml`. Se modifichi il compose e non vedi effetti, cambia il valore dall'interfaccia oppure cancella `config/settings.env` e riavvia. Le variabili `WEBUI_*` si leggono solo dal compose.
- **Riavvio automatico**: `restart: unless-stopped` fa ripartire il container dopo un riavvio del server, purché il servizio Docker sia abilitato all'avvio (`sudo systemctl enable docker`).
- **Log**: il compose limita i log di Docker a 3 file da 10 MB (`logging:`). Il log interno `/var/log/maillog` viene azzerato a ogni riavvio del container.
- **Coda di posta**: la coda di Postfix è **dentro** il container. Se ricrei il container (`docker compose down` seguito da `up`, un aggiornamento o qualsiasi modifica a `docker-compose.yml` seguita da `up -d`) i messaggi non ancora consegnati vanno persi. Prima di aggiornare controlla nella pagina **Status** che la coda sia vuota.
- **Backup**: salva periodicamente `docker-compose.yml`, `.env` (se lo usi), `config/`, `tokens/` e `certs/`. Con queste cartelle puoi ricreare il servizio identico su un altro server.
- **Segreti**: il file del token permette di inviare posta come `AUTH_USER` senza password. Proteggilo come una password. Se pensi che sia stato copiato, revoca le sessioni della casella in Entra ID (*Utenti* → casella → *Revoca sessioni*) e ricrea il token.

### Fase 13 – Aggiornamenti e manutenzione

- **Nuova versione dell'immagine**: pubblicala (Fase 4), poi nel compose cambia il tag (es. `2.0.0` → `2.1.0`) e applica:
  ```bash
  cd /opt/postfix-365 && docker compose pull && docker compose up -d
  ```
  I dati in `certs/`, `tokens/` e `config/` vengono mantenuti.
- **Ritorno alla versione precedente**: rimetti il tag precedente nel compose e ripeti lo stesso comando.
- **Aggiornamenti di sicurezza del sistema base**: ripubblica periodicamente l'immagine (es. ogni mese) con una nuova versione. Il workflow riparte da AlmaLinux 9 aggiornato.
- **Scadenza del segreto client**: prima della data annotata nella Fase 3 crea un nuovo segreto in Entra ID e inseriscilo nella pagina **Settings**. Il token non va ricreato.
- **Pulizia delle immagini vecchie**:
  ```bash
  docker image prune
  ```
- **Comandi utili**:
  - stato del container: `docker compose ps`;
  - log in tempo reale: `docker compose logs -f`;
  - shell nel container: `docker exec -it postfix bash`;
  - coda di posta: `docker exec postfix mailq`;
  - riprova subito la coda: `docker exec postfix postqueue -f`;
  - riavvio: `docker compose restart`;
  - arresto (la coda viene persa): `docker compose down`.

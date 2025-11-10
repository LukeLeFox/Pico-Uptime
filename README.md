# Pico Uptime — Mini Monitor di Rete per Raspberry Pi Pico W

[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20Pico%20W-orange.svg)]()
[![Language](https://img.shields.io/badge/language-MicroPython-blue.svg)]()
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)]()
[![Status](https://img.shields.io/badge/status-beta-lightgrey.svg)]()

Pico Uptime è un mini sistema di monitoraggio scritto in MicroPython che trasforma un Raspberry Pi Pico W in un uptime monitor web-based. Leggero, autonomo e configurabile direttamente da browser — senza ricompilare il codice.

---

## Funzionalità principali

- ✅ **Monitoraggio multiprotocollo**
  - HTTP / HTTPS (codici di stato)
  - TCP (connessione socket)
  - Ping ICMP
- 🖥️ **Interfaccia Web integrata**
  - Dashboard con stato in tempo reale
  - Aggiunta / rimozione target via browser
  - Gestione modalità silenziosa per singolo target
- 📲 **Notifiche Telegram**
  - Messaggi per transizioni UP/DOWN
  - Modalità silenziosa
  - Configurazione da pagina `/telegram`
- 🛠️ **Configurazione persistente**
  - File JSON:
    - `targets.json`
    - `config.json`
    - `telegram.json`
- ♻️ **Auto-protezione**
  - Riavvio automatico se il Wi-Fi resta down per troppo tempo
  - Pulsante reboot da UI
- 🔐 **Sicurezza**
  - Accesso con Basic Auth
  - Protezione tentativi multipli errati

---

## Struttura dei file

| File            | Descrizione                               |
|----------------|-------------------------------------------|
| `main.py`      | Codice principale (server web + monitor)  |
| `targets.json` | Lista target monitorati                   |
| `config.json`  | Intervallo/soglie di polling              |
| `telegram.json`| Config Telegram (token, chat_id, enable)  |

I file JSON possono essere preparati a mano oppure generati / aggiornati dall'interfaccia web.

---

## Esempi di configurazione

### `telegram.json`

```json
{
  "TELEGRAM_ENABLED": true,
  "TELEGRAM_BOT_TOKEN": "123456789:ABCDEF_TUO_TOKEN",
  "TELEGRAM_CHAT_ID": "123456789"
}
```

### `targets.json`

```json
[
  {
    "name": "cloudflare",
    "mode": "ping",
    "host": "1.1.1.1",
    "silent": false
  },
  {
    "name": "google dns",
    "mode": "tcp",
    "host": "8.8.8.8",
    "port": 53,
    "silent": false
  }
]
```

---

## Installazione

1. Flash MicroPython (>= 1.22) sul Raspberry Pi Pico W.
2. Copia `main.py` sul Pico W (via Thonny, mpremote, ecc.).
3. (Opzionale) Crea `targets.json`, `config.json` e `telegram.json` seguendo gli esempi sopra.
4. Modifica in `main.py` la sezione `CONFIG` con SSID e password Wi-Fi.
5. Genera l'hash della password per la Basic Auth:

```bash
python - << 'PY'
import hashlib
print(hashlib.sha256(b"tuaPasswordQui").hexdigest())
PY
```

6. Inserisci l'hash in `AUTH["PASS_SHA256"]` dentro `main.py`.
7. Riavvia il Pico W e controlla la seriale/log:

```text
[WiFi] connected, IP: 192.168.x.x
[HTTP] listening on port 8080
```

8. Apri il browser su `http://192.168.x.x:8080` e usa la dashboard.

---

## Endpoint principali

| Endpoint          | Funzione                         |
|------------------|----------------------------------|
| `/`              | Dashboard principale             |
| `/add`           | Aggiungi target                  |
| `/del?i=X`       | Rimuovi target X                 |
| `/test?i=X`      | Test target X                    |
| `/notifymode?i=X`| Cambia modalità notifica target  |
| `/telegram`      | Configurazione Telegram          |
| `/telegram_save` | Salvataggio config Telegram      |
| `/notify_test`   | Test invio notifica Telegram     |
| `/set`           | Aggiorna intervalli/soglie       |
| `/reboot`        | Reboot sicuro del dispositivo    |

---

## .gitignore consigliato

```
targets.json
config.json
telegram.json
*.pyc
.vscode/
.idea/
__pycache__/
```

---

## Note di sicurezza e privacy

- Evita di committare file contenenti token o chat-id veri.
- Se hai bisogno di rimuovere accidentalmente un secret dal repo, usa `git filter-repo` o la procedura ufficiale GitHub per rimuovere dati sensibili.

---

## License

Rilasciato sotto licenza **GPL-3.0**.

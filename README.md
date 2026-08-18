# Pico Uptime — Mini Monitor di Rete per Raspberry Pi Pico W

[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20Pico%20W-orange.svg)]()
[![Language](https://img.shields.io/badge/language-MicroPython-blue.svg)]()
[![Version](https://img.shields.io/badge/version-1.1.9-blue.svg)]()
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)]()
[![Status](https://img.shields.io/badge/status-stable-brightgreen.svg)]()

**Pico Uptime** trasforma un Raspberry Pi Pico W in un piccolo uptime monitor autonomo, con dashboard web, controlli HTTP/TCP/ICMP e notifiche Telegram.

Il progetto è volutamente leggero: nessun framework web, nessun database e nessun servizio cloud obbligatorio oltre a Telegram, se abilitato.

## Funzionalità

- **Monitoraggio multiprotocollo**: HTTP/HTTPS, TCP e Ping ICMP con RTT.
- **Dashboard web responsive** con stato UP/DOWN/UNKNOWN, aggiunta/modifica/eliminazione target e test manuale.
- **Diagnostica compatta**: uptime, RSSI Wi-Fi, IP, RAM libera, numero target e ultimo polling.
- **Telegram multi-chat**: più chat con un singolo bot, una o più chat per target, chat predefinite, test e rinomina.
- **Notifiche sonore/silenziose per target**.
- **Polling persistente** con intervallo e soglie UP/DOWN configurabili da browser.
- **Persistenza JSON** tramite `config.json`, `targets.json` e `telegram.json`.
- **Basic Auth** su tutta l'interfaccia con blocco temporaneo dopo login falliti.
- **Recovery Wi-Fi** e reboot manuale dalla UI.
- Compatibilità con il vecchio formato Telegram single-chat (`TELEGRAM_CHAT_ID`).

## Requisiti

- Raspberry Pi Pico W
- MicroPython recente per Pico W
- rete Wi-Fi 2.4 GHz
- facoltativo: bot Telegram

## Installazione

1. Installa MicroPython sul Pico W.
2. Copia `main.py` nella root del filesystem.
3. Configura SSID e password nella sezione `CONFIG`:

```python
CONFIG = {
    "WIFI_SSID": "your_wifi_ssid",
    "WIFI_PASSWORD": "your_wifi_password",
    "HTTP_PORT": 8080,
}
```

4. Genera l'hash SHA-256 della password web:

```bash
python -c "import hashlib; print(hashlib.sha256(b'your_password').hexdigest())"
```

5. Inseriscilo in `AUTH["PASS_SHA256"]`.
6. Riavvia il Pico e apri `http://IP_DEL_PICO:8080`.

I file JSON vengono creati o aggiornati dall'interfaccia quando necessario.

## Telegram multi-chat

Esempio `telegram.json`:

```json
{
  "TELEGRAM_ENABLED": true,
  "TELEGRAM_BOT_TOKEN": "123456789:YOUR_BOT_TOKEN",
  "TELEGRAM_CHATS": [
    {"key": "home", "name": "Home", "chat_id": "123456789", "enabled": true},
    {"key": "noc", "name": "NOC", "chat_id": "-1001234567890", "enabled": true}
  ],
  "DEFAULT_CHAT_KEYS": ["home"]
}
```

La `key` è l'identificatore interno. Il nome visualizzato può essere rinominato dalla UI senza rompere le associazioni dei target.

Il vecchio formato resta supportato:

```json
{
  "TELEGRAM_ENABLED": true,
  "TELEGRAM_BOT_TOKEN": "123456789:YOUR_BOT_TOKEN",
  "TELEGRAM_CHAT_ID": "123456789"
}
```

## Target

Esempio `targets.json`:

```json
[
  {
    "name": "Gateway",
    "mode": "ping",
    "host": "192.168.1.1",
    "silent": false,
    "notify_chat_keys": ["home", "noc"]
  },
  {
    "name": "Web service",
    "mode": "http",
    "url": "https://example.org/",
    "silent": true,
    "notify_chat_keys": ["noc"]
  }
]
```

`notify_chat_keys: []` disabilita intenzionalmente gli alert per quel target. Se la chiave manca, vengono usate le chat predefinite.

## Polling

Esempio `config.json`:

```json
{
  "CHECK_INTERVAL": 30,
  "UP_THRESHOLD": 4,
  "DOWN_THRESHOLD": 6
}
```

## Endpoint principali

| Endpoint | Funzione |
|---|---|
| `/` | Dashboard |
| `/add` | Aggiunge un target |
| `/edit?i=X` | Modifica un target |
| `/del?i=X` | Elimina un target |
| `/test?i=X` | Verifica manualmente un target |
| `/notify?i=X` | Test Telegram per il target |
| `/notifymode?i=X` | Alterna sonora/silenziosa |
| `/telegram` | Gestione Telegram/chat |
| `/telegram_test?key=X` | Test singola chat |
| `/telegram_rename?key=X` | Rinomina chat |
| `/settings` | Polling e soglie |
| `/reboot` | Riavvia il Pico W |

Tutti gli endpoint applicativi richiedono autenticazione.

## Segreti e file locali

Non committare configurazioni reali o credenziali. `config.json`, `targets.json` e `telegram.json` sono esclusi da Git tramite `.gitignore`.

SSID, password Wi-Fi e hash della password web vanno configurati localmente prima del deploy.

## Note sulle risorse

Pico Uptime gira su un microcontrollore con RAM limitata. La diagnostica è volutamente una singola barra compatta per evitare grosse allocazioni HTML durante il rendering.

## License

Rilasciato sotto licenza **GPL-3.0**.

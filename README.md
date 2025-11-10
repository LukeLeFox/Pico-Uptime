# 🧭 Pico Uptime — Mini Monitor di Rete per Raspberry Pi Pico W

![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi%20Pico%20W-blue)
![Language](https://img.shields.io/badge/Language-MicroPython-orange)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-2.0.0-blueviolet)

**Pico Uptime** è un mini sistema di monitoraggio scritto in **MicroPython** che trasforma un **Raspberry Pi Pico W** in un uptime monitor web-based.  
È progettato per essere leggero, completamente autonomo e configurabile direttamente da browser — senza ricompilare il codice.

---

## 🚀 Funzionalità principali

- ✅ **Monitoraggio multiprotocollo**
  - HTTP / HTTPS (codici di stato)
  - TCP (connessione socket)
  - Ping ICMP nativo
- 🌐 **Interfaccia Web integrata**
  - Dashboard con tabella dei target e stato in tempo reale
  - Aggiunta / rimozione target da browser
  - Gestione **notifiche silenziose/sonore per target**
  - Configurazione Telegram da UI
  - Test notifiche e reboot remoto
  - Modifica dinamica intervallo e soglie di polling
- 🔔 **Notifiche Telegram**
  - Messaggi automatici per transizioni UP/DOWN
  - Supporto modalità **silenziosa**
  - Configurabili via pagina `/telegram`
- 🧠 **Configurazione persistente**
  - Salvataggio su file JSON:
    - `targets.json`
    - `config.json`
    - `telegram.json`
- ♻️ **Auto-protezione**
  - Riavvio automatico se il Wi-Fi non è disponibile per troppo tempo
  - Pulsante reboot da interfaccia web
- 🔐 **Sicurezza**
  - Accesso via Basic Auth
  - Protezione contro tentativi multipli errati

---

## 📁 Struttura dei file

| File | Descrizione |
|------|-------------|
| `main.py` | Codice principale (server web + monitor) |
| `targets.json` | Lista dei target monitorati |
| `config.json` | Intervallo e soglie configurabili |
| `telegram.json` | Dati Telegram (token, chat id, abilitazione) |

---

### 📄 Esempio di `telegram.json`
{
  "TELEGRAM_ENABLED": true,
  "TELEGRAM_BOT_TOKEN": "123456789:ABCDEF_TUO_TOKEN",
  "TELEGRAM_CHAT_ID": "123456789"
}


📄 Esempio di targets.json
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

⚙️ Installazione
Copia i file sul Pico W

Carica main.py e i file .json tramite Thonny o mpremote

Configura il Wi-Fi e la password di accesso

Modifica CONFIG in main.py:

CONFIG = {
    "WIFI_SSID": "IlTuoSSID",
    "WIFI_PASSWORD": "LaTuaPassword",
    ...
}
Genera hash SHA256 della password

python - <<'PY'
import hashlib; print(hashlib.sha256(b"tuaPassword").hexdigest())
PY
Copialo in AUTH["PASS_SHA256"]

Avvio

Riavvia il Pico

Guarda la console:

[WiFi] connected, IP: 192.168.x.x
[HTTP] listening on port 8080
Apri nel browser http://192.168.x.x:8080

🌍 Interfaccia Web
Sezione	Descrizione
Dashboard	Stato dei target in tempo reale
Aggiungi target	HTTP / TCP / Ping
Impostazioni	Modifica intervallo e soglie
Notifiche	Gestisci modalità silenziosa per target
Config Telegram	Modifica token/chat id da browser
Test notifiche	Invia messaggi di prova (🔔 / 🔕)
Reboot	Riavvia il Pico in sicurezza

🧰 Endpoint principali
Endpoint	Funzione
/	Dashboard principale
/add	Aggiunta target
/del?i=X	Rimuove target X
/test?i=X	Test connessione target
/notifymode?i=X	Cambia modalità notifica
/telegram	Config Telegram
/telegram_save	Salva Telegram settings
/notify_test	Test notifiche Telegram
/set	Aggiorna polling/soglie
/reboot	Riavvia il dispositivo

🧩 Requisiti
Raspberry Pico W

Firmware MicroPython ≥ 1.22

Nessuna libreria esterna richiesta

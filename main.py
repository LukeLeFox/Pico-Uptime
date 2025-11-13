# main.py — Pico Uptime (Raspberry Pi Pico W, MicroPython)
# - Web UI con Basic Auth
# - Monitor HTTP / TCP / PING (ICMP)
# - Notifiche Telegram su variazioni UP/DOWN
# - Soglie e intervallo configurabili da UI (persistenti)
# - Notifiche silenziose/sonore per-target + pagina test notifiche
# - Config Telegram da UI (telegram.json) + toggle ON/OFF
# - Reboot da UI
# - Auto-reboot se Wi-Fi non torna dopo N tentativi

import network, time, socket, ssl, json, os, gc, machine
try:
    import uhashlib as hashlib
except:
    import hashlib
try:
    import ubinascii as binascii
except:
    import binascii
try:
    import ustruct as struct
except:
    import struct

LED = machine.Pin("LED", machine.Pin.OUT)

# =======================
# ======= CONFIG ========
# =======================
CONFIG = {
    "WIFI_SSID": "placeholder_ssid",
    "WIFI_PASSWORD": "placeholder_netpsw",


    "HTTP_PORT": 8080,
    
    #default polling conf here
    "CHECK_INTERVAL": 15,   # secondi
    "DOWN_THRESHOLD": 2,    # consecutivi KO per marcare DOWN
    "UP_THRESHOLD": 2,      # consecutivi OK per marcare UP
    
    #default conf telegram here
    "TELEGRAM_ENABLED": True,
    "TELEGRAM_BOT_TOKEN": "placeholder_token",
    "TELEGRAM_CHAT_ID": "placeholder_id",
    
    "TARGETS_FILE": "targets.json",
}

# ======= AUTH ========
# genera l'hash con:
#   python - <<'PY'
#   import hashlib; print(hashlib.sha256(b"tuaPasswordQui").hexdigest())
#   PY


AUTH = {
    "USER": "admin",
    "PASS_SHA256": "placeholder_psw",
    "MAX_FAILS": 5,
    "BLOCK_SECONDS": 30,
    "HEALTH_PUBLIC_TOKEN": "",  # opzionale per /health senza auth
}

# === FILE DI CONFIG ===
CONFIG_FILE = "config.json"
TELEGRAM_CONFIG_FILE = "telegram.json"

# =======================
# == RUNTIME CONFIG IO ==
# =======================
def load_runtime_config():
    """Carica parametri runtime da config.json (se esiste)."""
    try:
        if CONFIG_FILE in os.listdir():
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)

            ci = int(data.get("CHECK_INTERVAL", CONFIG["CHECK_INTERVAL"]))
            up = int(data.get("UP_THRESHOLD", CONFIG["UP_THRESHOLD"]))
            dn = int(data.get("DOWN_THRESHOLD", CONFIG["DOWN_THRESHOLD"]))

            # limiti di sicurezza
            ci = max(2, min(ci, 600))
            up = max(1, min(up, 10))
            dn = max(1, min(dn, 10))

            CONFIG["CHECK_INTERVAL"] = ci
            CONFIG["UP_THRESHOLD"] = up
            CONFIG["DOWN_THRESHOLD"] = dn
            print("[CFG] runtime loaded:", ci, up, dn) #<- Commenta per debug
        else:
            print("[CFG] no config file found, using defaults")
    except Exception as e:
        print("[CFG] load error:", repr(e))


def save_runtime_config():
    """Salva i parametri runtime nel file config.json."""
    try:
        data = {
            "CHECK_INTERVAL": CONFIG["CHECK_INTERVAL"],
            "UP_THRESHOLD": CONFIG["UP_THRESHOLD"],
            "DOWN_THRESHOLD": CONFIG["DOWN_THRESHOLD"],
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f)
        print("[CFG] saved to file") #<- Commenta per debug
        return True
    except Exception as e:
        print("[CFG] save error:", repr(e))
        return False

# =======================
# == TELEGRAM CONFIG IO ==
# =======================
def load_telegram_config():
    try:
        if TELEGRAM_CONFIG_FILE in os.listdir():
            with open(TELEGRAM_CONFIG_FILE, "r") as f:
                data = json.load(f)
            if "TELEGRAM_ENABLED" in data:
                CONFIG["TELEGRAM_ENABLED"] = bool(data["TELEGRAM_ENABLED"])
            if "TELEGRAM_BOT_TOKEN" in data and data["TELEGRAM_BOT_TOKEN"]:
                CONFIG["TELEGRAM_BOT_TOKEN"] = data["TELEGRAM_BOT_TOKEN"]
            if "TELEGRAM_CHAT_ID" in data and data["TELEGRAM_CHAT_ID"]:
                CONFIG["TELEGRAM_CHAT_ID"] = str(data["TELEGRAM_CHAT_ID"])
            print("[TGCFG] loaded from file") #<- Commenta per debug
        else:
            save_telegram_config()
    except Exception as e:
        print("[TGCFG] load error:", repr(e))

def save_telegram_config():
    try:
        data = {
            "TELEGRAM_ENABLED": CONFIG["TELEGRAM_ENABLED"],
            "TELEGRAM_BOT_TOKEN": CONFIG["TELEGRAM_BOT_TOKEN"],
            "TELEGRAM_CHAT_ID": CONFIG["TELEGRAM_CHAT_ID"],
        }
        with open(TELEGRAM_CONFIG_FILE, "w") as f:
            json.dump(data, f)
        return True
    except Exception as e:
        print("[TGCFG] save error:", repr(e))
        return False

# =======================
# ====== TARGET IO ======
# =======================
def load_targets():
    fname = CONFIG["TARGETS_FILE"]
    data = []
    
    try:
        if fname in os.listdir():
            with open(fname, "r") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        else:
            data = []
    except Exception as e:
        print("[CFG] load_targets error:", repr(e))
        data = []

    out = []
    for t in data:
        if not isinstance(t, dict):
            continue
        mode  = t.get("mode")
        name  = t.get("name", "target")
        silent = bool(t.get("silent", False))
        if mode == "http" and t.get("url"):
            out.append({"name": name, "mode": "http", "url": t["url"], "silent": silent})
        elif mode == "tcp" and t.get("host") and int(t.get("port", 0)) > 0:
            out.append({"name": name, "mode": "tcp", "host": t["host"], "port": int(t["port"]), "silent": silent})
        elif mode == "ping" and t.get("host"):
            out.append({"name": name, "mode": "ping", "host": t["host"], "silent": silent})

    return out

def save_targets(targets):
    try:
        with open(CONFIG["TARGETS_FILE"], "w") as f:
            json.dump(targets, f)
        return True
    except Exception as e:
        print("[TARGET] save error:", repr(e))
        return False

# =======================
# ======= NET/UTIL ======
# =======================
def wifi_connect(ssid, password, timeout=25):
    print("[WiFi] connecting to", ssid, "...")
    LED.off()
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            LED.toggle()
            time.sleep(0.25)
            if time.ticks_diff(time.ticks_ms(), t0) > timeout * 1000:
                print("[WiFi] timeout.")
                LED.off()
                return False
    ip = wlan.ifconfig()[0]
    print("[WiFi] connected, IP:", ip)
    LED.on()
    return True

def url_encode(s):
    SAFE = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = []
    for b in s.encode("utf-8"):
        if b == 0x20:
            out.append('+')
        elif b in SAFE:
            out.append(chr(b))
        else:
            out.append('%{:02X}'.format(b))
    return ''.join(out)

def html_escape(s):
    return (s.replace("&","&amp;")
             .replace("<","&lt;")
             .replace(">","&gt;")
             .replace('"',"&quot;"))

def parse_qs(qs):
    params = {}
    for part in qs.split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        k = k.replace("+"," ")
        v = v.replace("+"," ")
        def pct(x):
            res = b""
            xb = x.encode()
            i = 0
            while i < len(xb):
                if xb[i:i+1] == b'%' and i+2 < len(xb):
                    try:
                        res += bytes([int(xb[i+1:i+3], 16)])
                        i += 3
                        continue
                    except:
                        pass
                res += xb[i:i+1]
                i += 1
            try:
                return res.decode()
            except:
                return x
        params[pct(k)] = pct(v)
    return params

def parse_url(url):
    scheme = "http"
    port = 80
    path = "/"
    rest = url
    if "://" in url:
        scheme, rest = url.split("://", 1)
    if "/" in rest:
        hostport, path = rest.split("/", 1)
        path = "/" + path
    else:
        hostport = rest
        path = "/"
    if ":" in hostport:
        host, p = hostport.split(":", 1)
        port = int(p)
    else:
        host = hostport
        port = 443 if scheme == "https" else 80
    return scheme, host, port, path

# =======================
# ====== CHECKERS =======
# =======================
def check_tcp(host, port, timeout=3):
    s = None
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(addr)
        return True
    except:
        return False
    finally:
        try:
            if s:
                s.close()
        except:
            pass

def check_http(url, timeout=4):
    scheme, host, port, path = parse_url(url)
    s = None
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(addr)
        if scheme == "https":
            s = ssl.wrap_socket(s, server_hostname=host)
        req = "HEAD {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\nUser-Agent: PicoUptime/1\r\n\r\n".format(path, host)
        s.write(req.encode())
        data = s.read(64)
        if not data:
            return False, None
        line = data.split(b"\r\n", 1)[0]
        parts = line.split()
        status = int(parts[1]) if len(parts) > 1 else 0
        return (status < 400), status
    except:
        return False, None
    finally:
        try:
            if s:
                s.close()
        except:
            pass

# ----- PING (ICMP) -----
def _icmp_checksum(data):
    if len(data) & 1:
        data += b'\x00'
    s = 0
    for i in range(0, len(data), 2):
        s += (data[i] << 8) + data[i+1]
        s = (s & 0xffff) + (s >> 16)
    return (~s) & 0xffff

def check_ping(host, timeout_ms=1000, seq=1, payload_size=8):
    s = None
    try:
        dest = socket.getaddrinfo(host, 1)[0][-1][0]
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, 1)
        s.settimeout(timeout_ms / 1000.0)

        ident = 0xABCD
        payload = b'Q' * payload_size
        header = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
        csum = _icmp_checksum(header + payload)
        packet = struct.pack("!BBHHH", 8, 0, csum, ident, seq) + payload

        t0 = time.ticks_ms()
        s.sendto(packet, (dest, 1))
        resp = s.recv(128)
        t1 = time.ticks_ms()

        ihl = (resp[0] & 0x0F) * 4
        icmp = resp[ihl:ihl+8]
        if len(icmp) < 8:
            return False, None
        r_type, r_code, r_csum, r_ident, r_seq = struct.unpack("!BBHHH", icmp)
        if r_type == 0 and r_ident == ident and r_seq == seq:
            rtt = time.ticks_diff(t1, t0)
            return True, int(rtt)
        return False, None
    except:
        return False, None
    finally:
        try:
            if s:
                s.close()
        except:
            pass

# =======================
# ===== NOTIFICHE =======
# =======================
def telegram_send(bot_token, chat_id, text, silent=False):
    host = "api.telegram.org"
    port = 443
    path = "/bot{}/sendMessage".format(bot_token)

    payload = "chat_id={}&text={}&disable_web_page_preview=1".format(
        url_encode(str(chat_id)),
        url_encode(text)
    )
    if silent:
        payload += "&disable_notification=true"

    req = (
        "POST {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        "Content-Length: {}\r\n\r\n{}"
    ).format(path, host, len(payload), payload)

    s = None
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(6)
        s.connect(addr)
        s = ssl.wrap_socket(s, server_hostname=host)
        s.write(req.encode())
        resp = s.read() or b""
        if b'"ok":true' not in resp:
            try:
                print("[TG] fail:", resp.split(b"\r\n\r\n", 1)[-1].decode())
            except:
                print("[TG] fail (raw)")
        return True
    except Exception as e:
        print("[TG] error:", repr(e))
        return False
    finally:
        try:
            if s:
                s.close()
        except:
            pass

def notify(text, silent=False):
    if not CONFIG.get("TELEGRAM_ENABLED"):
        return
    try:
        telegram_send(CONFIG["TELEGRAM_BOT_TOKEN"], CONFIG["TELEGRAM_CHAT_ID"], text, silent=silent)
    except Exception as e:
        print("[TG] notify error:", repr(e))

# =======================
# ====== BASIC AUTH =====
# =======================
_auth_state = {"fails": 0, "blocked_until": 0}

def sha256_hex(b):
    h = hashlib.sha256(b)
    try:
        return binascii.hexlify(h.digest()).decode()
    except:
        return "".join("{:02x}".format(x) for x in h.digest())

def parse_headers(raw):
    try:
        head = raw.split(b"\r\n\r\n", 1)[0].decode()
    except:
        head = ""
    lines = head.split("\r\n")[1:]
    headers = {}
    for ln in lines:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers

def check_basic_auth(headers, now_ms):
    global _auth_state
    if _auth_state["blocked_until"] and time.ticks_diff(now_ms, _auth_state["blocked_until"]) < 0:
        return (False, "429 Too Many Requests", True)
    auth = headers.get("authorization", "")
    if auth.startswith("Basic "):
        try:
            raw = auth.split(" ", 1)[1]
            creds = binascii.a2b_base64(raw).decode()
            if ":" in creds:
                user, pwd = creds.split(":", 1)
            else:
                user, pwd = creds, ""
            if user == AUTH["USER"] and sha256_hex(pwd.encode()) == AUTH["PASS_SHA256"]:
                _auth_state["fails"] = 0
                _auth_state["blocked_until"] = 0
                return (True, "200 OK", False)
        except:
            pass
    _auth_state["fails"] += 1
    if _auth_state["fails"] >= AUTH["MAX_FAILS"]:
        _auth_state["blocked_until"] = time.ticks_add(now_ms, AUTH["BLOCK_SECONDS"] * 1000)
        _auth_state["fails"] = 0
        return (False, "429 Too Many Requests", True)
    return (False, "401 Unauthorized", False)

def http_response(conn, status="200 OK", ctype="text/html; charset=utf-8", body="", extra_headers=None):
    try:
        blen = len(body) if isinstance(body, str) else len(body)
        hdrs = [
            "HTTP/1.1 {}".format(status),
            "Server: PicoUptime",
            "Content-Type: {}".format(ctype),
            "Content-Length: {}".format(blen),
            "Connection: close",
        ]
        if extra_headers:
            hdrs.extend(extra_headers)
        conn.sendall(("\r\n".join(hdrs) + "\r\n\r\n").encode())
        if isinstance(body, str):
            conn.sendall(body.encode())
        else:
            conn.sendall(body)
    except:
        pass

# =======================
# ======= WEB UI ========
# =======================
def render_page(targets, states, flash=""):
    if targets is None:
        targets = []
    if states is None:
        states = []
    # costruzione righe tabella
    rows = []
    for i, t in enumerate(targets):
        st   = states[i]["status"] if i < len(states) else None
        code = states[i].get("last_code") if i < len(states) else None

        # badge stato
        if t.get("mode") == "ping":
            if st is True:
                badge = "🟢 UP" + ("" if code is None else " ({} ms)".format(code))
            elif st is False:
                badge = "🔴 DOWN (ping)"
            else:
                badge = "⚪ unknown"
        else:
            if st is True:
                badge = "🟢 UP"
            elif st is False:
                badge = "🔴 DOWN" + ("" if code is None else " (HTTP {})".format(code))
            else:
                badge = "⚪ unknown"

        # endpoint leggibile
        if t.get("mode") == "http":
            endpoint = html_escape(t.get("url",""))
        elif t.get("mode") == "tcp":
            endpoint = "{}:{}".format(html_escape(t.get("host","")), t.get("port",""))
        else:  # ping
            endpoint = "{} (ICMP)".format(html_escape(t.get("host","")))

        # stato notifica (supporta sia 'silent' sia 'mute' per retrocompatibilità)
        is_silent = bool(t.get("silent", t.get("mute", False)))
        notif_html = (
            "<a href='/notifymode?i={}'>🔊 Sonoro</a> | <span style='opacity:.5'>🔕 Silenzioso</span>"
            if is_silent else
            "<span style='opacity:.5'>🔊 Sonoro</span> | <a href='/notifymode?i={}'>🔕 Silenzioso</a>"
        ).format(i)


        # azioni
        actions = "<a href='/test?i={}'>Test</a> | <a href='/del?i={}' onclick='return confirm(\"Eliminare?\")'>Del</a>".format(i, i)

        rows.append(
            "<tr>"
            "<td>{}</td>"
            "<td>{}</td>"
            "<td><code>{}</code></td>"
            "<td style='text-align:center'>{}</td>"
            "<td style='text-align:center'>{}</td>"
            "<td style='text-align:right'>{}</td>"
            "</tr>".format(
                html_escape(t.get("name","target")),
                html_escape(t.get("mode","-")),
                endpoint,
                badge,
                notif_html,
                actions
            )
        )

    flash_html = "" if not flash else "<div class='flash'>{}</div>".format(html_escape(flash))

    # pagina
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pico Uptime</title>
  <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;max-width:980px;margin:28px auto;padding:0 12px;}}
    h1{{display:flex;align-items:center;gap:8px}}
    table{{width:100%%;border-collapse:collapse;margin:14px 0;}}
    th,td{{border:1px solid #ddd;padding:8px;}}
    th{{background:#f6f6f6;text-align:left;}}
    fieldset{{margin-top:16px;border:1px solid #ddd;}}
    legend{{padding:0 6px;color:#444}}
    input,select{{padding:6px;margin:4px 0;min-width:260px;}}
    .btn{{display:inline-block;padding:8px 12px;border:1px solid #444;background:#fafafa;cursor:pointer;text-decoration:none}}
    .flash{{padding:10px;background:#ffffcc;border:1px solid #e6db55;margin-bottom:10px}}
    .small{{color:#666;font-size:12px}}
  </style>
</head>
<body>

<h1>🧭 Pico Uptime</h1>
<p class="small">UI protetta da Basic Auth. Notifiche Telegram solo su variazione UP/DOWN. Colonna “Notifica” per Sonoro/Silenzioso per-target.</p>
{flash}

<table>
  <thead>
    <tr>
      <th>Nome</th>
      <th>Modo</th>
      <th>Endpoint</th>
      <th>Stato</th>
      <th>Notifica</th>
      <th style="text-align:right">Azioni</th>
    </tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
</table>

<fieldset>
  <legend>Aggiungi target</legend>
  <form action="/add" method="get" id="addForm">
    <label>Nome<br>
      <input type="text" name="name" placeholder="NAS HTTP">
    </label><br>

    <label>Modo<br>
      <select name="mode" id="mode" onchange="onModeChange(this.value)">
        <option value="http">http/https</option>
        <option value="tcp">tcp</option>
        <option value="ping">ping (ICMP)</option>
      </select>
    </label><br>

    <!-- HTTP/HTTPS -->
    <div id="httpFields">
      <label>URL<br>
        <input type="text" name="url" placeholder="http://10.10.99.253/">
      </label><br>
    </div>

    <!-- TCP -->
    <div id="tcpFields" style="display:none">
      <label>Host<br>
        <input type="text" name="host" placeholder="10.10.99.254">
      </label><br>
      <label>Porta<br>
        <input type="number" name="port" placeholder="1883" min="1" max="65535" step="1">
      </label><br>
    </div>

    <!-- PING -->
    <div id="pingFields" style="display:none">
      <label>Host<br>
        <input type="text" name="host_ping" placeholder="10.10.99.254">
      </label><br>
    </div>

    <button class="btn" type="submit">Aggiungi</button>
  </form>
</fieldset>

<script>
function onModeChange(v){{    // <-- graffe raddoppiate
  var http = document.getElementById('httpFields');
  var tcp  = document.getElementById('tcpFields');
  var ping = document.getElementById('pingFields');

  // toggle visibilità
  http.style.display = (v === 'http') ? 'block' : 'none';
  tcp.style.display  = (v === 'tcp')  ? 'block' : 'none';
  ping.style.display = (v === 'ping') ? 'block' : 'none';

  // abilita/disabilita i campi per evitare submit indesiderati
  Array.from(http.querySelectorAll('input')).forEach(function(el){{ el.disabled = (v !== 'http'); }});
  Array.from(tcp.querySelectorAll('input')).forEach(function(el){{ el.disabled  = (v !== 'tcp'); }});
  Array.from(ping.querySelectorAll('input')).forEach(function(el){{ el.disabled = (v !== 'ping'); }});

  // mappa host ping -> name=host solo quando serve
  var hostPing = document.querySelector('input[name="host_ping"]');
  if (v === 'ping') {{ hostPing.setAttribute('name','host'); }}
  else             {{ hostPing.setAttribute('name','host_ping'); }}
}}

// inizializza allo stato corrente del select
window.addEventListener('DOMContentLoaded', function(){{ 
  onModeChange(document.getElementById('mode').value); 
}});
</script>


<p class="small">
  Config: <a href="/telegram">/telegram</a> ·
  Test notifiche: <a href="/notify_test">/notify_test</a> ·
  Sistema: <a href="/reboot" onclick="return confirm('Riavviare il dispositivo?')">riavvia</a>
</p>

<p class="small">
Polling: ogni {ci}s — UP thr {up} / DOWN thr {dn}.
<a href="/settings">Modifica</a>
</p>

<script>
function onModeChange(v){{   // doppie graffe per .format
  document.getElementById('httpFields').style.display = (v==='http') ? 'block' : 'none';
  document.getElementById('hostFields').style.display = (v==='tcp' || v==='ping') ? 'block' : 'none';
  document.getElementById('portField').style.display = (v==='tcp') ? 'block' : 'none';
}}
// init on load
(function(){{ 
  var sel = document.getElementById('mode');
  if (sel) onModeChange(sel.value);
}})();
</script>

</body>
</html>
""".format(
    flash=flash_html,
    rows="\n".join(rows),
    ci=CONFIG["CHECK_INTERVAL"],
    up=CONFIG["UP_THRESHOLD"],
    dn=CONFIG["DOWN_THRESHOLD"]
)

    return html

def render_notify_test_page(targets, flash=""):
    if targets is None:
        targets = []
    rows = []
    for i, t in enumerate(targets or []):
        silent = bool(t.get("silent", False))
        mode_label = "🔕 silenziose" if silent else "🔔 sonore"
        rows.append(
            "<tr>"
            "<td>{}</td>"
            "<td style='text-align:center'>{}</td>"
            "<td style='text-align:center'>"
            "<a href='/notify?i={}&mode=silent'>🔕 test silenziosa</a> | "
            "<a href='/notify?i={}&mode=normal'>🔔 test sonora</a>"
            "</td>"
            "</tr>".format(
                html_escape(t.get("name", "target")),
                mode_label, i, i
            )
        )
    flash_html = "" if not flash else "<div class='flash'>{}</div>".format(html_escape(flash))
    return """<!doctype html>
<html><head>
<meta charset="utf-8"><title>Test notifiche - Pico Uptime</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 12px;}}
table{{width:100%%;border-collapse:collapse;margin:12px 0;}}
th,td{{border:1px solid #ddd;padding:8px;}}
th{{background:#f2f2f2;text-align:left;}}
.flash{{padding:10px;background:#ffffcc;border:1px solid #e6db55;margin-bottom:10px}}
.small{{color:#666;font-size:12px}}
</style>
</head><body>
<h1>🔔 Test notifiche Telegram</h1>
<p class="small">Ogni pulsante invia una notifica di test solo per il target selezionato.</p>
{flash}
<table>
<thead><tr><th>Nome</th><th>Modalità corrente</th><th>Invia test</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
<p class="small"><a href="/">← Torna alla dashboard</a></p>
</body></html>
""".format(flash=flash_html, rows="\n".join(rows))

def render_settings_page(flash=""):
    flash_html = "" if not flash else "<div class='flash'>{}</div>".format(html_escape(flash))
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Impostazioni</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 12px;}}
fieldset{{border:1px solid #ddd;}}
legend{{padding:0 6px;color:#444}}
label{{display:block;margin:8px 0 4px}}
input{{padding:6px;min-width:160px}}
.btn{{display:inline-block;padding:8px 12px;border:1px solid #444;background:#fafafa;cursor:pointer;text-decoration:none}}
.flash{{padding:10px;background:#ffffcc;border:1px solid #e6db55;margin-bottom:10px}}
.small{{color:#666;font-size:12px}}
</style>
</head><body>
<h2>⚙️ Impostazioni runtime</h2>
{flash}
<form action="/settings" method="get">
  <fieldset>
    <legend>Polling & Soglie</legend>
    <label>Intervallo di polling (secondi)</label>
    <input type="number" name="ci" min="2" max="600" value="{ci}">
    <label>UP_THRESHOLD (conteggi)</label>
    <input type="number" name="up" min="1" max="10" value="{up}">
    <label>DOWN_THRESHOLD (conteggi)</label>
    <input type="number" name="dn" min="1" max="10" value="{dn}">
  </fieldset>
  <p style="margin-top:12px">
    <button class="btn" type="submit" name="save" value="1">Salva</button>
    <a class="btn" href="/">Annulla</a>
  </p>
</form>
<p class="small">Le modifiche vengono salvate in <code>config.json</code> e applicate al volo.</p>
</body></html>
""".format(flash=flash_html, ci=CONFIG["CHECK_INTERVAL"], up=CONFIG["UP_THRESHOLD"], dn=CONFIG["DOWN_THRESHOLD"])


def render_telegram_page(flash=""):
    flash_html = "" if not flash else "<div class='flash'>{}</div>".format(html_escape(flash))
    current_enabled = "checked" if CONFIG.get("TELEGRAM_ENABLED") else ""
    html = """<!doctype html>
<html><head>
<meta charset="utf-8">
<title>Config Telegram - Pico Uptime</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 12px;}}
label{{display:block;margin:8px 0;}}
input{{padding:6px;min-width:260px;}}
.btn{{display:inline-block;padding:8px 12px;border:1px solid #444;background:#fafafa;cursor:pointer;margin-top:8px;text-decoration:none}}
.flash{{padding:10px;background:#ffffcc;border:1px solid #e6db55;margin-bottom:10px}}
.small{{color:#666;font-size:12px}}
</style>
</head><body>
<h1>⚙️ Configurazione Telegram</h1>
{flash}
<p class="small">
Le modifiche sono salvate in <code>telegram.json</code>. I campi lasciati vuoti non vengono modificati.
</p>
<form action="/telegram_save" method="get">
  <label>
    Bot Token (lascia vuoto per non cambiare)<br>
    <input type="text" name="token" placeholder="nuovo bot token">
  </label>
  <label>
    Chat ID (lascia vuoto per non cambiare)<br>
    <input type="text" name="chat" placeholder="nuovo chat id">
  </label>
  <label>
    <input type="checkbox" name="enabled" value="1" {enabled}> Abilita notifiche Telegram
  </label>
  <button class="btn" type="submit">Salva</button>
</form>
<p class="small"><a href="/">← Torna alla dashboard</a></p>
</body></html>
""".format(
        flash=flash_html,
        enabled=current_enabled
    )
    return html

# =======================
# == SERVER HTTP LOGICA ==
# =======================
SERVER_SOCK = None

def ensure_server_socket():
    global SERVER_SOCK
    if SERVER_SOCK is not None:
        return True
    try:
        port = CONFIG.get("HTTP_PORT", 80)
        s = socket.socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except:
            pass
        s.bind(("0.0.0.0", port))
        s.listen(2)
        s.settimeout(0.2)
        SERVER_SOCK = s
        print("[HTTP] listening on port", port) #<- Commenta per debug
        return True
    except Exception as e:
        print("[HTTP] create socket error:", repr(e))
        try:
            if s:
                s.close()
        except:
            pass
        SERVER_SOCK = None
        gc.collect()
        time.sleep(0.3)
        return False

def serve_once(targets, states):
    global SERVER_SOCK
    if not ensure_server_socket():
        return targets, states
    try:
        try:
            conn, addr = SERVER_SOCK.accept()
        except OSError:
            return targets, states
        conn.settimeout(1.0)

        try:
            data = conn.recv(1024)
        except:
            conn.close()
            return targets, states

        if not data:
            conn.close()
            return targets, states

        try:
            line = data.split(b"\r\n", 1)[0].decode()
        except:
            line = "GET / HTTP/1.1"

        parts = line.split(" ")
        method = parts[0] if len(parts) > 0 else "GET"
        fullpath = parts[1] if len(parts) > 1 else "/"
        if "?" in fullpath:
            path, qs = fullpath.split("?", 1)
        else:
            path, qs = fullpath, ""
        params = parse_qs(qs)
        headers = parse_headers(data)
        now_ms = time.ticks_ms()

        def require_auth_or_deny():
            ok, status, blocked = check_basic_auth(headers, now_ms)
            if ok:
                return True
            if status.startswith("429"):
                http_response(conn, "429 Too Many Requests",
                              body="Too Many Requests. Riprova tra {}s.".format(AUTH["BLOCK_SECONDS"]))
            else:
                http_response(
                    conn,
                    "401 Unauthorized",
                    extra_headers=['WWW-Authenticate: Basic realm="PicoUptime"'],
                    body="Auth required."
                )
            return False

        # --- ignora favicon per evitare richieste spurie ---
        if path == "/favicon.ico":
            http_response(conn, "204 No Content", body="")
            conn.close()
            return targets, states

        # ================== ROUTING ==================
        if path == "/":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            if targets is None: targets = []
            if states is None: states = []
            http_response(conn, body=render_page(targets, states, ""))
            conn.close()
            return targets, states

        elif path == "/add":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            if not isinstance(targets, list):
                targets = []
            name = params.get("name","").strip() or "target"
            mode = params.get("mode","http")
            if mode == "http":
                url = params.get("url","").strip()
                if url:
                    targets.append({"name": name, "mode": "http", "url": url, "silent": False})
                    save_targets(targets)
                    states.append({"status": None, "oks": 0, "fails": 0, "last_code": None})
                    flash = "Aggiunto target HTTP."
                else:
                    flash = "URL mancante."
            elif mode == "tcp":
                host = params.get("host","").strip()
                port = int(params.get("port","0") or "0")
                if host and port > 0:
                    targets.append({"name": name, "mode": "tcp", "host": host, "port": port, "silent": False})
                    save_targets(targets)
                    states.append({"status": None, "oks": 0, "fails": 0, "last_code": None})
                    flash = "Aggiunto target TCP."
                else:
                    flash = "Host/Porta mancanti."
            elif mode == "ping":
                host = params.get("host","").strip()
                if host:
                    targets.append({"name": name, "mode": "ping", "host": host, "silent": False})
                    save_targets(targets)
                    states.append({"status": None, "oks": 0, "fails": 0, "last_code": None})
                    flash = "Aggiunto target PING."
                else:
                    flash = "Host mancante."
            else:
                flash = "Modo non valido."
            http_response(conn, body=render_page(targets, states, flash))
            conn.close(); return targets, states

        elif path == "/del":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            i = int(params.get("i", "-1") or "-1")
            if 0 <= i < len(targets):
                targets.pop(i)
                save_targets(targets)
                if i < len(states):
                    states.pop(i)
                flash = "Target eliminato."
            else:
                flash = "Indice non valido."
            http_response(conn, body=render_page(targets, states, flash))
            conn.close(); return targets, states

        elif path == "/test":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            i = int(params.get("i", "-1") or "-1")
            if 0 <= i < len(targets):
                t = targets[i]
                ok, code = False, None
                if t["mode"] == "http":
                    ok, code = check_http(t["url"])
                elif t["mode"] == "tcp":
                    ok = check_tcp(t["host"], t["port"])
                else:
                    ok, code = check_ping(t["host"])
                if i < len(states): states[i]["last_code"] = code
                if t["mode"] == "ping":
                    flash = ("UP ✅" if ok else "DOWN ❌") + ("" if code is None else " ({} ms)".format(code))
                else:
                    flash = ("UP ✅" if ok else "DOWN ❌") + ("" if code is None else " (HTTP {})".format(code))
            else:
                flash = "Indice non valido."
            http_response(conn, body=render_page(targets, states, flash))
            conn.close(); return targets, states

        elif path == "/notify_test":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            body = render_notify_test_page(targets)
            http_response(conn, body=body)
            conn.close(); return targets, states

        elif path == "/notifymode":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            i = int(params.get("i", "-1") or "-1")
            if 0 <= i < len(targets):
                t = targets[i]
                t["silent"] = not bool(t.get("silent", False))
                save_targets(targets)
                flash = "Modalità notifica: {} per '{}'".format(
                    "silenziosa" if t["silent"] else "sonora",
                    t.get("name","target")
                )
            else:
                flash = "Indice non valido."
            http_response(conn, body=render_page(targets, states, flash))
            conn.close(); return targets, states

        elif path == "/notify":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            i = int(params.get("i", "-1") or "-1")
            mode = params.get("mode", "normal")
            if 0 <= i < len(targets):
                t = targets[i]
                silent = (mode == "silent")
                icon = "🔕" if silent else "🔔"
                notify("{} Test notifica — {}".format(icon, t.get("name","target")), silent=silent)
                body = render_notify_test_page(targets, "Notifica {} inviata per '{}'.".format(
                    "silenziosa" if silent else "sonora", t.get("name","target")))
            else:
                body = render_notify_test_page(targets, "Indice non valido.")
            http_response(conn, body=body)
            conn.close(); return targets, states

        elif path == "/tg_toggle":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            CONFIG["TELEGRAM_ENABLED"] = not CONFIG["TELEGRAM_ENABLED"]
            save_telegram_config()
            flash = "Notifiche Telegram {}.".format(
                "abilitate" if CONFIG["TELEGRAM_ENABLED"] else "disabilitate")
            http_response(conn, body=render_page(targets, states, flash))
            conn.close(); return targets, states

        elif path == "/telegram":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            http_response(conn, body=render_telegram_page(""))
            conn.close(); return targets, states

        elif path == "/telegram_save":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            token = params.get("token","").strip()
            chat  = params.get("chat","").strip()
            enabled_flag = params.get("enabled","")
            if token:
                CONFIG["TELEGRAM_BOT_TOKEN"] = token
            if chat:
                CONFIG["TELEGRAM_CHAT_ID"] = chat
            CONFIG["TELEGRAM_ENABLED"] = bool(enabled_flag)
            save_telegram_config()
            http_response(conn, body=render_telegram_page("Configurazione Telegram aggiornata."))
            conn.close(); return targets, states

        elif path == "/settings":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            # se GET senza parametri -> mostra form
            if not params:
                body = render_settings_page()
                http_response(conn, body=body)
                conn.close(); return targets, states

            # altrimenti: salvataggio valori
            try:
                ci = int(params.get("ci", CONFIG["CHECK_INTERVAL"]))
            except:
                ci = CONFIG["CHECK_INTERVAL"]
            try:
                up = int(params.get("up", CONFIG["UP_THRESHOLD"]))
            except:
                up = CONFIG["UP_THRESHOLD"]
            try:
                dn = int(params.get("dn", CONFIG["DOWN_THRESHOLD"]))
            except:
                dn = CONFIG["DOWN_THRESHOLD"]

            # clamp
            ci = max(5, min(ci, 3600))
            up = max(1, min(up, 10))
            dn = max(1, min(dn, 10))

            CONFIG["CHECK_INTERVAL"]  = ci
            CONFIG["UP_THRESHOLD"]    = up
            CONFIG["DOWN_THRESHOLD"]  = dn
            save_runtime_config()

            flash = "Impostazioni salvate: intervallo {}s, UP={}, DOWN={}.".format(ci, up, dn)
            http_response(conn, body=render_page(targets, states, flash))
            conn.close(); return targets, states

        elif path == "/reboot":
            if not require_auth_or_deny():
                conn.close(); return targets, states
            http_response(conn, body="Reboot in corso… fra 2 secondi.")
            try: conn.close()
            except: pass
            try: notify("♻️ Reboot richiesto dalla web UI.", silent=True)
            except: pass
            try:
                global SERVER_SOCK
                if SERVER_SOCK: SERVER_SOCK.close()
            except: pass
            SERVER_SOCK = None
            try:
                wlan = network.WLAN(network.STA_IF); wlan.active(False)
            except: pass
            time.sleep(2)
            machine.reset()

        # --- Fallback 404 per QUALSIASI altra rotta ---
        http_response(conn, "404 Not Found", body="Not Found")
        conn.close()
        return targets, states

    except Exception as e:
        print("[HTTP] serve error:", repr(e))
        try:
            if SERVER_SOCK:
                SERVER_SOCK.close()
        except:
            pass
        SERVER_SOCK = None
        gc.collect()
        time.sleep(0.1)
        return targets, states

    except Exception as e:
        print("[HTTP] serve error:", repr(e))
        try:
            if SERVER_SOCK:
                SERVER_SOCK.close()
        except:
            pass
        SERVER_SOCK = None
        gc.collect()
        time.sleep(0.1)
        return targets, states

# =======================
# ===== MAIN LOOP =======
# =======================
def main():
    print("[PicoUptime] starting...")
    while not wifi_connect(CONFIG["WIFI_SSID"], CONFIG["WIFI_PASSWORD"]):
        time.sleep(3)

    load_runtime_config()
    load_telegram_config()

    targets = load_targets()
    print("[DEBUG] targets dopo load:", targets)  #<- Commenta per debug
    print("[DEBUG] targets type:", type(targets))  #<- Commenta per debug
    
    if targets is None or not isinstance(targets, list):
        print("[CFG] targets is None or invalid, using empty list")
        targets = []
    
    states = [{"status": None, "oks": 0, "fails": 0, "last_code": None} for _ in targets]
    print("[DEBUG] states inizializzati:", len(states))  #<- Commenta per debug


    last_check_ms = time.ticks_ms()
    wifi_fail_count = 0

    while True:
        wlan = network.WLAN(network.STA_IF)
        if not wlan.isconnected():
            wifi_fail_count += 1
            print("[WiFi] lost, retry", wifi_fail_count)
            wifi_connect(CONFIG["WIFI_SSID"], CONFIG["WIFI_PASSWORD"])
            if wifi_fail_count > 10:
                print("[WiFi] too many fails, rebooting...")
                notify("⚠️ Riavvio automatico: Wi-Fi non disponibile.", silent=True)
                time.sleep(2)
                machine.reset()
        else:
            wifi_fail_count = 0

        targets, states = serve_once(targets, states)

        now = time.ticks_ms()
        interval_ms = CONFIG["CHECK_INTERVAL"] * 1000
        if time.ticks_diff(now, last_check_ms) >= interval_ms:
            last_check_ms = now

            if len(states) != len(targets):
                states = [{"status": None, "oks": 0, "fails": 0, "last_code": None} for _ in targets]

            for i, t in enumerate(targets):
                try:
                    if t["mode"] == "http":
                        ok, code = check_http(t["url"])
                        states[i]["last_code"] = code
                    elif t["mode"] == "tcp":
                        ok = check_tcp(t["host"], t["port"])
                        states[i]["last_code"] = None
                    else:
                        ok, rtt = check_ping(t["host"])
                        states[i]["last_code"] = rtt
                except:
                    ok = False
                    states[i]["last_code"] = None

                prev = states[i]["status"]
                silent = bool(t.get("silent", False))

                if ok:
                    states[i]["oks"] += 1
                    states[i]["fails"] = 0

                    if prev is None:
                        if states[i]["oks"] >= CONFIG["UP_THRESHOLD"]:
                            states[i]["status"] = True
                        continue

                    if prev is False and states[i]["oks"] >= CONFIG["UP_THRESHOLD"]:
                        states[i]["status"] = True
                        notify("✅ {} è ONLINE".format(t.get("name", "servizio")), silent=silent)

                else:
                    states[i]["fails"] += 1
                    states[i]["oks"] = 0

                    if prev is None:
                        if states[i]["fails"] >= CONFIG["DOWN_THRESHOLD"]:
                            states[i]["status"] = False
                        continue

                    if prev is True and states[i]["fails"] >= CONFIG["DOWN_THRESHOLD"]:
                        states[i]["status"] = False
                        notify("❌ {} è OFFLINE".format(t.get("name", "servizio")), silent=silent)

        time.sleep(0.05)

try:
    main()
except KeyboardInterrupt:
    # Interruzione manuale da Thonny
    pass
except Exception as e:
    try:
        print("[FATAL] eccezione fuori da main:", repr(e))
    except:
        pass
    time.sleep(2)      #<- Commenta per debug
    machine.reset()    #<- Commenta per debug

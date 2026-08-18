# main.py — Pico Uptime v1.1.9 (Raspberry Pi Pico W, MicroPython)
# - Web UI con Basic Auth
# - Monitor HTTP / TCP / PING (ICMP)
# - Notifiche Telegram multi-chat su variazioni UP/DOWN
# - Soglie e intervallo configurabili da UI (persistenti)
# - Notifiche silenziose/sonore per-target + pagina test notifiche
# - Chat Telegram selezionabili per-target + configurazione da UI
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

# Diagnostica leggera
APP_UPTIME_MS = 0
APP_LAST_TICK_MS = time.ticks_ms()
LAST_POLL_UPTIME_MS = None


def runtime_tick():
    global APP_UPTIME_MS, APP_LAST_TICK_MS
    now = time.ticks_ms()
    delta = time.ticks_diff(now, APP_LAST_TICK_MS)
    if delta > 0:
        APP_UPTIME_MS += delta
    APP_LAST_TICK_MS = now
    return APP_UPTIME_MS


def fmt_age(ms):
    if ms is None:
        return "in attesa"
    sec = max(0, int(ms // 1000))
    if sec < 60:
        return "{}s".format(sec)
    minutes = sec // 60
    if minutes < 60:
        return "{}m".format(minutes)
    hours = minutes // 60
    if hours < 48:
        return "{}h {}m".format(hours, minutes % 60)
    return "{}g {}h".format(hours // 24, hours % 24)


def compact_diag(target_count):
    now_ms = runtime_tick()

    try:
        wlan = network.WLAN(network.STA_IF)
        ip = wlan.ifconfig()[0] if wlan.isconnected() else "offline"
        try:
            rssi = "{} dBm".format(int(wlan.status("rssi")))
        except:
            rssi = "n/d"
    except:
        ip = "n/d"
        rssi = "n/d"

    try:
        ram = "{} KB".format(int(gc.mem_free() // 1024))
    except:
        ram = "n/d"

    if LAST_POLL_UPTIME_MS is None:
        poll = "in attesa"
    else:
        poll = "{} fa".format(fmt_age(max(0, now_ms - LAST_POLL_UPTIME_MS)))

    return (
        "<b>Sistema</b> · uptime {} · Wi-Fi {} · IP {} · RAM {} · "
        "{} target · ultimo polling {}"
    ).format(
        fmt_age(now_ms), html_escape(rssi), html_escape(str(ip)),
        html_escape(ram), target_count, html_escape(poll)
    )

# =======================
# ======= CONFIG ========
# =======================
CONFIG = {
    "WIFI_SSID": "your_wifi_ssid",
    "WIFI_PASSWORD": "your_wifi_password",

    "HTTP_PORT": 8080,

    "CHECK_INTERVAL": 15,    # secondi
    "DOWN_THRESHOLD": 2,     # consecutivi KO per marcare DOWN
    "UP_THRESHOLD": 2,       # consecutivi OK per marcare UP

    # Telegram viene caricato da telegram.json.
    # I valori qui sotto sono solo fallback sicuri.
    "TELEGRAM_ENABLED": True,
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHATS": [],
    "TELEGRAM_DEFAULT_CHAT_KEYS": [],

    "TARGETS_FILE": "targets.json",
}

# ======= AUTH ========
# genera l'hash con:
#   python - <<'PY'
#   import hashlib; print(hashlib.sha256(b"tuaPasswordQui").hexdigest())
#   PY
AUTH = {
    "USER": "admin",
    "PASS_SHA256": "your_sha256_password_hash",
    "MAX_FAILS": 5,
    "BLOCK_SECONDS": 30,
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
            print("[CFG] runtime loaded:", ci, up, dn)
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
        print("[CFG] saved to file")
        return True
    except Exception as e:
        print("[CFG] save error:", repr(e))
        return False

# =======================
# == TELEGRAM CONFIG IO ==
# =======================
def normalize_chat_key(value, fallback="chat"):
    """Crea una chiave breve/stabile utilizzabile anche nei nomi dei campi HTML."""
    raw = str(value or "").strip().lower()
    out = []
    for ch in raw:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in ("_", "-"):
            out.append(ch)
        elif ch in (" ", ".", "/", "\\"):
            out.append("_")
    key = "".join(out).strip("_-")
    return key or fallback


def get_telegram_chat(key):
    key = str(key or "")
    for chat in CONFIG.get("TELEGRAM_CHATS", []):
        if str(chat.get("key", "")) == key:
            return chat
    return None


def get_default_chat_keys():
    valid = []
    for key in CONFIG.get("TELEGRAM_DEFAULT_CHAT_KEYS", []):
        chat = get_telegram_chat(key)
        if chat is not None and key not in valid:
            valid.append(key)

    if valid:
        return valid

    # Se non è ancora stata scelta una chat predefinita, usa la prima attiva.
    for chat in CONFIG.get("TELEGRAM_CHATS", []):
        if chat.get("enabled", True):
            return [str(chat.get("key", ""))]
    return []


def load_telegram_config():
    try:
        if TELEGRAM_CONFIG_FILE not in os.listdir():
            print("[TGCFG] no telegram config file found")
            return

        with open(TELEGRAM_CONFIG_FILE, "r") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            print("[TGCFG] invalid config, using defaults")
            return

        if "TELEGRAM_ENABLED" in data:
            CONFIG["TELEGRAM_ENABLED"] = bool(data["TELEGRAM_ENABLED"])

        if data.get("TELEGRAM_BOT_TOKEN"):
            candidate_token = str(data["TELEGRAM_BOT_TOKEN"]).strip()
            if telegram_token_looks_valid(candidate_token):
                CONFIG["TELEGRAM_BOT_TOKEN"] = candidate_token
            else:
                print("[TGCFG] bot token present but invalid; re-enter it from the Telegram page")

        chats = []
        raw_chats = data.get("TELEGRAM_CHATS")

        # Nuovo formato multi-chat.
        if isinstance(raw_chats, list):
            used = []
            for idx, item in enumerate(raw_chats):
                if not isinstance(item, dict):
                    continue
                chat_id = str(item.get("chat_id", "")).strip()
                if not chat_id:
                    continue

                key = normalize_chat_key(item.get("key", ""), "chat{}".format(idx + 1))
                base_key = key
                n = 2
                while key in used:
                    key = "{}{}".format(base_key, n)
                    n += 1
                used.append(key)

                chats.append({
                    "key": key,
                    "name": str(item.get("name", key)).strip() or key,
                    "chat_id": chat_id,
                    "enabled": bool(item.get("enabled", True)),
                })

        # Migrazione trasparente dal vecchio telegram.json con TELEGRAM_CHAT_ID.
        elif data.get("TELEGRAM_CHAT_ID"):
            chats = [{
                "key": "default",
                "name": "Default",
                "chat_id": str(data.get("TELEGRAM_CHAT_ID")),
                "enabled": True,
            }]
            print("[TGCFG] legacy single-chat config loaded")

        CONFIG["TELEGRAM_CHATS"] = chats

        defaults = data.get("DEFAULT_CHAT_KEYS", data.get("TELEGRAM_DEFAULT_CHAT_KEYS", []))
        if not isinstance(defaults, list):
            defaults = []

        clean_defaults = []
        for key in defaults:
            key = str(key)
            if get_telegram_chat(key) is not None and key not in clean_defaults:
                clean_defaults.append(key)

        if not clean_defaults and chats:
            clean_defaults = [chats[0]["key"]]

        CONFIG["TELEGRAM_DEFAULT_CHAT_KEYS"] = clean_defaults
        print("[TGCFG] loaded:", len(chats), "chat(s)")

    except Exception as e:
        print("[TGCFG] load error:", repr(e))


def save_telegram_config():
    try:
        data = {
            "TELEGRAM_ENABLED": bool(CONFIG.get("TELEGRAM_ENABLED")),
            "TELEGRAM_BOT_TOKEN": CONFIG.get("TELEGRAM_BOT_TOKEN", ""),
            "TELEGRAM_CHATS": CONFIG.get("TELEGRAM_CHATS", []),
            "DEFAULT_CHAT_KEYS": get_default_chat_keys(),
        }
        with open(TELEGRAM_CONFIG_FILE, "w") as f:
            json.dump(data, f)
        CONFIG["TELEGRAM_DEFAULT_CHAT_KEYS"] = data["DEFAULT_CHAT_KEYS"]
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
    except Exception as e:
        print("[CFG] load_targets error:", repr(e))
        data = []

    out = []
    for t in data:
        if not isinstance(t, dict):
            continue

        mode = t.get("mode")
        name = t.get("name", "target")
        silent = bool(t.get("silent", t.get("mute", False)))

        item = {
            "name": name,
            "mode": mode,
            "silent": silent,
        }

        # Se la chiave è assente il target è "legacy" e usa le chat predefinite.
        # Se è presente ed è [], le notifiche sono volutamente disabilitate per quel target.
        if "notify_chat_keys" in t:
            keys = t.get("notify_chat_keys")
            if isinstance(keys, list):
                clean = []
                for key in keys:
                    key = str(key)
                    if key not in clean:
                        clean.append(key)
                item["notify_chat_keys"] = clean
            else:
                item["notify_chat_keys"] = []

        if mode == "http" and t.get("url"):
            item["url"] = t["url"]
            out.append(item)
        elif mode == "tcp" and t.get("host"):
            try:
                port = int(t.get("port", 0))
            except:
                port = 0
            if port > 0:
                item["host"] = t["host"]
                item["port"] = port
                out.append(item)
        elif mode == "ping" and t.get("host"):
            item["host"] = t["host"]
            out.append(item)

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
def _socket_write_all(sock, data):
    """Scrive l'intera richiesta anche se lo stream SSL effettua write parziali."""
    offset = 0
    total = len(data)
    while offset < total:
        try:
            written = sock.write(data[offset:])
        except AttributeError:
            written = sock.send(data[offset:])
        if written is None:
            # Alcune implementazioni stream restituiscono None dopo aver accettato i dati.
            return True
        if written <= 0:
            return False
        offset += written
    return True


def telegram_token_looks_valid(token):
    token = str(token or "").strip()
    if not token or ":" not in token:
        return False
    left, right = token.split(":", 1)
    return bool(left and right and left.isdigit())


def telegram_send(bot_token, chat_id, text, silent=False):
    host = "api.telegram.org"
    port = 443
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()

    if not telegram_token_looks_valid(bot_token):
        print("[TG] invalid bot token: configure it from the Telegram page")
        return False
    if not chat_id:
        print("[TG] invalid/empty chat id")
        return False

    path = "/bot{}/sendMessage".format(bot_token)
    payload = "chat_id={}&text={}&disable_web_page_preview=1".format(
        url_encode(chat_id),
        url_encode(text)
    )
    if silent:
        payload += "&disable_notification=true"

    # payload e request sono ASCII perché text/chat_id vengono percent-encoded.
    payload_bytes = payload.encode()
    req = (
        "POST {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        "Content-Length: {}\r\n\r\n"
    ).format(path, host, len(payload_bytes)).encode() + payload_bytes

    s = None
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(8)
        s.connect(addr)
        s = ssl.wrap_socket(s, server_hostname=host)

        if not _socket_write_all(s, req):
            print("[TG] socket write failed")
            return False

        chunks = []
        while True:
            try:
                chunk = s.read(512)
            except Exception:
                break
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(x) for x in chunks) > 4096:
                break
        resp = b"".join(chunks)

        head = b""
        body = resp
        if b"\r\n\r\n" in resp:
            head, body = resp.split(b"\r\n\r\n", 1)

        status_line = b""
        if head:
            status_line = head.split(b"\r\n", 1)[0]

        ok = b'"ok":true' in body
        if ok:
            return True

        try:
            print("[TG] API FAIL", status_line.decode(), body[:500].decode())
        except:
            print("[TG] API FAIL (raw)")
        return False

    except Exception as e:
        print("[TG] transport error:", repr(e))
        return False
    finally:
        try:
            if s:
                s.close()
        except:
            pass


def target_chat_keys(target):
    if isinstance(target, dict) and "notify_chat_keys" in target:
        keys = target.get("notify_chat_keys")
        return keys if isinstance(keys, list) else []
    return get_default_chat_keys()


def resolve_telegram_chats(chat_keys=None):
    if chat_keys is None:
        chat_keys = get_default_chat_keys()

    out = []
    for key in chat_keys:
        chat = get_telegram_chat(key)
        if chat is None or not chat.get("enabled", True):
            continue
        # evita duplicati
        duplicate = False
        for current in out:
            if current.get("key") == chat.get("key"):
                duplicate = True
                break
        if not duplicate:
            out.append(chat)
    return out


def notify(text, silent=False, chat_keys=None):
    if not CONFIG.get("TELEGRAM_ENABLED"):
        return 0
    bot_token = CONFIG.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        print("[TG] bot token not configured")
        return 0

    chats = resolve_telegram_chats(chat_keys)
    sent = 0


    if not chats:
        print("[TG] no enabled/resolvable chat for this target")
        return 0

    for chat in chats:
        key = str(chat.get("key", "?"))
        cid = str(chat.get("chat_id", ""))

        try:
            if telegram_send(bot_token, cid, text, silent=silent):
                sent += 1
            else:
                print("[TG] delivery failed ->", key)
        except Exception as e:
            print("[TG] notify error ->", key, repr(e))
        gc.collect()
        time.sleep(0.15)

    return sent

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
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        if body_bytes is None:
            body_bytes = b""
        hdrs = [
            "HTTP/1.1 {}".format(status),
            "Server: PicoUptime",
            "Content-Type: {}".format(ctype),
            "Content-Length: {}".format(len(body_bytes)),
            "Connection: close",
        ]
        if extra_headers:
            hdrs.extend(extra_headers)
        conn.sendall(("\r\n".join(hdrs) + "\r\n\r\n").encode())
        conn.sendall(body_bytes)
    except:
        pass

# =======================
# ======= WEB UI ========
# =======================
def ui_shell(title, body):
    template = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ - Pico Uptime</title>
<style>
:root{--bg:#0b1020;--panel:#131a2a;--panel2:#182235;--line:#26334c;--text:#e9eef8;--muted:#95a3bb;--good:#3ddc97;--bad:#ff6b6b;--warn:#ffd166;--accent:#6ea8fe;--shadow:0 12px 30px rgba(0,0,0,.22)}
*{box-sizing:border-box}
body{margin:0;background:#000;color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:22px 14px 36px}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:10px}.brand h1{font-size:22px;margin:0}.brand small{display:block;color:var(--muted);margin-top:2px}
.logo{width:42px;height:42px;display:grid;place-items:center;border-radius:12px;background:var(--panel2);border:1px solid var(--line);box-shadow:var(--shadow);font-size:22px}
.nav{display:flex;gap:8px;flex-wrap:wrap}
a{color:var(--accent);text-decoration:none}.btn,button{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--line);border-radius:9px;padding:8px 11px;background:var(--panel2);color:var(--text);text-decoration:none;cursor:pointer;font:inherit}.btn:hover,button:hover{border-color:#49658f}.btn.danger{color:#ffb0b0}.btn.good{color:#b9f5d8}
.panel{background:rgba(19,26,42,.96);border:1px solid var(--line);border-radius:14px;padding:15px;box-shadow:var(--shadow);margin:12px 0}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:13px}.stat b{display:block;font-size:22px;margin-top:3px}.muted,.small{color:var(--muted);font-size:12px}
.goodtxt{color:var(--good)}.badtxt{color:var(--bad)}.warntxt{color:var(--warn)}
.flash{padding:11px 13px;border-radius:10px;background:#332d16;border:1px solid #62582a;color:#ffe99a;margin:10px 0}
.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse;min-width:760px;background:var(--panel)}th,td{padding:11px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}th{font-size:12px;color:var(--muted);font-weight:600;background:#11192a}tr:last-child td{border-bottom:0}
.status{display:inline-flex;align-items:center;gap:6px;padding:5px 8px;border-radius:999px;font-size:12px;font-weight:700}.status.up{background:#123728;color:#8cf0bf}.status.down{background:#421d27;color:#ffb1bc}.status.unknown{background:#2d3240;color:#c1c9d7}
.badge{display:inline-block;padding:4px 7px;border:1px solid var(--line);border-radius:999px;background:#101829;color:#c7d5ed;font-size:11px;margin:2px 3px 2px 0}.badge.off{opacity:.5}
code{background:#0b1220;border:1px solid #202c43;border-radius:6px;padding:3px 5px;color:#dce7fa}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
label{display:block;color:#cbd6e8;font-size:13px;margin:8px 0 4px}input,select{width:100%;padding:9px 10px;border-radius:8px;border:1px solid var(--line);background:#0e1626;color:var(--text);font:inherit;outline:none}input:focus,select:focus{border-color:#557fb9}
.checks{display:flex;gap:8px;flex-wrap:wrap}.check{display:flex;align-items:center;gap:7px;padding:7px 9px;border:1px solid var(--line);border-radius:9px;background:#101829;font-size:12px}.check input{width:auto;margin:0}
.actions{display:flex;gap:6px;flex-wrap:wrap}.section-title{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px}.section-title h2{font-size:17px;margin:0}
hr{border:0;border-top:1px solid var(--line);margin:14px 0}
@media(max-width:760px){.stats{grid-template-columns:1fr 1fr}.grid2,.grid3{grid-template-columns:1fr}.wrap{padding-top:14px}.brand h1{font-size:19px}}
</style>
</head>
<body><div class="wrap">
<div class="top">
  <div class="brand"><div class="logo">🧭</div><div><h1>Pico Uptime</h1><small>MicroPython monitor</small></div></div>
  <div class="nav">
    <a class="btn" href="/">Dashboard</a>
    <a class="btn" href="/telegram">Telegram</a>
    <a class="btn" href="/settings">Impostazioni</a>
    <a class="btn danger" href="/reboot" onclick="return confirm('Riavviare il dispositivo?')">Riavvia</a>
  </div>
</div>
__BODY__
</div></body></html>"""
    return template.replace("__TITLE__", html_escape(title)).replace("__BODY__", body)


def flash_html(flash):
    return "" if not flash else "<div class='flash'>{}</div>".format(html_escape(flash))


def telegram_status_text():
    if not CONFIG.get("TELEGRAM_ENABLED"):
        return "OFF"
    if not CONFIG.get("TELEGRAM_BOT_TOKEN"):
        return "Token assente"
    return "{} chat".format(len(CONFIG.get("TELEGRAM_CHATS", [])))


def chat_label(key):
    chat = get_telegram_chat(key)
    if chat is None:
        return key
    return str(chat.get("name", key))


def render_chat_badges(target):
    keys = target_chat_keys(target)
    if not keys:
        return "<span class='muted'>Nessuna</span>"

    parts = []
    for key in keys:
        chat = get_telegram_chat(key)
        if chat is None:
            parts.append("<span class='badge off'>{}</span>".format(html_escape(str(key))))
        else:
            css = "badge" if chat.get("enabled", True) else "badge off"
            parts.append("<span class='{}'>{}</span>".format(css, html_escape(str(chat.get("name", key)))))
    if "notify_chat_keys" not in target:
        parts.append("<span class='badge'>default</span>")
    return "".join(parts)


def render_chat_checkboxes(selected_keys=None):
    if selected_keys is None:
        selected_keys = get_default_chat_keys()

    chats = CONFIG.get("TELEGRAM_CHATS", [])
    if not chats:
        return "<span class='muted'>Nessuna chat configurata. <a href='/telegram'>Aggiungine una</a>.</span>"

    rows = []
    for chat in chats:
        key = str(chat.get("key", ""))
        checked = " checked" if key in selected_keys else ""
        state = "" if chat.get("enabled", True) else " · disattivata"
        rows.append(
            "<label class='check'><input type='checkbox' name='chat_{}' value='1'{}> {}{}</label>".format(
                html_escape(key), checked, html_escape(str(chat.get("name", key))), html_escape(state)
            )
        )
    return "<div class='checks'>{}</div>".format("".join(rows))


def selected_chat_keys_from_params(params):
    out = []
    for chat in CONFIG.get("TELEGRAM_CHATS", []):
        key = str(chat.get("key", ""))
        if params.get("chat_" + key, "") == "1":
            out.append(key)
    return out


def target_probe_signature(target):
    """Identifica solo cio' che cambia realmente il probe di rete."""
    mode = str(target.get("mode", ""))
    if mode == "http":
        return (mode, str(target.get("url", "")))
    if mode == "tcp":
        return (mode, str(target.get("host", "")), int(target.get("port", 0) or 0))
    return (mode, str(target.get("host", "")))


def target_endpoint(target):
    if target.get("mode") == "http":
        return str(target.get("url", ""))
    if target.get("mode") == "tcp":
        return "{}:{}".format(target.get("host", ""), target.get("port", ""))
    return "{} (ICMP)".format(target.get("host", ""))


def render_page(targets, states, flash=""):
    targets = targets if isinstance(targets, list) else []
    states = states if isinstance(states, list) else []

    up_count = 0
    down_count = 0
    unknown_count = 0
    rows = []

    for i, target in enumerate(targets):
        state = states[i] if i < len(states) else {}
        status = state.get("status")
        code = state.get("last_code")

        if status is True:
            up_count += 1
            status_html = "<span class='status up'>● UP</span>"
        elif status is False:
            down_count += 1
            status_html = "<span class='status down'>● DOWN</span>"
        else:
            unknown_count += 1
            status_html = "<span class='status unknown'>● UNKNOWN</span>"

        detail = ""
        if target.get("mode") == "ping" and code is not None:
            detail = "<span class='small'> {} ms</span>".format(code)
        elif target.get("mode") == "http" and code is not None:
            detail = "<span class='small'> HTTP {}</span>".format(code)

        silent = bool(target.get("silent", False))
        notif = "<a class='btn' href='/notifymode?i={}'>{}</a>".format(
            i, "🔕 Silenziosa" if silent else "🔔 Sonora"
        )

        rows.append(
            "<tr>"
            "<td><b>{}</b><div class='small'>{}</div></td>"
            "<td><span class='badge'>{}</span></td>"
            "<td><code>{}</code></td>"
            "<td>{}{}</td>"
            "<td>{}</td>"
            "<td>{}</td>"
            "<td><div class='actions'>"
            "<a class='btn' href='/test?i={}'>Verifica</a><a class='btn' href='/notify?i={}&mode=normal'>Test TG</a>"
            "<a class='btn' href='/edit?i={}'>Modifica</a>"
            "<a class='btn danger' href='/del?i={}' onclick='return confirm(\"Eliminare questo target?\")'>Elimina</a>"
            "</div></td>"
            "</tr>".format(
                html_escape(str(target.get("name", "target"))),
                "sonora" if not silent else "silenziosa",
                html_escape(str(target.get("mode", "-")).upper()),
                html_escape(target_endpoint(target)),
                status_html, detail,
                notif,
                render_chat_badges(target),
                i, i, i, i
            )
        )

    if not rows:
        rows.append("<tr><td colspan='7'><span class='muted'>Nessun target configurato.</span></td></tr>")

    add_chat_html = render_chat_checkboxes(None)

    body = """
__FLASH__
<div class="stats">
  <div class="stat"><span class="muted">Online</span><b class="goodtxt">__UP__</b></div>
  <div class="stat"><span class="muted">Offline</span><b class="badtxt">__DOWN__</b></div>
  <div class="stat"><span class="muted">Da inizializzare</span><b>__UNKNOWN__</b></div>
  <div class="stat"><span class="muted">Telegram</span><b>__TG__</b></div>
</div>

<div class="panel small" style="padding:9px 12px;margin:8px 0">__DIAG__</div>

<div class="panel">
  <div class="section-title"><h2>Target monitorati</h2><span class="small">Polling __CI__s · UP __UPTH__ / DOWN __DNTH__</span></div>
  <div class="tablewrap">
  <table>
    <thead><tr><th>Nome</th><th>Tipo</th><th>Endpoint</th><th>Stato</th><th>Avviso</th><th>Chat</th><th>Azioni</th></tr></thead>
    <tbody>__ROWS__</tbody>
  </table>
  </div>
</div>

<div class="panel">
  <div class="section-title"><h2>Aggiungi target</h2><span class="small">Le chat selezionate riceveranno UP/DOWN</span></div>
  <form action="/add" method="get">
    <div class="grid2">
      <div><label>Nome</label><input name="name" required placeholder="Router sede"></div>
      <div><label>Tipo</label>
        <select name="mode" id="mode" onchange="modeChange(this.value)">
          <option value="ping">PING (ICMP)</option>
          <option value="tcp">TCP</option>
          <option value="http">HTTP/HTTPS</option>
        </select>
      </div>
    </div>
    <div id="pingFields"><label>Host</label><input name="host_ping" placeholder="192.168.1.1"></div>
    <div id="tcpFields" style="display:none"><div class="grid2"><div><label>Host</label><input name="host_tcp" placeholder="192.168.1.1"></div><div><label>Porta</label><input type="number" min="1" max="65535" name="port" placeholder="443"></div></div></div>
    <div id="httpFields" style="display:none"><label>URL</label><input name="url" placeholder="https://example.org/"></div>
    <label>Chat Telegram</label>
    __CHAT_CHECKS__
    <div style="margin-top:12px"><button type="submit">＋ Aggiungi target</button></div>
  </form>
</div>

<div class="panel">
  <div class="section-title"><h2>Strumenti</h2></div>
  <div class="actions">
    <a class="btn" href="/notify_test">Test notifiche</a>
    <a class="btn" href="/telegram">Gestisci Telegram</a>
    <a class="btn" href="/settings">Polling e soglie</a>
  </div>
</div>

<script>
function modeChange(v){
  document.getElementById('pingFields').style.display=(v==='ping')?'block':'none';
  document.getElementById('tcpFields').style.display=(v==='tcp')?'block':'none';
  document.getElementById('httpFields').style.display=(v==='http')?'block':'none';
}
</script>
"""
    body = body.replace("__FLASH__", flash_html(flash))
    body = body.replace("__UP__", str(up_count))
    body = body.replace("__DOWN__", str(down_count))
    body = body.replace("__UNKNOWN__", str(unknown_count))
    body = body.replace("__TG__", html_escape(telegram_status_text()))
    body = body.replace("__DIAG__", compact_diag(len(targets)))
    body = body.replace("__CI__", str(CONFIG["CHECK_INTERVAL"]))
    body = body.replace("__UPTH__", str(CONFIG["UP_THRESHOLD"]))
    body = body.replace("__DNTH__", str(CONFIG["DOWN_THRESHOLD"]))
    body = body.replace("__ROWS__", "".join(rows))
    body = body.replace("__CHAT_CHECKS__", add_chat_html)
    return ui_shell("Dashboard", body)


def render_target_edit_page(index, target, flash=""):
    selected = target_chat_keys(target)
    mode = target.get("mode", "ping")

    ping_display = "block" if mode == "ping" else "none"
    tcp_display = "block" if mode == "tcp" else "none"
    http_display = "block" if mode == "http" else "none"

    body = """
__FLASH__
<div class="panel">
  <div class="section-title"><h2>Modifica target</h2><span class="small"># __INDEX__</span></div>
  <form action="/edit_save" method="get">
    <input type="hidden" name="i" value="__INDEX__">
    <div class="grid2">
      <div><label>Nome</label><input name="name" required value="__NAME__"></div>
      <div><label>Tipo</label>
        <select name="mode" id="mode" onchange="modeChange(this.value)">
          <option value="ping" __PINGSEL__>PING (ICMP)</option>
          <option value="tcp" __TCPSEL__>TCP</option>
          <option value="http" __HTTPSEL__>HTTP/HTTPS</option>
        </select>
      </div>
    </div>
    <div id="pingFields" style="display:__PINGDISPLAY__"><label>Host</label><input name="host_ping" value="__PINGHOST__"></div>
    <div id="tcpFields" style="display:__TCPDISPLAY__"><div class="grid2"><div><label>Host</label><input name="host_tcp" value="__TCPHOST__"></div><div><label>Porta</label><input type="number" min="1" max="65535" name="port" value="__PORT__"></div></div></div>
    <div id="httpFields" style="display:__HTTPDISPLAY__"><label>URL</label><input name="url" value="__URL__"></div>
    <label>Chat Telegram</label>
    __CHAT_CHECKS__
    <label class="check" style="margin-top:10px;width:max-content"><input type="checkbox" name="silent" value="1" __SILENT__> Notifica silenziosa</label>
    <div class="actions" style="margin-top:14px"><button type="submit">Salva</button><a class="btn" href="/">Annulla</a></div>
  </form>
</div>
<script>
function modeChange(v){
  document.getElementById('pingFields').style.display=(v==='ping')?'block':'none';
  document.getElementById('tcpFields').style.display=(v==='tcp')?'block':'none';
  document.getElementById('httpFields').style.display=(v==='http')?'block':'none';
}
</script>
"""
    body = body.replace("__FLASH__", flash_html(flash))
    body = body.replace("__INDEX__", str(index))
    body = body.replace("__NAME__", html_escape(str(target.get("name", ""))))
    body = body.replace("__PINGSEL__", "selected" if mode == "ping" else "")
    body = body.replace("__TCPSEL__", "selected" if mode == "tcp" else "")
    body = body.replace("__HTTPSEL__", "selected" if mode == "http" else "")
    body = body.replace("__PINGDISPLAY__", ping_display)
    body = body.replace("__TCPDISPLAY__", tcp_display)
    body = body.replace("__HTTPDISPLAY__", http_display)
    body = body.replace("__PINGHOST__", html_escape(str(target.get("host", ""))) if mode == "ping" else "")
    body = body.replace("__TCPHOST__", html_escape(str(target.get("host", ""))) if mode == "tcp" else "")
    body = body.replace("__PORT__", html_escape(str(target.get("port", ""))))
    body = body.replace("__URL__", html_escape(str(target.get("url", ""))))
    body = body.replace("__CHAT_CHECKS__", render_chat_checkboxes(selected))
    body = body.replace("__SILENT__", "checked" if target.get("silent", False) else "")
    return ui_shell("Modifica target", body)


def render_notify_test_page(targets, flash=""):
    targets = targets if isinstance(targets, list) else []
    rows = []
    for i, target in enumerate(targets):
        keys = target_chat_keys(target)
        chat_names = ", ".join([chat_label(k) for k in keys]) if keys else "Nessuna"
        rows.append(
            "<tr><td><b>{}</b></td><td>{}</td><td>{}</td>"
            "<td><div class='actions'><a class='btn' href='/notify?i={}&mode=normal'>🔔 Sonora</a>"
            "<a class='btn' href='/notify?i={}&mode=silent'>🔕 Silenziosa</a></div></td></tr>".format(
                html_escape(str(target.get("name", "target"))),
                html_escape(chat_names),
                "Silenziosa" if target.get("silent", False) else "Sonora",
                i, i
            )
        )
    if not rows:
        rows.append("<tr><td colspan='4'><span class='muted'>Nessun target.</span></td></tr>")

    body = """
__FLASH__
<div class="panel">
  <div class="section-title"><h2>Test notifiche Telegram</h2><span class="small">Il test usa le chat assegnate al target</span></div>
  <div class="tablewrap"><table>
    <thead><tr><th>Target</th><th>Chat</th><th>Default target</th><th>Test</th></tr></thead>
    <tbody>__ROWS__</tbody>
  </table></div>
</div>
"""
    body = body.replace("__FLASH__", flash_html(flash)).replace("__ROWS__", "".join(rows))
    return ui_shell("Test notifiche", body)


def render_settings_page(flash=""):
    body = """
__FLASH__
<div class="panel">
  <div class="section-title"><h2>Polling e soglie</h2><span class="small">Applicate al volo e salvate in config.json</span></div>
  <form action="/settings" method="get">
    <div class="grid3">
      <div><label>Intervallo polling (secondi)</label><input type="number" name="ci" min="5" max="3600" value="__CI__"></div>
      <div><label>UP threshold</label><input type="number" name="up" min="1" max="10" value="__UP__"></div>
      <div><label>DOWN threshold</label><input type="number" name="dn" min="1" max="10" value="__DN__"></div>
    </div>
    <div class="actions" style="margin-top:14px"><button type="submit">Salva</button><a class="btn" href="/">Annulla</a></div>
  </form>
</div>
"""
    body = body.replace("__FLASH__", flash_html(flash))
    body = body.replace("__CI__", str(CONFIG["CHECK_INTERVAL"]))
    body = body.replace("__UP__", str(CONFIG["UP_THRESHOLD"]))
    body = body.replace("__DN__", str(CONFIG["DOWN_THRESHOLD"]))
    return ui_shell("Impostazioni", body)


def mask_chat_id(chat_id):
    value = str(chat_id or "")
    if len(value) <= 6:
        return value
    return "{}…{}".format(value[:3], value[-4:])


def render_telegram_page(flash=""):
    chat_rows = []
    defaults = get_default_chat_keys()

    for chat in CONFIG.get("TELEGRAM_CHATS", []):
        key = str(chat.get("key", ""))
        enabled = bool(chat.get("enabled", True))
        default_badge = "<span class='badge'>★ default</span>" if key in defaults else ""
        state_badge = "<span class='status up'>ATTIVA</span>" if enabled else "<span class='status unknown'>OFF</span>"
        chat_rows.append(
            "<tr><td><b>{}</b><div class='small'>{}</div></td><td><code>{}</code></td><td>{} {}</td>"
            "<td><div class='actions'>"
            "<a class='btn' href='/telegram_test?key={}'>Test</a>"
            "<a class='btn' href='/telegram_rename?key={}'>Rinomina</a>"
            "<a class='btn' href='/telegram_toggle?key={}'>{}</a>"
            "<a class='btn' href='/telegram_default?key={}'>{}</a>"
            "<a class='btn danger' href='/telegram_del?key={}' onclick='return confirm(\"Rimuovere questa chat?\")'>Elimina</a>"
            "</div></td></tr>".format(
                html_escape(str(chat.get("name", key))),
                html_escape(key),
                html_escape(mask_chat_id(chat.get("chat_id", ""))),
                state_badge, default_badge,
                url_encode(key),
                url_encode(key),
                url_encode(key), "Disattiva" if enabled else "Attiva",
                url_encode(key), "Togli default" if key in defaults else "Default",
                url_encode(key)
            )
        )

    if not chat_rows:
        chat_rows.append("<tr><td colspan='4'><span class='muted'>Nessuna chat configurata.</span></td></tr>")

    current_token = CONFIG.get("TELEGRAM_BOT_TOKEN", "")
    if telegram_token_looks_valid(current_token):
        token_state = "Configurato"
    elif current_token:
        token_state = "NON VALIDO"
    else:
        token_state = "Non configurato"
    enabled_checked = "checked" if CONFIG.get("TELEGRAM_ENABLED") else ""

    body = """
__FLASH__
<div class="stats">
  <div class="stat"><span class="muted">Telegram globale</span><b>__GLOBAL__</b></div>
  <div class="stat"><span class="muted">Bot token</span><b>__TOKENSTATE__</b></div>
  <div class="stat"><span class="muted">Chat configurate</span><b>__CHATCOUNT__</b></div>
  <div class="stat"><span class="muted">Chat default</span><b>__DEFAULTCOUNT__</b></div>
</div>

<div class="panel">
  <div class="section-title"><h2>Bot Telegram</h2><span class="small">Il token non viene mai mostrato nella pagina</span></div>
  <form action="/telegram_save" method="post">
    <label>Nuovo bot token</label>
    <input type="password" name="token" autocomplete="new-password" placeholder="Lascia vuoto per mantenere quello attuale">
    <label class="check" style="margin-top:10px;width:max-content"><input type="checkbox" name="enabled" value="1" __ENABLEDCHECK__> Notifiche Telegram abilitate</label>
    <div style="margin-top:12px"><button type="submit">Salva configurazione bot</button></div>
  </form>
</div>

<div class="panel">
  <div class="section-title"><h2>Chat</h2><span class="small">Una o più chat possono essere associate a ogni target</span></div>
  <div class="tablewrap"><table>
    <thead><tr><th>Nome</th><th>Chat ID</th><th>Stato</th><th>Azioni</th></tr></thead>
    <tbody>__CHATROWS__</tbody>
  </table></div>
</div>

<div class="panel">
  <div class="section-title"><h2>Aggiungi chat</h2></div>
  <form action="/telegram_add" method="post">
    <div class="grid3">
      <div><label>Nome</label><input name="name" required placeholder="NOC"></div>
      <div><label>Chiave</label><input name="key" placeholder="noc"><span class="small">Se vuota viene generata dal nome.</span></div>
      <div><label>Chat ID</label><input name="chat_id" required placeholder="-1001234567890"></div>
    </div>
    <div class="checks" style="margin-top:10px">
      <label class="check"><input type="checkbox" name="chat_enabled" value="1" checked> Attiva</label>
      <label class="check"><input type="checkbox" name="make_default" value="1"> Predefinita</label>
    </div>
    <div style="margin-top:12px"><button type="submit">＋ Aggiungi chat</button></div>
  </form>
</div>
"""
    body = body.replace("__FLASH__", flash_html(flash))
    body = body.replace("__GLOBAL__", "ON" if CONFIG.get("TELEGRAM_ENABLED") else "OFF")
    body = body.replace("__TOKENSTATE__", token_state)
    body = body.replace("__CHATCOUNT__", str(len(CONFIG.get("TELEGRAM_CHATS", []))))
    body = body.replace("__DEFAULTCOUNT__", str(len(defaults)))
    body = body.replace("__ENABLEDCHECK__", enabled_checked)
    body = body.replace("__CHATROWS__", "".join(chat_rows))
    return ui_shell("Telegram", body)


def render_telegram_rename_page(key, flash=""):
    chat = get_telegram_chat(key)
    if chat is None:
        return render_telegram_page("Chat non trovata.")

    body = """
__FLASH__
<div class="panel">
  <div class="section-title">
    <h2>Rinomina chat</h2>
    <span class="small">Cambia solo il nome visualizzato in Pico Uptime</span>
  </div>

  <div class="stats" style="margin-bottom:16px">
    <div class="stat"><span class="muted">Chiave interna</span><b>__KEY__</b></div>
    <div class="stat"><span class="muted">Chat ID</span><b>__CHATID__</b></div>
  </div>

  <form action="/telegram_rename_save" method="post">
    <input type="hidden" name="key" value="__KEYVALUE__">

    <label>Nome visualizzato</label>
    <input name="name" required maxlength="48" value="__NAME__" placeholder="Nome chat">

    <div class="actions" style="margin-top:14px">
      <button type="submit">Salva nuovo nome</button>
      <a class="btn" href="/telegram">Annulla</a>
    </div>
  </form>
</div>
"""
    body = body.replace("__FLASH__", flash_html(flash))
    body = body.replace("__KEY__", html_escape(str(chat.get("key", key))))
    body = body.replace("__KEYVALUE__", html_escape(str(chat.get("key", key))))
    body = body.replace("__CHATID__", html_escape(mask_chat_id(chat.get("chat_id", ""))))
    body = body.replace("__NAME__", html_escape(str(chat.get("name", key))))
    return ui_shell("Rinomina chat", body)


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
        print("[HTTP] listening on port", port)
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

        # Legge almeno tutti gli header HTTP.
        data = b""
        try:
            while b"\r\n\r\n" not in data and len(data) < 4096:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                data += chunk
        except:
            pass

        if not data:
            conn.close()
            return targets, states

        try:
            head, initial_body = data.split(b"\r\n\r\n", 1)
        except:
            head, initial_body = data, b""

        try:
            line = head.split(b"\r\n", 1)[0].decode()
        except:
            line = "GET / HTTP/1.1"

        parts = line.split(" ")
        method = parts[0].upper() if len(parts) > 0 else "GET"
        fullpath = parts[1] if len(parts) > 1 else "/"

        if "?" in fullpath:
            path, qs = fullpath.split("?", 1)
        else:
            path, qs = fullpath, ""

        headers = parse_headers(head + b"\r\n\r\n")
        now_ms = time.ticks_ms()

        # Per i form POST legge l'intero body urlencoded.
        if method == "POST":
            try:
                content_length = int(headers.get("content-length", "0") or "0")
            except:
                content_length = 0

            body_data = initial_body
            try:
                while len(body_data) < content_length:
                    chunk = conn.recv(min(1024, content_length - len(body_data)))
                    if not chunk:
                        break
                    body_data += chunk
            except:
                pass

            try:
                params = parse_qs(body_data[:content_length].decode())
            except:
                params = {}
        else:
            params = parse_qs(qs)

        def require_auth_or_deny():
            ok, status, blocked = check_basic_auth(headers, now_ms)
            if ok:
                return True
            if status.startswith("429"):
                http_response(
                    conn,
                    "429 Too Many Requests",
                    body="Too Many Requests. Riprova tra {}s.".format(AUTH["BLOCK_SECONDS"])
                )
            else:
                http_response(
                    conn,
                    "401 Unauthorized",
                    extra_headers=['WWW-Authenticate: Basic realm="PicoUptime"'],
                    body="Auth required."
                )
            return False

        # Favicon senza contenuti e senza autenticazione.
        if path == "/favicon.ico":
            http_response(conn, "204 No Content", body="")
            conn.close()
            return targets, states

        # Tutto il resto, inclusi 404 e pagine di configurazione, richiede login.
        if not require_auth_or_deny():
            conn.close()
            return targets, states

        if not isinstance(targets, list):
            targets = []
        if not isinstance(states, list):
            states = []

        # ================== ROUTING ==================
        if path == "/":
            http_response(conn, body=render_page(targets, states, ""))
            conn.close()
            return targets, states

        elif path == "/add":
            name = params.get("name", "").strip() or "target"
            mode = params.get("mode", "ping").strip()
            selected = selected_chat_keys_from_params(params)
            target = {
                "name": name,
                "mode": mode,
                "silent": False,
                "notify_chat_keys": selected,
            }
            flash = ""

            if mode == "http":
                url = params.get("url", "").strip()
                if url:
                    target["url"] = url
                else:
                    flash = "URL mancante."
            elif mode == "tcp":
                host = params.get("host_tcp", "").strip()
                try:
                    port = int(params.get("port", "0") or "0")
                except:
                    port = 0
                if host and 0 < port <= 65535:
                    target["host"] = host
                    target["port"] = port
                else:
                    flash = "Host o porta TCP non validi."
            elif mode == "ping":
                host = params.get("host_ping", "").strip()
                if host:
                    target["host"] = host
                else:
                    flash = "Host mancante."
            else:
                flash = "Tipo target non valido."

            if not flash:
                targets.append(target)
                save_targets(targets)
                states.append({"status": None, "oks": 0, "fails": 0, "last_code": None})
                flash = "Target '{}' aggiunto.".format(name)

            http_response(conn, body=render_page(targets, states, flash))
            conn.close()
            return targets, states

        elif path == "/edit":
            try:
                i = int(params.get("i", "-1") or "-1")
            except:
                i = -1
            if 0 <= i < len(targets):
                body = render_target_edit_page(i, targets[i], "")
            else:
                body = render_page(targets, states, "Indice target non valido.")
            http_response(conn, body=body)
            conn.close()
            return targets, states

        elif path == "/edit_save":
            try:
                i = int(params.get("i", "-1") or "-1")
            except:
                i = -1

            if not (0 <= i < len(targets)):
                http_response(conn, body=render_page(targets, states, "Indice target non valido."))
                conn.close()
                return targets, states

            old = targets[i]
            name = params.get("name", "").strip() or old.get("name", "target")
            mode = params.get("mode", old.get("mode", "ping")).strip()
            selected = selected_chat_keys_from_params(params)
            updated = {
                "name": name,
                "mode": mode,
                "silent": params.get("silent", "") == "1",
                "notify_chat_keys": selected,
            }
            error = ""

            if mode == "http":
                url = params.get("url", "").strip()
                if url:
                    updated["url"] = url
                else:
                    error = "URL mancante."
            elif mode == "tcp":
                host = params.get("host_tcp", "").strip()
                try:
                    port = int(params.get("port", "0") or "0")
                except:
                    port = 0
                if host and 0 < port <= 65535:
                    updated["host"] = host
                    updated["port"] = port
                else:
                    error = "Host o porta TCP non validi."
            elif mode == "ping":
                host = params.get("host_ping", "").strip()
                if host:
                    updated["host"] = host
                else:
                    error = "Host mancante."
            else:
                error = "Tipo target non valido."

            if error:
                http_response(conn, body=render_target_edit_page(i, old, error))
            else:
                probe_changed = target_probe_signature(old) != target_probe_signature(updated)
                targets[i] = updated
                save_targets(targets)

                # Cambiare chat, nome o modalita' sonora/silenziosa non deve
                # riportare il monitor a UNKNOWN. Reset solo se cambia il probe.
                if probe_changed and i < len(states):
                    states[i] = {"status": None, "oks": 0, "fails": 0, "last_code": None}

                http_response(conn, body=render_page(targets, states, "Target '{}' aggiornato.".format(name)))
            conn.close()
            return targets, states

        elif path == "/del":
            try:
                i = int(params.get("i", "-1") or "-1")
            except:
                i = -1
            if 0 <= i < len(targets):
                name = targets[i].get("name", "target")
                targets.pop(i)
                save_targets(targets)
                if i < len(states):
                    states.pop(i)
                flash = "Target '{}' eliminato.".format(name)
            else:
                flash = "Indice target non valido."
            http_response(conn, body=render_page(targets, states, flash))
            conn.close()
            return targets, states

        elif path == "/test":
            try:
                i = int(params.get("i", "-1") or "-1")
            except:
                i = -1

            if 0 <= i < len(targets):
                target = targets[i]
                ok = False
                code = None
                if target.get("mode") == "http":
                    ok, code = check_http(target.get("url", ""))
                elif target.get("mode") == "tcp":
                    ok = check_tcp(target.get("host", ""), int(target.get("port", 0)))
                else:
                    ok, code = check_ping(target.get("host", ""))

                if i < len(states):
                    states[i]["last_code"] = code

                detail = ""
                if target.get("mode") == "ping" and code is not None:
                    detail = " ({} ms)".format(code)
                elif target.get("mode") == "http" and code is not None:
                    detail = " (HTTP {})".format(code)

                flash = "Verifica manuale: {} {}{} — non modifica stato/soglie e non invia alert.".format(
                    target.get("name", "target"),
                    "UP ✅" if ok else "DOWN ❌",
                    detail
                )
            else:
                flash = "Indice target non valido."

            http_response(conn, body=render_page(targets, states, flash))
            conn.close()
            return targets, states

        elif path == "/notify_test":
            http_response(conn, body=render_notify_test_page(targets))
            conn.close()
            return targets, states

        elif path == "/notifymode":
            try:
                i = int(params.get("i", "-1") or "-1")
            except:
                i = -1
            if 0 <= i < len(targets):
                target = targets[i]
                target["silent"] = not bool(target.get("silent", False))
                save_targets(targets)
                flash = "Notifica {} per '{}'.".format(
                    "silenziosa" if target["silent"] else "sonora",
                    target.get("name", "target")
                )
            else:
                flash = "Indice target non valido."
            http_response(conn, body=render_page(targets, states, flash))
            conn.close()
            return targets, states

        elif path == "/notify":
            try:
                i = int(params.get("i", "-1") or "-1")
            except:
                i = -1
            mode = params.get("mode", "normal")
            if 0 <= i < len(targets):
                target = targets[i]
                silent = mode == "silent"
                keys = target_chat_keys(target)
                sent = notify(
                    "{} Test notifica — {}".format("🔕" if silent else "🔔", target.get("name", "target")),
                    silent=silent,
                    chat_keys=keys
                )
                flash = "Test inviato a {} chat per '{}'.".format(sent, target.get("name", "target"))
            else:
                flash = "Indice target non valido."
            http_response(conn, body=render_notify_test_page(targets, flash))
            conn.close()
            return targets, states

        elif path == "/telegram":
            http_response(conn, body=render_telegram_page(""))
            conn.close()
            return targets, states

        elif path == "/telegram_save":
            token = params.get("token", "").strip()
            if token:
                CONFIG["TELEGRAM_BOT_TOKEN"] = token
            CONFIG["TELEGRAM_ENABLED"] = params.get("enabled", "") == "1"
            save_telegram_config()
            http_response(conn, body=render_telegram_page("Configurazione bot aggiornata."))
            conn.close()
            return targets, states

        elif path == "/telegram_add":
            name = params.get("name", "").strip()
            chat_id = params.get("chat_id", "").strip()
            requested_key = params.get("key", "").strip()
            key = normalize_chat_key(requested_key or name, "chat")

            if not name or not chat_id:
                flash = "Nome e Chat ID sono obbligatori."
            elif get_telegram_chat(key) is not None:
                flash = "La chiave '{}' esiste già.".format(key)
            else:
                chat = {
                    "key": key,
                    "name": name,
                    "chat_id": chat_id,
                    "enabled": params.get("chat_enabled", "") == "1",
                }
                CONFIG["TELEGRAM_CHATS"].append(chat)

                defaults = get_default_chat_keys()
                if params.get("make_default", "") == "1" or not defaults:
                    if key not in defaults:
                        defaults.append(key)
                    CONFIG["TELEGRAM_DEFAULT_CHAT_KEYS"] = defaults

                save_telegram_config()
                flash = "Chat '{}' aggiunta.".format(name)

            http_response(conn, body=render_telegram_page(flash))
            conn.close()
            return targets, states

        elif path == "/telegram_rename":
            key = params.get("key", "")
            chat = get_telegram_chat(key)
            if chat is None:
                http_response(conn, body=render_telegram_page("Chat non trovata."))
            else:
                http_response(conn, body=render_telegram_rename_page(key, ""))
            conn.close()
            return targets, states

        elif path == "/telegram_rename_save":
            key = params.get("key", "")
            new_name = params.get("name", "").strip()
            chat = get_telegram_chat(key)

            if chat is None:
                flash = "Chat non trovata."
            elif not new_name:
                http_response(conn, body=render_telegram_rename_page(key, "Il nome non può essere vuoto."))
                conn.close()
                return targets, states
            else:
                old_name = str(chat.get("name", key))
                chat["name"] = new_name[:48]
                save_telegram_config()
                flash = "Chat '{}' rinominata in '{}'.".format(old_name, chat["name"])

            http_response(conn, body=render_telegram_page(flash))
            conn.close()
            return targets, states

        elif path == "/telegram_toggle":
            key = params.get("key", "")
            chat = get_telegram_chat(key)
            if chat is None:
                flash = "Chat non trovata."
            else:
                chat["enabled"] = not bool(chat.get("enabled", True))
                save_telegram_config()
                flash = "Chat '{}' {}.".format(
                    chat.get("name", key),
                    "attivata" if chat["enabled"] else "disattivata"
                )
            http_response(conn, body=render_telegram_page(flash))
            conn.close()
            return targets, states

        elif path == "/telegram_default":
            key = params.get("key", "")
            chat = get_telegram_chat(key)
            if chat is None:
                flash = "Chat non trovata."
            else:
                defaults = list(CONFIG.get("TELEGRAM_DEFAULT_CHAT_KEYS", []))
                if key in defaults:
                    if len(defaults) <= 1:
                        flash = "Deve rimanere almeno una chat predefinita."
                    else:
                        defaults.remove(key)
                        CONFIG["TELEGRAM_DEFAULT_CHAT_KEYS"] = defaults
                        save_telegram_config()
                        flash = "'{}' rimossa dalle chat predefinite.".format(chat.get("name", key))
                else:
                    defaults.append(key)
                    CONFIG["TELEGRAM_DEFAULT_CHAT_KEYS"] = defaults
                    save_telegram_config()
                    flash = "'{}' aggiunta alle chat predefinite.".format(chat.get("name", key))
            http_response(conn, body=render_telegram_page(flash))
            conn.close()
            return targets, states

        elif path == "/telegram_del":
            key = params.get("key", "")
            chat = get_telegram_chat(key)
            if chat is None:
                flash = "Chat non trovata."
            else:
                name = chat.get("name", key)
                new_chats = []
                for current in CONFIG.get("TELEGRAM_CHATS", []):
                    if current.get("key") != key:
                        new_chats.append(current)
                CONFIG["TELEGRAM_CHATS"] = new_chats

                defaults = []
                for current_key in CONFIG.get("TELEGRAM_DEFAULT_CHAT_KEYS", []):
                    if current_key != key:
                        defaults.append(current_key)
                CONFIG["TELEGRAM_DEFAULT_CHAT_KEYS"] = defaults

                # Rimuove la chat anche dalle assegnazioni esplicite dei target.
                targets_changed = False
                for target in targets:
                    if "notify_chat_keys" in target and isinstance(target.get("notify_chat_keys"), list):
                        if key in target["notify_chat_keys"]:
                            target["notify_chat_keys"].remove(key)
                            targets_changed = True
                if targets_changed:
                    save_targets(targets)

                save_telegram_config()
                flash = "Chat '{}' eliminata.".format(name)

            http_response(conn, body=render_telegram_page(flash))
            conn.close()
            return targets, states

        elif path == "/telegram_test":
            key = params.get("key", "")
            chat = get_telegram_chat(key)
            if chat is None:
                flash = "Chat non trovata."
            elif not CONFIG.get("TELEGRAM_ENABLED"):
                flash = "Telegram globale è disabilitato."
            elif not CONFIG.get("TELEGRAM_BOT_TOKEN"):
                flash = "Bot token non configurato."
            else:
                ok = telegram_send(
                    CONFIG.get("TELEGRAM_BOT_TOKEN", ""),
                    chat.get("chat_id", ""),
                    "🧭 Pico Uptime — test chat '{}'".format(chat.get("name", key)),
                    silent=False
                )
                flash = "Test inviato a '{}'.".format(chat.get("name", key)) if ok else "Invio test fallito."
            http_response(conn, body=render_telegram_page(flash))
            conn.close()
            return targets, states

        # Compatibilità con il vecchio link/toggle globale.
        elif path == "/tg_toggle":
            CONFIG["TELEGRAM_ENABLED"] = not CONFIG.get("TELEGRAM_ENABLED", True)
            save_telegram_config()
            http_response(
                conn,
                body=render_page(
                    targets,
                    states,
                    "Telegram {}.".format("abilitato" if CONFIG["TELEGRAM_ENABLED"] else "disabilitato")
                )
            )
            conn.close()
            return targets, states

        elif path == "/settings":
            # GET senza parametri: mostra la pagina.
            if not params:
                http_response(conn, body=render_settings_page(""))
                conn.close()
                return targets, states

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

            ci = max(5, min(ci, 3600))
            up = max(1, min(up, 10))
            dn = max(1, min(dn, 10))

            CONFIG["CHECK_INTERVAL"] = ci
            CONFIG["UP_THRESHOLD"] = up
            CONFIG["DOWN_THRESHOLD"] = dn
            save_runtime_config()

            flash = "Impostazioni salvate: polling {}s, UP {}, DOWN {}.".format(ci, up, dn)
            http_response(conn, body=render_settings_page(flash))
            conn.close()
            return targets, states

        elif path == "/reboot":
            http_response(conn, body="Reboot in corso… fra 2 secondi.")
            try:
                conn.close()
            except:
                pass
            try:
                notify("♻️ Reboot richiesto dalla web UI.", silent=True)
            except:
                pass
            try:
                if SERVER_SOCK:
                    SERVER_SOCK.close()
            except:
                pass
            SERVER_SOCK = None
            try:
                wlan = network.WLAN(network.STA_IF)
                wlan.active(False)
            except:
                pass
            time.sleep(2)
            machine.reset()

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

# =======================
# ===== MAIN LOOP =======
# =======================
def main():
    global LAST_POLL_UPTIME_MS
    print("[PicoUptime] starting...")
    while not wifi_connect(CONFIG["WIFI_SSID"], CONFIG["WIFI_PASSWORD"]):
        time.sleep(3)

    load_runtime_config()
    load_telegram_config()

    targets = load_targets()


    # Fallback di sicurezza se targets.json non è valido.
    if targets is None or not isinstance(targets, list):
        print("[CFG] targets is None or invalid, using empty list")
        targets = []

    states = [{"status": None, "oks": 0, "fails": 0, "last_code": None} for _ in targets]


    last_check_ms = time.ticks_ms()
    wifi_fail_count = 0

    while True:
        runtime_tick()
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
                            keys = target_chat_keys(t)
                            print("[MON] UNKNOWN -> UP:", t.get("name", "servizio"), "chat:", keys)
                            notify(
                                "✅ {} è ONLINE".format(t.get("name", "servizio")),
                                silent=silent,
                                chat_keys=keys
                            )

                        continue

                    if prev is False and states[i]["oks"] >= CONFIG["UP_THRESHOLD"]:
                        states[i]["status"] = True
                        keys = target_chat_keys(t)
                        print("[MON] DOWN -> UP:", t.get("name", "servizio"), "chat:", keys)
                        notify(
                            "✅ {} è ONLINE".format(t.get("name", "servizio")),
                            silent=silent,
                            chat_keys=keys
                        )


                else:
                    states[i]["fails"] += 1
                    states[i]["oks"] = 0

                    if prev is None:
                        if states[i]["fails"] >= CONFIG["DOWN_THRESHOLD"]:
                            states[i]["status"] = False
                            keys = target_chat_keys(t)
                            print("[MON] UNKNOWN -> DOWN:", t.get("name", "servizio"), "chat:", keys)
                            notify(
                                "❌ {} è OFFLINE".format(t.get("name", "servizio")),
                                silent=silent,
                                chat_keys=keys
                            )

                        continue

                    if prev is True and states[i]["fails"] >= CONFIG["DOWN_THRESHOLD"]:
                        states[i]["status"] = False
                        keys = target_chat_keys(t)
                        print("[MON] UP -> DOWN:", t.get("name", "servizio"), "chat:", keys)
                        notify(
                            "❌ {} è OFFLINE".format(t.get("name", "servizio")),
                            silent=silent,
                            chat_keys=keys
                        )


            LAST_POLL_UPTIME_MS = runtime_tick()

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

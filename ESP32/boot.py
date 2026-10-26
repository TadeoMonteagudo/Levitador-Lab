# ============================================================
# boot.py - Wi-Fi robusto ante soft reboot + modem sleep
# ============================================================
import network
import time

WIFI_SSID = "MGSV"
WIFI_PASS = "Dream12.,"


def conectar_wifi(timeout_s=25):
    sta = network.WLAN(network.STA_IF)

    # --- Limpieza de estado previo (clave en soft reboot) ---
    try:
        sta.active(False)
    except Exception:
        pass
    try:
        sta.disconnect()
    except Exception:
        pass
    time.sleep(0.5)

    # --- Activar interfaz limpia ---
    sta.active(True)
    time.sleep(0.3)

    # Sin power save mientras conectamos (evita timeouts de DNS/assoc)
    try:
        sta.config(pm=network.WLAN.PM_NONE)
    except Exception:
        pass

    if sta.isconnected():
        print("[WIFI] Ya conectado. IP:", sta.ifconfig()[0])
        return sta

    # --- Intentos con reintento real ---
    intentos = 3
    for i in range(intentos):
        print("[WIFI] Intento {}/{} -> {}".format(i + 1, intentos, WIFI_SSID))
        try:
            sta.connect(WIFI_SSID, WIFI_PASS)
        except OSError as e:
            print("[WIFI] connect() falló:", e)
            # Reset rápido y reintentar
            try:
                sta.active(False)
                time.sleep(0.5)
                sta.active(True)
                time.sleep(0.3)
            except Exception:
                pass
            continue

        # Esperar conexión
        t = 0
        while not sta.isconnected() and t < timeout_s * 2:
            time.sleep(0.5)
            t += 1

        if sta.isconnected():
            print("[WIFI] Conectado. IP:", sta.ifconfig()[0])
            return sta

        print("[WIFI] Timeout, status:", sta.status())
        try:
            sta.disconnect()
        except Exception:
            pass
        time.sleep(1)

    print("[WIFI] ERROR: no se pudo conectar tras {} intentos".format(intentos))
    return sta


_wlan = conectar_wifi()

# Solo activar modem sleep si realmente hay conexión
try:
    if _wlan.isconnected():
        _wlan.config(pm=network.WLAN.PM_POWERSAVE)
        print("[WIFI] Modem sleep activado (PM_POWERSAVE)")
    else:
        print("[WIFI] Sin conexión: no se activa modem sleep")
except Exception as e:
    print("[WIFI] No se pudo configurar PM:", e)
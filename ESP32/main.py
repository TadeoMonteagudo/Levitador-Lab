# ============================================================
# main.py - Control PID ESP32 + MQTT
#   - Frecuencia fija 160 MHz (estable con WiFi)
#   - LOW_POWER: solo heartbeat de modo cada 20 s
#   - Reconexión WiFi con limpieza de driver
# ============================================================
import uasyncio as asyncio
from machine import Pin, PWM
import machine
import network
import json
import time
from umqtt.simple import MQTTClient

from pid import PID


# ============================================================
# 1. CONFIGURACIÓN
# ============================================================
WIFI_SSID = "MGSV"
WIFI_PASS = "Dream12.,"

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT   = 1883
CLIENT_ID   = "esp32_pid_unlam_01"
TOPIC_PUB   = b"esp32/datos"
TOPIC_SUB   = b"esp32/ordenes"

# Heartbeat en LOW_POWER (mantiene asociación WiFi sin gastar tráfico)
HEARTBEAT_LP_S = 20

# Frecuencia fija: 160 MHz es estable con WiFi y consume menos que 240.
# NO bajar a 80 MHz con WiFi activo: resetea el driver (Wifi Internal State Error).
CPU_FREQ_HZ = 160_000_000

USE_ENB_GPIO    = False   # ENB -> GPIO 21
USE_SENSOR_GATE = False   # MOSFET VCC HC-SR04 -> GPIO 22


# ============================================================
# 2. HARDWARE
# ============================================================
in3_pwm = PWM(Pin(18), freq=1000)
in4_dir = Pin(19, Pin.OUT)
in4_dir.value(0)

trigger = Pin(5, Pin.OUT)
echo    = Pin(35, Pin.IN)
trigger.value(0)

enb = None
if USE_ENB_GPIO:
    enb = Pin(21, Pin.OUT)
    enb.value(0)

sensor_pwr = None
if USE_SENSOR_GATE:
    sensor_pwr = Pin(22, Pin.OUT)
    sensor_pwr.value(0)

ALTURA_MAXIMA_CM = 48.0


# ============================================================
# 3. ESTADO
# ============================================================
pid = PID(kp=2.0, ki=0.5, kd=0.1, sp=20.0,
          pwm_min=550, pwm_max=650, pwm_base=600)
pid.salida = 0

modo = "LOW_POWER"
client = None

in3_pwm.duty(0)
altura_filtrada = 0.0

_wlan = network.WLAN(network.STA_IF)


# ============================================================
# 4. HELPERS
# ============================================================
def set_enb(active):
    if enb is not None:
        enb.value(1 if active else 0)


def set_sensor_power(active):
    if sensor_pwr is not None:
        sensor_pwr.value(1 if active else 0)


def wifi_pm(mode):
    try:
        if _wlan.active() and _wlan.isconnected():
            _wlan.config(pm=mode)
    except Exception as e:
        print("[WIFI PM] Ignorado:", e)


def wifi_reconectar():
    """Reconexión limpia: apaga, prende, conecta."""
    try:
        _wlan.active(False)
        time.sleep_ms(200)
        _wlan.active(True)
        time.sleep_ms(200)
        _wlan.connect(WIFI_SSID, WIFI_PASS)
    except Exception as e:
        print("[WIFI] Error en reconectar:", e)


# ============================================================
# 5. SENSOR
# ============================================================
def medir_altura_suelo_cm():
    trigger.value(0)
    time.sleep_us(2)
    trigger.value(1)
    time.sleep_us(10)
    trigger.value(0)

    timeout = time.ticks_us()
    while echo.value() == 0:
        if time.ticks_diff(time.ticks_us(), timeout) > 3000:
            return -1.0

    inicio_echo = time.ticks_us()
    while echo.value() == 1:
        if time.ticks_diff(time.ticks_us(), inicio_echo) > 3000:
            return -1.0
    fin_echo = time.ticks_us()

    duracion = time.ticks_diff(fin_echo, inicio_echo)
    distancia_techo = (duracion * 0.0343) / 2.0
    altura_suelo = ALTURA_MAXIMA_CM - distancia_techo

    if altura_suelo < 0.0:
        return 0.0
    if altura_suelo > ALTURA_MAXIMA_CM:
        return ALTURA_MAXIMA_CM
    return altura_suelo


# ============================================================
# 6. CALLBACK MQTT
# ============================================================
def al_recibir_mensaje(topic, msg):
    global modo
    try:
        data = json.loads(msg.decode())
        print("[MQTT RX]", data)

        if "modo" in data:
            nuevo = str(data["modo"]).upper()
            if nuevo in ("RUN", "LOW_POWER") and nuevo != modo:
                modo = nuevo
                pid.reset()

                if modo == "RUN":
                    set_enb(True)
                    set_sensor_power(True)
                    print("[MODO] RUN")
                else:
                    in3_pwm.duty(0)
                    set_enb(False)
                    set_sensor_power(False)
                    print("[MODO] LOW_POWER")
            elif nuevo not in ("RUN", "LOW_POWER"):
                print("[MODO] Desconocido:", nuevo)

        kp = data.get("kp", None)
        ki = data.get("ki", None)
        kd = data.get("kd", None)
        sp_nuevo = data.get("sp", None)

        sp_cambio = False
        if sp_nuevo is not None:
            try:
                sp_cambio = abs(float(sp_nuevo) - pid.sp) > 0.01
            except Exception:
                pass

        if sp_cambio:
            pid.actualizar(kp=kp, ki=ki, kd=kd, sp=sp_nuevo)
            pid.reset()
            print("[PID] SP -> {} (reset)".format(pid.sp))
        elif any(v is not None for v in (kp, ki, kd)):
            pid.actualizar(kp=kp, ki=ki, kd=kd)
            print("[PID] kp={} ki={} kd={}".format(pid.kp, pid.ki, pid.kd))

    except Exception as e:
        print("[MQTT] Error parseando JSON:", e)


# ============================================================
# 7. CONTROL LOOP
# ============================================================
async def control_loop():
    global altura_filtrada

    while True:
        if modo == "RUN":
            medicion = medir_altura_suelo_cm()
            if medicion >= 0:
                if altura_filtrada == 0.0:
                    altura_filtrada = medicion
                else:
                    altura_filtrada = (altura_filtrada * 0.6) + (medicion * 0.4)

            pwm_out = pid.calcular(altura_filtrada)
            in3_pwm.duty(pwm_out)
            await asyncio.sleep_ms(30)
        else:
            in3_pwm.duty(0)
            set_sensor_power(False)
            pid.salida = 0
            await asyncio.sleep(1)


# ============================================================
# 8. CONEXIÓN MQTT
# ============================================================
async def conectar_mqtt():
    wifi_pm(network.WLAN.PM_NONE)
    cliente = None
    try:
        cliente = MQTTClient(CLIENT_ID, MQTT_BROKER,
                             port=MQTT_PORT, keepalive=120)
        cliente.set_callback(al_recibir_mensaje)
        cliente.connect()
        cliente.subscribe(TOPIC_SUB)
        print("[MQTT] Conectado a", MQTT_BROKER)
        return cliente
    except OSError as e:
        print("[MQTT] Fallo conexión:", e)
        return None
    except Exception as e:
        print("[MQTT] Error conexión:", e)
        return None
    finally:
        wifi_pm(network.WLAN.PM_POWERSAVE)


# ============================================================
# 9. MQTT LOOP
# ============================================================
async def mqtt_loop():
    global client

    backoff = 2
    max_backoff = 30
    primer_intento = True

    wifi_down_since = None
    wifi_estaba_ok = False

    last_publish = time.ticks_ms()

    while True:
        # ---------- WiFi ----------
        if not _wlan.isconnected():
            if wifi_down_since is None:
                wifi_down_since = time.ticks_ms()
                print("[MQTT] WiFi caído")
                if client is not None:
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    client = None
                backoff = 2
                primer_intento = True
                wifi_estaba_ok = False

            # Reintento cada 5 s con limpieza de driver
            if time.ticks_diff(time.ticks_ms(), wifi_down_since) >= 5000:
                print("[MQTT] Reconectando WiFi...")
                wifi_reconectar()
                wifi_down_since = time.ticks_ms()

            await asyncio.sleep(1)
            continue
        else:
            if wifi_down_since is not None:
                print("[MQTT] WiFi reconectado")
            wifi_down_since = None

        # ---------- Esperar ruteo post-WiFi ----------
        if not wifi_estaba_ok:
            print("[MQTT] WiFi OK, esperando 2s...")
            await asyncio.sleep(2)
            wifi_estaba_ok = True

        # ---------- Sin cliente MQTT ----------
        if client is None:
            if not primer_intento:
                print("[MQTT] Reintento en {}s...".format(backoff))
                await asyncio.sleep(backoff)
            primer_intento = False

            cliente_nuevo = await conectar_mqtt()
            if cliente_nuevo is not None:
                client = cliente_nuevo
                backoff = 2
                last_publish = time.ticks_ms()
            else:
                backoff = min(backoff * 2, max_backoff)
                continue

        # ---------- Operación normal ----------
        try:
            client.check_msg()
            ahora = time.ticks_ms()

            if modo == "RUN":
                if time.ticks_diff(ahora, last_publish) >= 1000:
                    estado = pid.estado()
                    pwm_raw = estado["pwm"]
                    pwm_pct = round(pwm_raw / 1023.0 * 100.0, 1)
                    estado["pwm_raw"] = pwm_raw
                    estado["pwm"]     = pwm_pct
                    estado["rpm"]     = int(pwm_pct / 100.0 * 3500)
                    estado["modo"]    = modo

                    payload = json.dumps(estado)
                    client.publish(TOPIC_PUB, payload.encode())
                    print("[MQTT TX]", payload)
                    last_publish = ahora
            else:
                if time.ticks_diff(ahora, last_publish) >= HEARTBEAT_LP_S * 1000:
                    payload = json.dumps({"modo": "LOW_POWER"})
                    client.publish(TOPIC_PUB, payload.encode())
                    print("[MQTT TX]", payload)
                    last_publish = ahora

        except OSError as e:
            print("[MQTT] Desconectado:", e)
            try:
                client.disconnect()
            except Exception:
                pass
            client = None
        except Exception as e:
            print("[MQTT] Error inesperado:", e)

        await asyncio.sleep_ms(100 if modo == "RUN" else 500)


# ============================================================
# 10. MAIN
# ============================================================
async def main():
    # Frecuencia fija ANTES de tocar WiFi/MQTT
    try:
        machine.freq(CPU_FREQ_HZ)
        print("[CPU] {} MHz".format(CPU_FREQ_HZ // 1_000_000))
    except Exception as e:
        print("[CPU] No se pudo fijar frecuencia:", e)

    set_enb(False)
    set_sensor_power(False)
    in3_pwm.duty(0)

    if _wlan.isconnected():
        wifi_pm(network.WLAN.PM_POWERSAVE)

    print("[INIT] Arrancando en LOW_POWER")

    asyncio.create_task(control_loop())
    asyncio.create_task(mqtt_loop())

    while True:
        await asyncio.sleep(3600)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    in3_pwm.duty(0)
    set_enb(False)
    set_sensor_power(False)
    print("\n[MOTOR] Detenido")
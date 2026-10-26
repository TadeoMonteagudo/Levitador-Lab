# ============================================================
# pid.py - Controlador PID con anti-windup y saturación PWM
# ============================================================
import time


class PID:
    def __init__(self, kp=2.0, ki=0.5, kd=0.1, sp=20.0,
                 pwm_min=600, pwm_max=700, pwm_base=750):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.sp = float(sp)

        self.pwm_min = int(pwm_min)
        self.pwm_max = int(pwm_max)
        self.pwm_base = int(pwm_base)

        self.integral = 0.0
        self.error_previo = 0.0
        self.error = 0.0
        self.pv = 0.0
        self.salida = self.pwm_base
        self.ultimo_tiempo = None

        self._integral_max = 50.0

    def reset(self):
        self.integral = 0.0
        self.error_previo = 0.0
        self.error = 0.0
        self.pv = 0.0
        self.salida = self.pwm_base
        self.ultimo_tiempo = None

    def actualizar(self, kp=None, ki=None, kd=None, sp=None):
        if kp is not None:
            self.kp = float(kp)
        if ki is not None:
            self.ki = float(ki)
        if kd is not None:
            self.kd = float(kd)
        if sp is not None:
            self.sp = float(sp)

    def calcular(self, pv):
        ahora = time.ticks_ms()

        if self.ultimo_tiempo is None:
            self.ultimo_tiempo = ahora
            self.pv = pv
            self.error = self.sp - pv
            self.error_previo = self.error
            return self.salida

        dt = time.ticks_diff(ahora, self.ultimo_tiempo) / 1000.0
        if dt <= 0:
            return self.salida

        self.ultimo_tiempo = ahora
        self.pv = pv
        self.error = self.sp - pv

        P = self.kp * self.error

        self.integral += self.error * dt
        if self.integral > self._integral_max:
            self.integral = self._integral_max
        elif self.integral < -self._integral_max:
            self.integral = -self._integral_max
        I = self.ki * self.integral

        derivativa = (self.error - self.error_previo) / dt
        D = self.kd * derivativa
        self.error_previo = self.error

        salida = self.pwm_base + (P + I + D)
        self.salida = int(max(self.pwm_min, min(self.pwm_max, salida)))
        return self.salida

    def estado(self):
        return {
            "pv":  round(self.pv, 2),
            "sp":  round(self.sp, 2),
            "error": round(self.error, 2),
            "kp":  round(self.kp, 3),
            "ki":  round(self.ki, 3),
            "kd":  round(self.kd, 3),
            "pwm": self.salida,
            "integral": round(self.integral, 2),
        }
"""
Bot de aviso de canchas liberadas – OnDepor / CUBA sede Palermo (versión GitHub Actions)
---------------------------------------------------------------------------------------
Cada ejecución hace UN chequeo:
  1. Abre Chrome (sin ventana) con la sesión guardada de la ejecución anterior.
  2. Si la sesión se cerró, inicia sesión con ONDEPOR_EMAIL / ONDEPOR_PASSWORD.
  3. Recorre la grilla de Palermo (hoy, mañana y pasado) navegando como una persona.
  4. Compara con el chequeo anterior y manda un mail si se liberó algún turno.

IMPORTANTE: el repositorio es público, así que este script NO imprime nombres de
socios ni datos de la sesión en los registros. La sesión se guarda cifrada.

Secrets necesarios (Settings → Secrets and variables → Actions):
  ONDEPOR_EMAIL, ONDEPOR_PASSWORD, GMAIL_USER, GMAIL_APP_PASSWORD, MAIL_TO (opcional)
Variables opcionales (pestaña Variables): SEDE, HORAS_MIN, HORAS_MAX,
  HORA_INICIO_ACTIVO, HORA_FIN_ACTIVO, CANCHAS
"""

import base64
import hashlib
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from playwright.sync_api import sync_playwright

ESTADO_DIR = Path(os.environ.get("ESTADO_DIR", "estado"))
ESTADO_PATH = ESTADO_DIR / "estado.json"
SESION_PATH = ESTADO_DIR / "sesion.enc"

FAV_URL = "https://ondepor.com/user/_favorites"
LOGIN_URL = "https://ondepor.com/app/login"

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def env(nombre, defecto=None):
    v = os.environ.get(nombre, "").strip()
    return v if v else defecto


SEDE = env("SEDE", "Palermo")
HORAS_MIN = float(env("HORAS_MIN", "2"))
HORAS_MAX = float(env("HORAS_MAX", "48"))
HORA_INI = int(env("HORA_INICIO_ACTIVO", "7"))
HORA_FIN = int(env("HORA_FIN_ACTIVO", "23"))
CANCHAS = [c.strip().upper() for c in env("CANCHAS", "").split(",") if c.strip()]

DAY_JS = r"""() => {
  const re = /^(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo) \d+/;
  const e = [...document.querySelectorAll('body *')]
      .find(x => x.children.length === 0 && re.test((x.innerText || '').trim()));
  return e ? e.innerText.trim() : null;
}"""

PARSE_JS = r"""() => {
  const rows = [...document.querySelectorAll('table tr')];
  if (!rows.length) return null;
  const heads = [...rows[0].children].slice(1).map(c => c.innerText.replace(/\s+/g, ' ').trim());
  const occ = {}; const out = [];
  rows.slice(1).forEach((r, ri) => {
    let col = 0; const cells = [...r.children]; if (!cells.length) return;
    const hora = cells[0].innerText.trim();
    cells.slice(1).forEach(c => {
      while (occ[ri + ',' + col]) col++;
      const rs = parseInt(c.getAttribute('rowspan')) || 1;
      for (let k = 0; k < rs; k++) occ[(ri + k) + ',' + col] = 1;
      if (c.classList.contains('is-available')) out.push({hora, cancha: heads[col] || ('Col ' + col)});
      col++;
    });
  });
  return out;
}"""


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# --- Estado y sesión cifrada -------------------------------------------------

def fernet():
    semilla = (os.environ["ONDEPOR_PASSWORD"] + "|ondepor-bot|" + os.environ["ONDEPOR_EMAIL"]).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(semilla).digest()))


def cargar_estado():
    try:
        return json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def guardar_estado(estado):
    ESTADO_DIR.mkdir(parents=True, exist_ok=True)
    ESTADO_PATH.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def cargar_sesion():
    try:
        return json.loads(fernet().decrypt(SESION_PATH.read_bytes()))
    except (OSError, InvalidToken, ValueError):
        return None


def guardar_sesion(ctx):
    ESTADO_DIR.mkdir(parents=True, exist_ok=True)
    SESION_PATH.write_bytes(fernet().encrypt(json.dumps(ctx.storage_state()).encode()))


# --- Mail --------------------------------------------------------------------

def enviar_mail(asunto, html):
    usuario = os.environ["GMAIL_USER"].strip()
    destino = env("MAIL_TO", usuario)
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = asunto, usuario, destino
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(usuario, os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
        s.sendmail(usuario, [destino], msg.as_string())
    log(f"Mail enviado: {asunto}")


def html_turnos(titulo, turnos, nota):
    partes = [f"<p><b>{titulo}</b></p>"]
    por_dia = {}
    for t in sorted(turnos, key=lambda x: (x["fecha"], x["hora"], x["cancha"])):
        por_dia.setdefault(t["fecha"], []).append(t)
    for fecha, lista in por_dia.items():
        d = datetime.fromisoformat(fecha)
        partes.append(f"<p><b>{DIAS[d.weekday()]} {d:%d/%m}</b></p><ul>")
        partes += [f'<li>{t["hora"]} – {t["cancha"].title()}</li>' for t in lista]
        partes.append("</ul>")
    if not turnos:
        partes.append("<p>No hay turnos libres en la ventana.</p>")
    partes.append(f'<p><a href="{FAV_URL}">Reservar en OnDepor</a></p>')
    partes.append(f'<p style="color:#666;font-size:12px">{nota}</p>')
    return "\n".join(partes)


# --- Navegación --------------------------------------------------------------

class LoginFallido(Exception):
    pass


def iniciar_sesion(page):
    log("Iniciando sesión en OnDepor…")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_selector("input[type=password]", timeout=20000)
    page.locator("input[type=email]").first.fill(os.environ["ONDEPOR_EMAIL"])
    page.locator("input[type=password]").first.fill(os.environ["ONDEPOR_PASSWORD"])
    recordar = page.locator("input[type=checkbox]").first
    if recordar.count() and not recordar.is_checked():
        try:
            recordar.check(timeout=3000)
        except Exception:
            pass
    page.locator("input[type=password]").first.press("Enter")
    limite = time.time() + 30
    while time.time() < limite:
        page.wait_for_timeout(1000)
        if "/app/login" not in page.url and "ondepor.com" in page.url:
            break
    page.goto(FAV_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if "_favorites" not in page.url:
        raise LoginFallido(page.url.split("?")[0])
    log("Sesión iniciada.")


def parsear_fecha(label):
    m = re.search(r"(\d{1,2}) de (\w+)\.? (\d{4})", label or "")
    if not m or m.group(2)[:3].lower() not in MESES:
        return None
    return datetime(int(m.group(3)), MESES[m.group(2)[:3].lower()], int(m.group(1))).date()


def leer_grilla(page, dias):
    page.goto(FAV_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if "_favorites" not in page.url:
        iniciar_sesion(page)

    actividad = page.locator("h1,h2,h3,h4,h5,h6", has_text=SEDE).first
    if actividad.count() == 0:
        page.get_by_text(re.compile(r"(Ver|Mostrar) actividades", re.I)).first.click()
        page.wait_for_timeout(1000)
    actividad.click()
    page.wait_for_selector("table td.calendar-slot", timeout=25000)
    page.wait_for_timeout(1500)

    turnos, label = [], page.evaluate(DAY_JS)
    for i in range(dias):
        if i > 0:
            prev = label
            page.get_by_role("link", name="Siguiente").first.click()
            limite = time.time() + 20
            while time.time() < limite:
                page.wait_for_timeout(500)
                label = page.evaluate(DAY_JS)
                if label and label != prev:
                    break
            else:
                raise RuntimeError("El calendario no avanzó de día")
            page.wait_for_selector("table td.calendar-slot", timeout=20000)
            page.wait_for_timeout(1500)
        fecha = parsear_fecha(label)
        if not fecha:
            raise RuntimeError("No pude leer la fecha del calendario")
        for s in page.evaluate(PARSE_JS) or []:
            turnos.append({"fecha": fecha.isoformat(), "hora": s["hora"], "cancha": s["cancha"]})
    return turnos


def filtrar(turnos, ahora):
    desde, hasta = ahora + timedelta(hours=HORAS_MIN), ahora + timedelta(hours=HORAS_MAX)
    out = []
    for t in turnos:
        try:
            hh, mm = map(int, t["hora"].split(":"))
        except ValueError:
            continue
        inicio = datetime.fromisoformat(t["fecha"]).replace(hour=hh, minute=mm)
        if desde <= inicio <= hasta and (not CANCHAS or any(c in t["cancha"].upper() for c in CANCHAS)):
            out.append(t)
    return out


# --- Principal ---------------------------------------------------------------

def main():
    ahora = datetime.now()
    faltan = [n for n in ("ONDEPOR_EMAIL", "ONDEPOR_PASSWORD", "GMAIL_USER", "GMAIL_APP_PASSWORD")
              if not os.environ.get(n, "").strip()]
    if faltan:
        log("Faltan Secrets: " + ", ".join(faltan) + ". Cargalos en Settings → Secrets and variables → Actions.")
        return 0
    forzar = os.environ.get("FORZAR_MAIL", "").lower() in ("1", "true", "yes", "si", "sí")
    if not forzar and not (HORA_INI <= ahora.hour < HORA_FIN):
        log(f"Fuera del horario activo ({HORA_INI}–{HORA_FIN} h). Nada que hacer.")
        return 0

    estado = cargar_estado()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=env("CANAL", "chrome"), headless=True)
        ctx = browser.new_context(storage_state=cargar_sesion(), locale="es-AR",
                                  timezone_id="America/Argentina/Buenos_Aires",
                                  viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        try:
            turnos = filtrar(leer_grilla(page, int(HORAS_MAX // 24) + 2), ahora)
            guardar_sesion(ctx)
        except LoginFallido:
            log("No se pudo iniciar sesión (¿contraseña incorrecta o captcha?).")
            if not estado.get("aviso_login"):
                enviar_mail("[Bot OnDepor] No pude iniciar sesión en OnDepor",
                            "<p>El bot intentó loguearse y OnDepor no lo dejó entrar.</p>"
                            "<p>Revisá ONDEPOR_EMAIL / ONDEPOR_PASSWORD en los Secrets del repositorio. "
                            "Si están bien, puede que OnDepor bloquee logins automáticos desde servidores.</p>")
                estado["aviso_login"] = True
                guardar_estado(estado)
            return 1
        finally:
            browser.close()

    actuales = {f'{t["fecha"]} {t["hora"]}|{t["cancha"]}': t for t in turnos}
    nota = (f"Ventana: turnos que empiezan entre {HORAS_MIN:g} h y {HORAS_MAX:g} h desde el chequeo "
            f"({ahora:%d/%m %H:%M}). Bot en GitHub Actions.")
    primera = "libres" not in estado

    if primera or forzar:
        titulo = "Bot iniciado – turnos libres ahora" if primera else "Prueba – turnos libres ahora"
        enviar_mail(f"[Bot OnDepor] {titulo} ({len(turnos)})",
                    html_turnos(f"CUBA {SEDE} – {titulo}", turnos, nota + " Desde ahora solo aviso turnos nuevos."))
        nuevos = []
    else:
        previos = set(estado.get("libres", []))
        nuevos = [t for k, t in actuales.items() if k not in previos]
        if nuevos:
            resumen = ", ".join(f'{DIAS[datetime.fromisoformat(t["fecha"]).weekday()][:3]} {t["hora"]}'
                                for t in sorted(nuevos, key=lambda x: (x["fecha"], x["hora"])))
            enviar_mail(f"[Bot OnDepor] Se liberó cancha en {SEDE}: {resumen}",
                        html_turnos(f"Turnos que se liberaron en CUBA {SEDE}", nuevos, nota))

    guardar_estado({"libres": list(actuales), "ultimo_chequeo": ahora.isoformat(timespec="seconds")})
    log(f"Chequeo OK: {len(turnos)} libres en la ventana, {len(nuevos)} nuevos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

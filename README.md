# Bot OnDepor – aviso de canchas liberadas (CUBA Palermo)

Corre gratis en GitHub Actions cada 5 minutos entre las 7 y las 23 h (hora de Argentina).
Abre un Chrome sin ventana, entra a OnDepor como lo haría una persona, mira la grilla de
**CUBA - Palermo | PADEL** y manda un mail cuando se libera un turno que empieza entre
2 h y 48 h desde el chequeo.

- La sesión de OnDepor se guarda **cifrada** entre ejecuciones; si se cierra, el bot vuelve a iniciar sesión solo.
- Los registros públicos solo muestran cantidades y horarios libres: nunca nombres de socios ni datos de tu cuenta.

## 1. Cargar los Secrets (una sola vez)

En este repositorio: **Settings → Secrets and variables → Actions → New repository secret**.

| Nombre | Qué va |
|---|---|
| `ONDEPOR_EMAIL` | El mail con el que entrás a OnDepor |
| `ONDEPOR_PASSWORD` | Tu contraseña de OnDepor |
| `GMAIL_USER` | La cuenta de Gmail que manda el aviso |
| `GMAIL_APP_PASSWORD` | Contraseña de aplicación de 16 letras (https://myaccount.google.com/apppasswords) |
| `MAIL_TO` | *(opcional)* A quién le llega el aviso. Si no está, le llega a `GMAIL_USER` |

Los Secrets están cifrados: nadie los puede ver, ni siquiera vos después de guardarlos.

## 2. Probar

**Actions → Chequeo canchas → Run workflow**, tildá *Mandar mail con todo lo libre* y dale **Run workflow**.
En 1–2 minutos te llega "[Bot OnDepor] Prueba – turnos libres ahora".

Desde ese momento corre solo cada 5 minutos.

## 3. Ajustes opcionales

En **Settings → Secrets and variables → Actions → pestaña Variables** podés crear:

| Variable | Por defecto | Ejemplo |
|---|---|---|
| `HORAS_MIN` | 2 | 3 |
| `HORAS_MAX` | 48 | 24 |
| `HORA_INICIO_ACTIVO` / `HORA_FIN_ACTIVO` | 7 / 23 | 8 / 22 |
| `CANCHAS` | todas | `CANCHA 01, CANCHA 02` |
| `SEDE` | Palermo | Núñez |

Si cambiás el horario activo, ajustá también la línea `cron` de `.github/workflows/chequeo.yml` (está en UTC = Argentina + 3 h).

## 4. Más puntual con cron-job.org (opcional)

GitHub no garantiza los 5 minutos: a veces demora 10–20. Para disparos exactos cada 3 minutos:

1. Creá un token en https://github.com/settings/personal-access-tokens/new → *Only select repositories* → este repo → permiso **Actions: Read and write**.
2. En https://cron-job.org creá un cron job:
   - URL: `https://api.github.com/repos/<tu-usuario>/ondepor-bot/actions/workflows/chequeo.yml/dispatches`
   - Método **POST**, cada 3 minutos, de 7 a 23 h (zona horaria Buenos Aires)
   - Headers: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`
   - Body: `{"ref":"main"}`

## Si algo falla

- **"No pude iniciar sesión en OnDepor"** (te llega por mail una sola vez): revisá `ONDEPOR_EMAIL` / `ONDEPOR_PASSWORD`. Si están bien, OnDepor puede estar bloqueando logins desde servidores; en ese caso conviene usar la versión local.
- **Ejecuciones en rojo** en la pestaña Actions: GitHub te avisa por mail. Abrí la ejecución para ver el error.
- Para pausarlo: **Actions → Chequeo canchas → ··· → Disable workflow**.

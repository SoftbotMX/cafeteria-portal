# Portal de padres de familia — cafetería escolar (Softbot)

> **Única fuente de instrucciones del proyecto, compartida por todos los agentes.** `CLAUDE.md` y
> `GEMINI.md` son **symlinks** a este archivo. **Edita siempre `AGENTS.md`.** Comprobación:
> `test -L CLAUDE.md && test -L GEMINI.md || { ln -sf AGENTS.md CLAUDE.md; ln -sf AGENTS.md GEMINI.md; }`

## Carga de contexto (OBLIGATORIO al iniciar sesión)

> 🔴 Las rutas de memoria son literales y las MISMAS para todos los agentes (viven bajo `.claude/`
> por herencia). No las adaptes a tu agente.

Memoria: `/Users/gvaldez/.claude/projects/-Users-gvaldez-Desktop-Softbot-Proyectos-cafeteria-portal/memory/`
(enlace a iCloud). **No leas el índice global.** Carga sólo:
1. `project_cafeteria_portal.md` — estado, arquitectura, gotchas.
2. La sesión `session_*_portal*.md` **más reciente por fecha en el nombre** (sección «RETOMAR»).

Contexto del TPV (sólo si hace falta): memoria del TPV en
`…-Proyectos-tpv/memory/` (sesión `session_2026-10-20_tpv_cafeteria_escolar.md`).

## Resumen de sesión (OBLIGATORIO al terminar)
Crea/actualiza `session_AAAA-MM-DD_portal*.md` en la carpeta de memoria de arriba y enlázalo en su
`MEMORY.md` y en `project_cafeteria_portal.md`. Si la sesión sigue después de escribirlo, actualízalo
antes de cerrar.

## Codex como segunda voz (OBLIGATORIO al hacer cambios)
Ningún cambio de código se da por bueno con una sola opinión: antes de commitear pasa el diff
**completo, sin truncar** por Codex (`/codex review`; `/codex challenge` en pagos, autenticación y
todo lo que mueva dinero) y reporta qué dijo y qué rechazaste. Verifica reproduciendo antes de
creer. Si Codex falla, dilo y sigue. El hook `scripts/hooks/pre-commit` lo recuerda
(`git config core.hooksPath scripts/hooks`; registrar con `scripts/hooks/codex-visto`).

## Qué es
Portal web para los **padres de familia** (en el código, `tutor`) de una cafetería escolar que usa **Softbot TPV**
(repo `SoftbotMX/tpv`, módulo `softbot_tpv_cafeteria`): ven el saldo de sus hijos, piden con
fecha, recargan con MercadoPago, y la **administración** de la cafetería da de alta padres de familia e hijos.

- Diseño: `docs/superpowers/specs/2026-10-20-portal-tutores-design.md` (local, no se sube).
- Contrato con el TPV: `docs/API_TPV.md`. **El portal no guarda saldos**: todo se lee del TPV.
- Avisos/bot de WhatsApp: Conduit (`../conduit`, `docs/INTEGRACION_CAFETERIA_ESCOLAR.md`).

## Reglas que no se negocian
- El `tutor_ref` sale **siempre de la sesión**, nunca del formulario o de la URL.
- Un webhook de MercadoPago **no se cree**: se consulta el pago en su API antes de acreditar.
  `409 pago_ya_acreditado` del TPV **no es éxito**.
- Toda escritura al TPV lleva su `llave`; los secretos (TPV, MercadoPago, Conduit) van cifrados en
  BD o en `.env`, nunca en el repo, el log ni el chat.
- Despliegue: la API del TPV va **antes** que el portal.

## Stack y comandos
Python 3.12 + FastAPI, Jinja2 + HTMX, PostgreSQL 16, SQLAlchemy 2 + Alembic, pytest. Docker en el
VPS (`softbot.com.mx`), subdominio `cafeteria.softbot.com.mx`. Ver `README.md` para correr local.

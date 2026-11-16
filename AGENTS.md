# Instrucciones para agentes — JG Costos IA

**Lea primero `PROJECT_HANDOFF.md`.** Es el punto de entrada obligatorio: arquitectura real, estado de módulos, defectos conocidos, decisiones pendientes y reglas.

Resumen de reglas (el detalle está en PROJECT_HANDOFF.md §14):

- Esta carpeta no tiene Git: haga una copia en `../snapshots/` antes de cambios grandes.
- Persistencia actual: SQLite. No cambiar a Google Sheets ni a otro motor sin la decisión D1 del propietario.
- Nunca usar `data/personal.db` para pruebas; usar `JG_DATABASE` temporal.
- No escribir en Google Sheets/Drive reales ni desplegar en `mi-servidor` sin autorización explícita.
- No leer ni copiar credenciales.
- Pruebas: `../venv/bin/python -m unittest discover -s tests`, `node tests/*.cjs`, `../venv/bin/python tests/e2e_smoke_playwright.py`.
- Actualice `PROJECT_HANDOFF.md` (estado, defectos, registro de cambios) al terminar cada avance.

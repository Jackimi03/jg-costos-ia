# JG Costos IA

ERP para **contratistas de construcción** en Panamá: cotizaciones con análisis de precio unitario (APU) asistido por IA, obras con cronograma y control de costos, órdenes de cambio, clientes, contratos, informes y PDF de cotización. Interfaz en español, pensado para una empresa pequeña.

> Proyecto de código abierto bajo **AGPL-3.0**. Si lo modificas y lo ofreces como servicio a otras personas, debes publicar también tus cambios.

> **Se despliega en TU propia infraestructura.** Este repositorio es solo el código: no incluye servidores, cuentas ni datos, y **no se conecta a la infraestructura del autor**. Cada organización lo instala en su equipo o servidor y conecta sus propias cuentas (OpenAI, Gmail, Tailscale). No existe un servicio alojado ni una instancia compartida.

## Qué incluye

| Módulo | Qué hace |
|---|---|
| **Inicio** | Indicadores, presupuesto vs costo real por obra, alertas (sobrecosto, incidencias, actividades vencidas, órdenes pendientes), vencimientos a 30 días, embudo de cotizaciones |
| **Cotizaciones + IA** | Partidas con APU por unidad (materiales, mano de obra, equipos, desperdicio) que se recalcula al instante; APU automático con la API de OpenAI; gastos generales y utilidad incluidos en el precio de venta; condiciones de pago por zona (visita técnica, anticipo, saldo); PDF profesional inmutable al emitir |
| **Obras** | Ficha integral, cronograma tipo Gantt con línea “Hoy”, actividades, jornadas, materiales, gastos, ingresos, bitácora, evidencias, incidencias, distribución de costos |
| **Órdenes de cambio** | Adicionales y deductivos con impacto en costo y plazo, aprobación por administrador, presupuesto y entrega revisados |
| **Clientes, contratos, catálogo, equipo, informes** | Registros con historial de versiones, adjuntos, permisos por rol y obra, auditoría |
| **Mensajes** | WhatsApp Web en una ventana acoplada al ERP (WhatsApp no permite incrustarse: `X-Frame-Options: DENY`) |
| **Mapas** | Direcciones, coordenadas o enlaces de Google Maps con mapa incrustado y detección de zona |
| **Acceso** | Local sin contraseña; desde otros equipos, solo con **correo + código de 6 dígitos** de un solo uso enviado al administrador |
| **Instaladores** | Generador de instaladores para Linux, Windows y Mac que conectan cada equipo a **su** servidor por Tailscale (red privada, sin dominio público) |

**Tecnología:** Python 3.14 · FastAPI · SQLite · ReportLab · HTML/CSS/JavaScript sin framework · Docker Compose · pruebas con `unittest`, Node y Playwright.

## Despliegue en su propia infraestructura

Necesita un equipo o servidor **propio** con Linux (o Docker) e internet para instalar dependencias. Todo queda en su máquina: base de datos SQLite, archivos, PDF y secretos.

### Opción A · Equipo local (probar o una sola persona)

```bash
git clone https://github.com/Jackimi03/jg-costos-ia.git && cd jg-costos-ia
sh instalar-local.sh          # crea .venv e instala dependencias
sh start-local.sh             # inicia en http://127.0.0.1:8765 (datos en data/)
```

### Opción B · Su servidor con Docker

```bash
git clone https://github.com/Jackimi03/jg-costos-ia.git && cd jg-costos-ia
docker compose -f compose.personal.yaml up -d --build
```

El contenedor escucha **solo en `127.0.0.1:8765` de su servidor**: no queda expuesto a internet. Para entrar desde su computadora use un túnel SSH (`sh abrir-panel-servidor.sh <alias-ssh-de-su-servidor>`) y abra `http://127.0.0.1:8766`.

### Opción C · Acceso de su equipo de trabajo por red privada (Tailscale)

1. Instale Tailscale en **su** servidor y únalo a **su** red (tailnet).
2. Junto a `compose.personal.yaml` cree un archivo `.env` con la IP de Tailscale de **su** servidor:
   ```
   JG_REMOTE_BIND=100.x.y.z:8765
   JG_ALLOWED_HOSTS=100.x.y.z
   ```
3. `docker compose -f compose.personal.yaml up -d --build`. Desde la red privada solo se entra con **correo + código**; el panel antiguo y la sesión automática quedan bloqueados.

### Primer arranque: conecte sus cuentas en **Configuración**

| Sección | Qué poner | Para qué |
|---|---|---|
| Datos de la empresa | Nombre, RUC, DV, dirección, teléfono, correo, condiciones | Encabezado del PDF y barra lateral |
| Costos y utilidad | Gastos fijos mensuales, utilidad por tramos | Gastos generales y utilidad automáticos |
| Inteligencia artificial | Su clave API de OpenAI y modelo | APU automático (opcional) |
| Correo y acceso | Su correo de administrador y una cuenta Gmail con contraseña de aplicación | Enviar los códigos de acceso |
| Instalador para otros equipos | Una auth key de **su** Tailscale y la IP de **su** servidor | Generar los instaladores |
| Catálogo de precios | “Importar precios de referencia” o crear sus servicios | Precios de sus partidas |

Las claves se guardan solo en su servidor (`<carpeta de la base>/secrets/`, permisos 0600) y nunca se envían al navegador.

### Instaladores para Linux, Windows y Mac

Los instaladores **no vienen como archivos en este repositorio, a propósito**: cada uno lleva dentro la clave de la red privada de quien lo genera. Se crean en **su** instalación desde **Configuración → Instalador para otros equipos → Descargar para Linux / Windows / Mac** (código en `jg/erp_access.py`). Cada instalador:

- instala Tailscale desde los paquetes oficiales y une el equipo a **su** red sin pedir cuentas;
- comprueba que llega a **su** servidor y crea el icono “JG Costos IA”;
- deja al usuario en la pantalla de acceso: escribe su correo y entra con el código de 6 dígitos que recibe el administrador.

Formatos: Linux `.sh` (`bash archivo.sh`), Windows `.cmd` (doble clic; pide permisos de administrador), Mac `.command` (doble clic; si macOS lo bloquea, clic derecho → Abrir, y permita la VPN de Tailscale). Los de Windows y Mac aún no se han probado en equipos reales: reporte cualquier problema. Comparta los instaladores solo con personas autorizadas.

### Personalización

- **Precios:** `fixtures/JG_Precios_Referenciales.xlsm` es un **archivo de ejemplo con precios ficticios**. Reemplácelo por el suyo (mismo formato) y actualice `sha256` en `fixtures/source.json` (`sha256sum fixtures/JG_Precios_Referenciales.xlsm`).
- **Condiciones comerciales** (zonas y costo de visita, anticipo 25/30 %, validez 15 días): constantes `ZONES`, `ADVANCE_*` y `QUOTE_VALID_DAYS` en `jg/erp.py`; están pensadas para Panamá.
- **Marca:** colores en `jg/erp.css`; el nombre de la empresa se toma de Configuración.

## Pruebas

```bash
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests
node tests/test_sheet.cjs && node tests/test_web.cjs
.venv/bin/python tests/e2e_smoke_playwright.py
```

Las pruebas usan bases de datos temporales; nunca tocan `data/`.

## Seguridad

- No publique el ERP directamente en internet: está pensado para `127.0.0.1` + túnel SSH o una red privada (Tailscale).
- `data/`, `secrets/` y `.env` están excluidos de Git.
- Reporte vulnerabilidades de forma privada al propietario del repositorio antes de publicarlas.

## Documentación para desarrolladores

- [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md): arquitectura, estado de módulos, decisiones y registro de cambios. Describe también el despliegue original del autor (“mi-servidor”, rutas `~/apps/…`) como **referencia histórica**: no son servicios a los que este código se conecte.
- [`AGENTS.md`](AGENTS.md): reglas para agentes de IA que trabajen en el código.

## Licencia

[GNU Affero General Public License v3.0](LICENSE) © JG Soluciones PA.

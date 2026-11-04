# venture-scout-agent

Agentes que cada 2 o 3 días revisan el ecosistema de emprendimiento de Brisbane y Australia, guardan el historial,
miden tendencias y sentimiento, y proponen ideas de negocio de IA y agentes que se puedan empezar desde cero, a tiempo
parcial y con muy poco capital. También señalan personas, comunidades y eventos para ampliar la red de contactos.

## Cómo funciona

| Etapa | Agente | Modelo | Qué hace |
|---|---|---|---|
| 1 | Collectors (uno por grupo de `sources.yaml`) | Haiku + búsqueda web | Noticias, ecosistema de Brisbane, programas y subvenciones, universidades, foros y grupos públicos, LinkedIn y blogs, eventos, problemas de pymes. Devuelve elementos con resumen, etiquetas, temas y sentimiento (de -1 a 1) |
| 2 | Inbox manual | Haiku | Publicaciones que pegues en `inbox/` desde grupos privados |
| 3 | Store y tendencias | Código (sin IA) | SQLite en `data/venture.db`: deduplica por URL, cuenta etiquetas y frases, compara la ventana actual (14 días) con la anterior y calcula el sentimiento promedio por tema |
| 4 | Idea analyst | Sonnet | Re-evalúa las ideas del historial y agrega nuevas, con puntajes de señal de mercado, fit contigo, tiempo parcial y sentimiento, capital inicial, horas y meses a primer ingreso, riesgo de conflicto con QFD y una prueba de validación |
| 5 | Scoring y filtros | Código | Puntaje compuesto con los pesos de `config.yaml` y filtros duros (capital ≤ AUD 5,000, ≤ 15 h/semana, ≤ 6 meses a ingreso, fit ≥ 60) |
| 6 | People matcher | Sonnet | Personas con afinidad, cómo acercarte sin ser invasivo, comunidades y eventos próximos |
| 7 | Strategist (domingos y día 1) | Opus | Dos o tres apuestas, plan de 30 días y criterios para descartar |
| 8 | Análisis histórico | Código | Sobre todo el historial: ocurrencias acumuladas, términos persistentes (presentes semana tras semana), términos que ganan o pierden participación, series de 8 semanas, sentimiento por tema en el tiempo y personas recurrentes. Se entrega también al analista y al estratega |
| 9 | Reporte | Código | Correo HTML en español y `reports/AAAA-MM-DD.md` (más `reports/latest.md`) en el repositorio |

Las URLs de evidencia que el modelo cite y que no vengan de los elementos recolectados se descartan, y las personas
que no aparezcan en lo recolectado también.

## Programación

GitHub Actions (`.github/workflows/venture-scout.yml`) a las 05:47 de Brisbane los martes, jueves y domingos. El domingo
incluye la revisión estratégica semanal. También puedes lanzarlo desde **Actions > venture-scout > Run workflow**
(opciones: dry run, revisión semanal, solo ciertos grupos).

## Configuración

Secretos del repositorio (**Settings > Secrets and variables > Actions**), los mismos de job-scout-agent:

| Secreto | Uso |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API (incluye la herramienta de búsqueda web, que debe estar habilitada en la organización de la Console) |
| `GMAIL_USER` | Cuenta que envía el correo |
| `GMAIL_APP_PASSWORD` | App password de Gmail |
| `EMAIL_TO` (opcional) | Destinatario del reporte; si no existe, el reporte llega a la misma cuenta `GMAIL_USER` |

Para probar solo el correo: **Run workflow** con **test_email** marcado. Si una ejecución programada falla, llega un
aviso al mismo destinatario con el enlace al registro.

Archivos que puedes editar sin tocar código:

- `sources.yaml`: grupos, búsquedas, dominios preferidos y `watchlist` de autoridades a seguir.
- `profile/founder.yaml`: tu perfil, restricciones y dominios a evaluar en cada ejecución.
- `config.yaml`: modelos, filtros, pesos del puntaje, presupuesto de búsquedas.

## Costo esperado

Con 8 grupos y hasta 6 búsquedas por grupo: unas 48 búsquedas web (USD 10 por cada 1,000) más tokens de Haiku y Sonnet,
del orden de AUD 1 a 2 por ejecución y algo más los domingos por Opus. El reporte muestra el costo real estimado.
Ajusta `max_searches_per_group` para bajarlo.

## Límites

- Facebook y LinkedIn: solo se alcanzan páginas públicas indexadas por el buscador. Los grupos privados y el feed
  requieren sesión iniciada y extraerlos va contra sus términos de uso; para eso está `inbox/`.
- El sentimiento lo asigna el modelo por elemento; las frecuencias y tendencias se calculan en código y son
  reproducibles.
- Empleo en QFD: el empleo secundario y los conflictos de interés deben declararse y aprobarse según la política de
  QFD y del sector público de Queensland. El analista penaliza las ideas con riesgo de conflicto y nunca propone
  vender a QFD ni usar sus datos, sistemas o contactos.

## Uso local

```bash
pip install -r requirements.txt
python -m pytest -q
python run.py --mock                     # sin red, Claude simulado, salida en out/
python run.py --dry-run --groups news    # real, pero el correo queda en out/ en lugar de enviarse
python -m agents.collector events        # ver la salida cruda de un grupo
python tests/simulate_production.py      # ensayo de producción: 8 semanas de ejecuciones programadas
```

El ensayo de producción ejecuta `run.py` por la ruta real (sin `--mock` ni `--dry-run`) sobre una copia del
repositorio. Solo reemplaza la API de Claude (respuestas con el formato real del SDK, pausas de búsqueda y una falla
inyectada) y el SMTP de Gmail, y valida modelos, herramienta de búsqueda, `tool_choice` y esquemas JSON.

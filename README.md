# Agentes con workflow persistente y aprobación

Autor: [Focuz](https://www.focuz.io/). Caso de las sesiones 12 a 15 y extensiones senior.
Python 3.11 o posterior. Usa solo la biblioteca estándar y SQLite.
Versión: 1.1.0. Repositorio: https://github.com/focuz-ai/focuz-ai-engineering-agents

```powershell
python -m unittest discover -s tests -v
python -m agents
python -m agents.github_tools
python -m agents.issue_tests
python -m agents.estimation
```

La demo crea una propuesta, la aprueba otro usuario y ejecuta dos veces la misma
solicitud. El contador final de tickets locales es 1. No envía mensajes, no crea
tickets externos, no ejecuta shell y no llama modelos reales.

## Diseño

PENDING → APPROVED → DONE. Un digest vincula la aprobación al payload. Roles y
tenant se comprueban en cada operación. Un ID no puede cambiar de tenant, autor
o contenido. La transacción que crea el ticket también persiste el estado.
La propuesta inicia `BEGIN IMMEDIATE` antes de consultar si existe el ID. Dos
conexiones que proponen el mismo ID, tenant, autor y payload reciben la misma
propuesta; un cambio de ámbito, autor o payload se rechaza sin modificarla.
La lectura, validación e inserción se confirman o revierten juntas. No se espera
una llamada al modelo ni una aprobación humana dentro de esa transacción.
La aprobación bloquea la escritura antes de leer el estado, de modo que una
aprobación concurrente no puede devolver una solicitud DONE a APPROVED.
Los parámetros SQL se enlazan, nunca se interpolan. Las pruebas verifican la
reanudación después de cerrar y abrir una base persistente.

Este caso enseña la capa de control que un agente necesita. El planificador
determinista permite evaluar invariantes sin confundirlas con calidad del LLM.
Para añadir function calling conserva `propose` como frontera de validación y
mantén `approve` fuera del alcance de herramientas del modelo. No expongas roles
construidos por el cliente en un servicio: aquí se crean explícitamente por ser
una demostración de escritorio con identidades ficticias.

## Corrección 1.0.1

La corrección de SEC-01 elimina la carrera entre el SELECT y el INSERT de
`propose`. Las regresiones coordinan dos conexiones con eventos, sin sleeps:
comprueban reintentos idénticos y rechazan colisiones de tenant, autor o payload.
También verifican que quede una sola solicitud y ninguna transacción abierta.
La espera de bloqueo de SQLite sigue siendo finita; si otro proceso retiene
el bloqueo hasta agotar el timeout de conexión, se devuelve el error de SQLite.

## Laboratorio

1. Intenta ejecutar sin aprobación y comprueba cero efectos.
2. Prueba otra empresa y otro digest; ambas operaciones deben fallar.
3. Cierra el proceso después de aprobar y reanuda con la base en disco.
4. Implementa rechazo y caducidad de aprobación sin permitir revivir DONE.
5. Integra un grafo LangGraph conservando estos invariantes.
6. Añade evaluación de selección de herramienta y límites de pasos/tokens.
7. Para una API externa, diseña outbox e idempotency keys antes de reintentar.

## Límites de la garantía

La idempotencia se demuestra dentro de una sola base SQLite y transacción.
No se garantiza exactamente una ejecución de efectos distribuidos. Una API puede
completar su efecto y perderse la respuesta; se necesita reconciliación o un
contrato de idempotencia en el receptor. No hay autenticación, RBAC de red, UI de
aprobación, sandbox de procesos, TTL ni cola distribuida implementados.
El modelo no debe poder escribir directamente en SQLite ni autoconcederse roles.

## Demos del workshop (1.1.0)

### Tool calling de GitHub con aprobación

`GitHubToolDemo.propose(request_id, principal, tool_name, arguments)` valida
`create_issue` con los campos exactos `repository/title/body`. No admite roles,
tenant o funciones de aprobación en los argumentos del modelo. Requiere un
repositorio de la allowlist del servidor y límites de texto. El JSON canónico
completo queda ligado al digest del `Workflow` existente.

`Workflow.approve` requiere otro usuario. `GitHubToolDemo.execute` comprueba de
nuevo tenant, rol executor y repositorio permitido; una política revocada invalida
también el replay. Dos ejecuciones aprobadas dejan un solo ticket SQLite.
La salida declara `mode=local_simulation` y `remote_issue_created=false`.
Esta demo no llama GitHub ni Pydantic AI. Para un adaptador remoto se necesitan
permisos mínimos, revisión visible y reconciliación del efecto, además del digest.

### De issue a casos de prueba y Markdown

El vídeo de la demo GitHub recorre lectura de issues, propuesta de al menos cinco
casos, exportación a Markdown y revisión/ejecución en el IDE. `agents.issue_tests`
reproduce ese recorrido con una fixture y plantilla determinista, sin LLM:

```powershell
python -m agents.issue_tests --export casos-issue-17.md
```

`read_issue` filtra tenant, rol `qa` y repositorio autorizado. `propose_cases`
solo admite el requisito didáctico `approved_ticket`; otro caso requiere análisis
humano y se rechaza en lugar de inventar pruebas. Produce cinco escenarios con
`issue_id/test_id/preconditions/input/expected`: ruta feliz, falta de aprobación,
tenant ajeno, replay y rol revocado. `render_markdown` conserva trazabilidad,
escapa texto no confiable y etiqueta los casos como propuestos, no ejecutados.
El archivo se crea de forma exclusiva para evitar sobrescribir evidencia anterior.

Revisa los casos, localiza sus equivalentes en `tests/test_agents.py` y
`tests/test_workshop.py`, y ejecuta la suite. Adjunta salida y commit al informe
de QA: un Markdown generado no prueba cumplimiento. La extensión no automatiza
GitHub, no abre el IDE y no interpreta requisitos generales mediante IA.

### Estimador con dos gates humanos

`EstimationFlow` persiste el recorrido
`BREAKDOWN_PENDING → ESTIMATION_PENDING → FINAL_PENDING → DONE`:

1. `propose(id, principal, tasks, hourly_rate, contingency_percent)` valida de
   1 a 30 tareas con `id/description`, tarifa y contingencia, y fija `plan_digest`.
2. `approve_breakdown(id, reviewer, expected_digest)` es el gate 1. El autor no
   puede aprobar su propuesta. El flujo no acepta ni persiste estimaciones antes
   de esta aprobación; un servicio externo sí puede haberlas calculado previamente.
3. `submit_estimates(id, estimator, expected_plan_digest, estimates)` acepta las
   mismas tareas con `id/hours/evidence`. Horas automáticas requieren citas;
   `None` sin citas indica abstención. Produce un nuevo `estimate_digest`.
4. `approve_final(id, reviewer, expected_digest, hours_overrides)` es el gate 2.
   El revisor es distinto del autor y estimador; puede ser quien aprobó gate 1.
   Las tareas sin precedente exigen horas revisadas explícitamente. El precio
   se calcula con Decimal; no lo decide un LLM.

El contrato numérico admite hasta 64 posiciones decimales por entrada (incluidos
ceros finales): horas por tarea >0 y <=100000, tarifa >0 y <=1000000 y contingencia
entre 0 y 100. Mayor escala produce `ValueError`, no truncamiento silencioso.
Esta restricción explícita se añadió al corregir la revisión independiente de
1.1.0. La suma de hasta 30 tareas y el producto se calculan exactamente en un
contexto local de 256 cifras, con redondeo, exponentes y excepciones propios.
Solo el total final se redondea a céntimos con `ROUND_HALF_UP`, sin depender del
contexto Decimal global. Una hora a `1.00499999999999999999999999999` da `1.00`;
a `1.005`, `1.01`. Las pruebas incluyen un oráculo de fracciones exactas, 30 tareas
de 64 decimales y un contexto global con precisión 3 y excepciones de redondeo.

Cada transición usa `BEGIN IMMEDIATE`, revalida roles/tenant y confirma o revierte
estado y resultado juntos. Cambiar una estimación antes del gate 2 invalida su
digest anterior. Después de DONE, solo se permite repetir la misma decisión;
ni otro payload ni otro revisor sustituyen el resultado. Las pruebas cubren
reapertura, cuatro aprobaciones concurrentes y rollback ante un fallo SQLite.

La demo asigna identidades ficticias y ejecuta las aprobaciones programáticamente
para enseñar el contrato; no representa una aprobación humana real ni una UI.
Combina 10 horas con evidencia sintética y 4 horas de revisión: 14 horas, tarifa
60 EUR/h, contingencia 10%, total `924.00` EUR. No envía propuesta comercial.

Para consumir la fixture exportada desde el repositorio RAG:

```powershell
python -m agents.estimation --input intercambio.json
```

El archivo se limita a 100 KiB, versión 1 y tenant A de la demo; no puede aportar
roles. `read_workshop_exchange(path, trusted_tenant)` verifica el ámbito externo.
Las citas siguen siendo datos propuestos: la práctica no autentica su procedencia
ni evalúa su relevancia semántica. En producción deben provenir del recuperador
autorizado y conservar trazabilidad; una cadena que parece un ID no es evidencia
por sí sola. No hay envío comercial, modelo, Redis, Rails o servicio distribuido.

### Cotejo de demos

| Fuente LIDR | Implementación Focuz | Evaluación |
| --- | --- | --- |
| Agente GitHub con Pydantic AI | `agents.github_tools` | Allowlist, permisos, aprobación, digest, replay |
| Vídeo GitHub: issues a casos de QA | `agents.issue_tests` | Cinco escenarios, Markdown y vínculo a tests ejecutables |
| Estimador IT con dos revisiones | `agents.estimation` | Dos gates, tarea desconocida, precio, persistencia |
| Servicio y plataforma RAG | Intercambio JSON desde `rag.estimation` | Formato, tenant, citas y horas sin inventar |

Las implementaciones locales son originales, inspiradas en los objetivos de
las demos enlazadas. No incorporan código ni dependencias de los repositorios
de LIDR y no acreditan ejecución del stack externo.

## Fuentes

https://docs.langchain.com/oss/python/langgraph/persistence
https://docs.langchain.com/oss/python/langgraph/interrupts
https://developers.openai.com/api/docs/guides/function-calling
https://modelcontextprotocol.io/specification/latest/basic/security_best_practices
https://www.lidr.co/ai-engineering/ (estructura curricular)

Consulta: 28 de octubre de 2026. Véase NOTICE.md.

Fuentes adicionales, consultadas el 2 de noviembre de 2026:

- https://lidr.notion.site/material-workshop-ai-engineer-pro
- https://github.com/LIDR-ai/lidr-github-agent-demo
- https://www.youtube.com/watch?v=SnvkiKnBaE8
- https://github.com/LIDR-ai/lidr-it-estimator-demo
- https://github.com/LIDR-ai/lidr-rag-estimation-service-demo

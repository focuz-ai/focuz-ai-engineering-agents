# Agentes con workflow persistente y aprobación

Autor: [Focuz](https://www.focuz.io/). Caso de las sesiones 12 a 15 y extensiones senior.
Python 3.11 o posterior. Usa solo la biblioteca estándar y SQLite.

```powershell
python -m unittest discover -s tests -v
python -m agents
```

La demo crea una propuesta, la aprueba otro usuario y ejecuta dos veces la misma
solicitud. El contador final de tickets locales es 1. No envía mensajes, no crea
tickets externos, no ejecuta shell y no llama modelos reales.

## Diseño

PENDING → APPROVED → DONE. Un digest vincula la aprobación al payload. Roles y
tenant se comprueban en cada operación. Un ID no puede cambiar de tenant, autor
o contenido. La transacción que crea el ticket también persiste el estado.
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

## Fuentes

https://docs.langchain.com/oss/python/langgraph/persistence
https://docs.langchain.com/oss/python/langgraph/interrupts
https://developers.openai.com/api/docs/guides/function-calling
https://modelcontextprotocol.io/specification/latest/basic/security_best_practices
https://www.lidr.co/ai-engineering/ (estructura curricular)

Consulta: 28 de octubre de 2026. Véase NOTICE.md.

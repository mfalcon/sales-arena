# TODO — Paralelización

Hoy la simulación es secuencial: seller→consumer→seller→consumer para cada conversación, en serie. Con 20 consumers × ~5 turns × 2 calls/turn = ~200 llamadas LLM secuenciales.

## Qué se puede paralelizar

### 1. Consumer openings (trivial)
Los 20 mensajes de apertura del consumer son independientes — se pueden generar todos en paralelo.
- **Ahorro**: 20 llamadas secuenciales → 1 batch
- **Implementación**: `asyncio.gather()` o `ThreadPoolExecutor`

### 2. Seller responses dentro de la misma ronda
El seller responde a cada conversación usando el stock actual. Si el stock no cambia durante la ronda de sellers (porque los consumers aún no respondieron), todas las respuestas del seller son independientes.
- **Problema**: con el nuevo round-robin (seller→consumer por conversación), el stock SÍ cambia entre conversaciones. Paralelizar los sellers rompe la consistencia de stock.
- **Solución**: volver a batch de sellers + batch de consumers por ronda (el diseño original), pero ahora con stock correcto.
- **Trade-off**: stock ligeramente desactualizado dentro de la ronda vs velocidad.

### 3. Judge evaluations (trivial)
Las 20 evaluaciones del judge son completamente independientes — se pueden correr en paralelo.
- **Ahorro**: 20 llamadas secuenciales → 1 batch
- **Implementación**: `asyncio.gather()` o `ThreadPoolExecutor`

### 4. Múltiples simulaciones en paralelo
Para comparar prompts o modelos, correr N simulaciones simultáneas.
- **Implementación**: `multiprocessing` o correr múltiples `python run.py simulate` en paralelo.

## Enfoque recomendado

```
Fase 1 (fácil):
  - Paralelizar consumer openings con ThreadPoolExecutor
  - Paralelizar judge evaluations con ThreadPoolExecutor
  - Impacto: ~30% reducción de tiempo total

Fase 2 (medio):
  - Usar asyncio + httpx para llamadas LLM async
  - Reemplazar openai sync client por async client
  - Batch sellers en paralelo por ronda (acepta stock ligeramente desactualizado)
  - Impacto: ~60% reducción

Fase 3 (mayor cambio):
  - Pipeline completo async
  - Múltiples simulaciones en paralelo para A/B testing
  - Rate limiting inteligente por provider
```

## Consideraciones
- **Rate limits**: OpenAI tiene rate limits por minuto. Con 20 consumers, las calls paralelas pueden pegarle al rate limit. Necesita retry con backoff exponencial.
- **Stock consistency**: paralelizar seller responses dentro de una ronda significa que todos ven el mismo stock snapshot. Esto es aceptable si se documenta.
- **Reproducibilidad**: con paralelismo, el orden de ejecución varía. El random seed ya no garantiza los mismos resultados.
- **Costos**: paralelizar no cambia el costo total (mismas llamadas), solo el tiempo.

# Sales Arena

**Entrená tu agente de ventas antes de mandarlo a producción.**

Sales Arena es un entrenador de agentes de ventas. Traés tu prompt de vendedor, tu catálogo y tus reglas de negocio, y Sales Arena lo pone a vender contra consumidores simulados con distintos perfiles. Después evalúa el desempeño e itera automáticamente para optimizar profit.

## Quick Start

### 1. Instalar

```bash
pip install -r requirements.txt
```

### 2. Setup con tu agente

Dále `program.md` a tu agente (Claude Code, Codex, etc.) y dejá que te guíe por el setup. El agente te va a pedir tu catálogo, reglas de negocio, y un prompt inicial.

### 3. Correr

```bash
python run.py simulate
```

### 4. Iterar

El agente lee los resultados, ajusta el prompt, y vuelve a correr. Podés dejarlo iterando toda la noche.

## Cómo funciona

1. **Simulación**: 20 consumidores con distintos perfiles (decidido, cazador de ofertas, indeciso, exigente, apurado, curioso) chatean con tu vendedor en paralelo.
2. **Evaluación**: Un juez LLM verifica que no se violaron reglas de negocio. Un analista LLM da feedback cualitativo.
3. **Métrica**: Profit total de ventas válidas (las que no violaron constraints).
4. **Iteración**: El agente ajusta el prompt basándose en los resultados y repite.

## Estructura

```
sales-arena/
  run.py              # CLI
  program.md          # Instrucciones para el agente orquestador
  arena/              # Código core
  workspace/          # Tu catálogo, constraints, prompt (lo crea el agente)
  experiments/        # Resultados de cada experimento
```

## Requisitos

- Python 3.11+
- Un LLM compatible con API OpenAI (LM Studio, Ollama, OpenAI, Anthropic vía proxy, etc.)

## Inspiración

- [autoresearch](https://github.com/karpathy/autoresearch) de Karpathy — el loop de iteración automática
- [The Loop Is Only as Good as the Metric](https://www.distributedthoughts.org/2026-03-16-the-loop-is-only-as-good-as-the-metric/) — la importancia de métricas claras

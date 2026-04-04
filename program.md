# Sales Arena — Instrucciones para el Agente Orquestador

Eres el agente orquestador de **Sales Arena**, un entrenador de agentes de ventas. Tu trabajo es:

1. Hacer el setup inicial con el usuario
2. Correr simulaciones
3. Iterar automáticamente sobre el prompt del vendedor para maximizar profit

## Fase 1: Setup Inicial

### Conversación con el usuario

Preguntale al usuario:

1. **¿Qué vendés?** — Pedile su catálogo de productos. Aceptá cualquier formato (CSV, JSON, Excel, texto, lo que tenga). Si no tiene un archivo, pedile que te liste los productos con precio de venta y costo.

2. **¿Tenés reglas de negocio?** — Preguntale por constraints: descuentos máximos, políticas de envío, garantías, devoluciones, cosas que el vendedor NO puede hacer. Si no tiene claras las reglas, ayudalo a definirlas.

3. **¿Tenés un prompt de vendedor?** — Si ya tiene uno, usalo como punto de partida. Si no, generá uno razonable basándote en el negocio.

4. **¿Tenés conversaciones de venta reales?** — Si tiene chats previos, pedíselos. Sirven para entender el tono y las situaciones comunes.

5. **¿Qué modelos querés probar?** — Preguntale qué modelos tiene disponibles (local o API). Si usa LM Studio u Ollama, preguntale qué modelo tiene cargado.

### Armar el workspace

Con la info del usuario, creá estos archivos:

- `workspace/catalog.md` — El catálogo completo, en formato legible.
- `workspace/constraints.md` — Las reglas de negocio, una por línea.
- `workspace/seller_prompt.md` — El prompt inicial del vendedor.
- `workspace/config.yaml` — Con esta estructura:

```yaml
model:
  base_url: "http://localhost:1234/v1"  # o la URL del API
  name: "nombre-del-modelo"
  temperature: 0.7
  max_tokens: 1500
  api_key: "not-needed"  # o el API key real

# Opcional: modelo distinto para consumidores (por default usa el mismo que el vendedor)
# consumer_model:
#   base_url: "http://localhost:1234/v1"
#   name: "otro-modelo"
#   temperature: 0.7
#   max_tokens: 1500
#   api_key: "not-needed"

num_consumers: 20
max_turns: 10

# Stock inicial (producto -> cantidad)
stock:
  "Producto A": 10
  "Producto B": 5

# Costo de cada producto (para calcular profit)
cost_map:
  "Producto A": 100
  "Producto B": 200

# Precio de venta de referencia (para calcular presupuesto de consumidores)
price_map:
  "Producto A": 150
  "Producto B": 300
```

### Inicializar git

```bash
cd /ruta/al/proyecto/sales-arena
git init
git add .
git commit -m "setup: configuración inicial"
```

## Fase 2: Correr un Experimento

```bash
python run.py simulate
```

Esto corre la simulación completa (20 consumidores, 10 turnos max) y genera resultados en `experiments/<timestamp>/`.

### Leer resultados

Después de cada simulación, leé:

```
experiments/<latest>/summary.md
```

Ahí vas a encontrar: profit, ventas, violaciones, y análisis del analista.

### Overrides

```bash
python run.py simulate --consumers 10    # menos consumidores (más rápido)
python run.py simulate --turns 5         # menos turnos
```

## Fase 3: Loop de Optimización

Este es el loop principal. Iterás el prompt del vendedor para maximizar profit.

### Algoritmo

```
LOOP:
  1. Correr: python run.py simulate > run.log 2>&1
  2. Leer: experiments/<latest>/summary.md
  3. Extraer: profit total
  4. SI profit mejoró respecto al baseline:
       - git add workspace/ experiments/<latest>/
       - git commit -m "profit: $X -> $Y | sales: A/B | change: descripción del cambio"
       - Actualizar baseline
  5. SI profit NO mejoró:
       - git checkout -- workspace/seller_prompt.md  (rollback al prompt anterior)
       - Loguear en el commit message qué se intentó y por qué no funcionó
       - git commit --allow-empty -m "descartado: profit $X (baseline $Y) | cambio: descripción"
  6. Analizar el summary.md y las conversaciones para entender qué mejorar
  7. Modificar workspace/seller_prompt.md con el ajuste
  8. REPETIR
```

### Qué modificar

- **workspace/seller_prompt.md** — El prompt del vendedor. Es la variable principal.
- **workspace/config.yaml → model.name** — Solo para comparar modelos (ver Fase 4).

### Qué NO modificar — NUNCA

- **Archivos en `arena/`, `run.py`, `config.py`** — NUNCA modifiques código fuente de Sales Arena. Ni para "mejorar", ni para "arreglar", ni por ninguna razón. El código es read-only. Solo podés tocar archivos en `workspace/`.

### Qué NO modificar durante el loop

- `workspace/catalog.md` — El catálogo no cambia durante el loop de optimización.
- `workspace/constraints.md` — Las reglas no cambian.
- `num_consumers`, `max_turns` — Mantené los parámetros fijos para comparación justa.

### Tips para iterar el prompt

Basándote en el análisis del summary.md:

- Si hay **violaciones de constraints**: reforzar la instrucción en el prompt ("NUNCA des más de X% de descuento").
- Si hay **no-ventas por falta de info**: agregar instrucciones para dar más detalles del producto.
- Si hay **no-ventas por precio**: ajustar la estrategia de negociación.
- Si hay **mal manejo de stock agotado**: agregar instrucciones para ofrecer alternativas.
- Si el vendedor es **demasiado agresivo**: suavizar el tono.
- Si el vendedor es **demasiado pasivo**: agregar instrucciones de cierre.

## Fase 4: Comparación de Modelos

Después de encontrar el mejor prompt en un modelo:

1. Guardar el prompt actual como baseline.
2. Cambiar `workspace/config.yaml → model.name` al siguiente modelo.
3. Correr un experimento con el mismo prompt.
4. Comparar profit.
5. Si el nuevo modelo es mejor, iterar prompts en ese modelo (volver a Fase 3).
6. Si no, volver al modelo anterior.

Repetir para cada modelo en la lista.

## Reglas

### NEVER STOP

Una vez que el loop empezó, **NO pares a preguntar**. No preguntes "¿sigo?", "¿está bien?", "¿quieres que continúe?". El usuario puede estar durmiendo o lejos de la computadora. Vos corrés el loop indefinidamente hasta que te interrumpan manualmente.

Si te quedás sin ideas para mejorar el prompt:
- Releé las conversaciones individuales (no solo el summary).
- Probá cambios más radicales en el tono o la estrategia.
- Combiná ideas de experimentos anteriores que casi funcionaron.
- Probá otro modelo.

### Errores

- Si un experimento falla (error de LLM, timeout): logueá el error, reintenté una vez.
- Si falla dos veces seguidas: cambiá algo (reducí consumers, revisá la conexión).
- Si el modelo no responde: verificá la conexión con `curl` al endpoint.

### Formato de commits

```
profit: $X | sales: Y/Z | model: nombre | change: qué se cambió y por qué
```

Ejemplos:
```
profit: $1,250 -> $1,800 | sales: 14/20 | model: llama3 | change: agregué instrucción de ofrecer alternativas cuando no hay stock
descartado: profit $1,100 (baseline $1,800) | model: llama3 | change: tono más agresivo en cierre — bajó conversión
```

# Bugs conocidos

## BUG-001: Purchase detection por NLP acepta compras condicionales

**Archivo**: `arena/simulation.py:197-247`

**Descripción**: La detección de compra por NLP fallback (cuando no hay PURCHASE JSON marker) usa regex contra frases como "I'll take it", "deal", "let's do it", etc. No distingue entre una compra confirmada y una propuesta condicional ("**If** you can do $1,275, I'll take it").

**Consecuencias**:
- Se registra una venta sin que el seller haya aceptado el precio
- El precio se extrae del texto del consumer, no del acuerdo real
- El producto sale como `_from_context` (inferido del historial, no explícito)
- Infla artificialmente el profit con ventas a precios que el seller nunca aprobó

**Ejemplo real** (experimento 2026-04-08_21-14-50, c01):
- Seller ofreció S24 Ultra a $1,329.05 (5% off de $1,399)
- Consumer dijo: "If you can do $1,275 for the Ultra, I'll take it right now"
- Sistema detectó "I'll take it" → registró venta a $1,275
- Seller nunca aceptó $1,275 — la conversación se cortó

**Casos sospechosos en el best run (2026-04-08_21-14-50)**:
- c01: S24 vendido a $1,275 (markup -34%) — probable condicional no aceptado
- c05: Xiaomi 14 vendido a $879 (markup -47%) — probable condicional no aceptado

**Ejemplo real 2** (experimento 2026-04-08_21-14-50, c08):
- Seller ofreció Galaxy A55 a $379 (5% off)
- Consumer dijo: "That sounds like a really good **deal**! $379 is actually quite tempting. Just curious, does the A55 come with 256GB...?"
- El pattern `r"deal"` matcheó "good deal" → registró venta a $379
- El consumer NO compró — estaba preguntando sobre storage y pidiendo extras
- **Causa raíz**: el regex `r"deal"` matchea la palabra "deal" en cualquier contexto, incluyendo "good deal", "big deal", "not a big deal", etc.

**Variantes del problema**:
1. **Condicionales**: "If you can do X, I'll take it" → matchea "I'll take it"
2. **Falsos positivos por substring**: "good deal" → matchea "deal"
3. **Expresiones casuales**: "sold on the idea" → matchea "sold"

**Fix sugerido**:
- Cambiar `r"deal"` por `r"\bdeal[!.]?\s*$"` (solo "deal" como cierre de frase)
- Agregar word boundaries a todos los patterns
- Agregar patterns negativos para condicionales:
```python
conditional_patterns = [r"if you", r"if I", r"could you", r"would you", r"can you"]
```
- O mejor: solo aceptar NLP purchase si el seller ya respondió aceptando en el turno anterior.

---

## BUG-005: Fuzzy matching de cost/stock matchea producto incorrecto por substring

**Archivo**: `arena/evaluation.py:230-240`, `arena/stock.py:44-61`

**Descripción**: `_find_cost()` y `StockTracker._find_product()` usan substring matching (`key.lower() in product_lower`). Cuando un nombre de producto es substring de otro (e.g. "Samsung Galaxy S24" es substring de "Samsung Galaxy S24 Ultra 256GB"), matchea el primero que encuentra según orden del dict, no el más específico.

**Ejemplo real** (experimento 2026-04-09_14-19-36, c16):
- Consumer compró "Samsung Galaxy S24 Ultra 256GB" a $1,329
- `_find_cost` matcheó con "Samsung Galaxy S24" (cost $699) en vez de "Samsung Galaxy S24 Ultra" (cost $1,049)
- Profit reportado: $630 (real: $280)

**Fix sugerido**: Preferir el match más largo (más específico) en vez del primero encontrado.

---

## BUG-002: Judge falla control cases consistentemente

**Archivo**: `arena/evaluation.py:159-193`

**Descripción**: El judge (gemma-4-26b a temp 0.1) no pasa las validaciones de control (synthetic violation + clean conversation). Esto significa que las evaluaciones de violaciones no son confiables.

**Consecuencias**:
- Descuentos de 20-60% pasan como ventas válidas
- El profit reportado incluye ventas que deberían ser invalidadas
- El WARNING aparece en todos los experimentos pero los resultados se computan igual

**Ejemplo**: Experimento 2026-04-08_20-05-17 (baseline), c14 vendió Galaxy A55 a $240 (40% descuento, -$9 profit) — judge no lo detectó.

---

## BUG-003: Judge produce JSON malformado intermitentemente

**Archivo**: `arena/evaluation.py:140-156`, `arena/llm.py:161-241`

**Descripción**: El judge a veces genera JSON que el modelo no puede parsear (error 400 del API). Cuando esto pasa, `_run_judge` retorna `valid_sale: True` por defecto — la venta se cuenta como válida sin evaluación.

**Ejemplo de error**:
```
⚠ Judge failed for c07: Error code: 400 - {'error': 'Failed to parse input at pos 0: \n  "violations": [\n ...
```

**Consecuencia**: Ventas con violaciones claras pueden pasar sin ser evaluadas.

---

## BUG-004: Judge se loopea en output de violaciones

**Archivo**: `arena/evaluation.py:133-156`

**Descripción**: En el experimento Voss (2026-04-09_08-13-22), la violación de c17 contiene cientos de repeticiones de "the agreement to sell the agreement to sell..." — el modelo entró en un loop de generación.

**Consecuencia**: El output de violaciones se contamina con texto repetitivo. No afecta al cálculo de profit pero hace ilegible el resultado.

# Evaluation Items

Casos a evaluar manualmente al revisar experimentos. Cada item describe qué verificar y un ejemplo real.

## E-001: Seller filtra reasoning interno al consumer

**Qué buscar**: El seller muestra su cadena de razonamiento (headers markdown, análisis, verificaciones) en vez de solo la respuesta al cliente.

**Ejemplo** (experimento 2026-04-09_16-46-06, c12):
```
# Reasoning and Analysis

Looking at the catalog, I can see the Samsung Galaxy S24 is available with 8 units
in stock and a sale price of $949. The customer is asking for basic information...

## Final Answer

Hey there! 👋 Yes, we've got the Samsung Galaxy S24 in stock — 8 units available!
Price: $949 (brand new, sealed)
```

**Impacto**: El consumer ve el razonamiento del seller, incluyendo reglas internas de descuento, verificaciones de stock, y estrategia. Rompe la inmersión y puede dar ventaja al consumer en la negociación.

**Fix aplicado**: `_strip_reasoning_tags()` en `arena/llm.py` ahora detecta `## Final Answer` / `## Response` y extrae solo el contenido posterior.

**Qué verificar en futuros runs**: Que ningún mensaje del seller contenga `#`, `##`, `---`, `Reasoning`, `Analysis`, `Let me verify`, o bullet points de verificación interna.

---

## E-002: Consumer marca `status: "browsing"` cuando quiere comprar

**Qué buscar**: El message del consumer indica intención clara de compra ("I'm ready to proceed", "let's do it") pero el status JSON es `browsing`.

**Ejemplo** (experimento 2026-04-09_16-46-06, c11 seq=200):
```json
{"message": "Perfect, that sounds fair! 😊 Yes, I'm ready to proceed. The Xiaomi 14 at $571 total is still an amazing deal.", "status": "browsing"}
```

**Impacto**: La venta no se registra. El consumer "quiere comprar" pero el sistema no lo detecta.

**Frecuencia**: 1/62 intents (~1.6%) en el último run con qwen.

---

## E-003: Fuzzy matching de producto matchea variante incorrecta

**Qué buscar**: El nombre de producto en sale_details no matchea exactamente el catálogo y el sistema elige el producto equivocado.

**Ejemplo** (experimento 2026-04-09_14-19-36, c16):
- Consumer compra: "Samsung Galaxy S24 Ultra 256GB"
- Sistema matcheó: "Samsung Galaxy S24" (cost $699) en vez de "Samsung Galaxy S24 Ultra" (cost $1,049)
- Profit inflado: $630 reportado vs $280 real

**Fix aplicado**: `_find_cost()` ahora prefiere el match más largo. Consumer prompt incluye lista exacta de nombres de productos.

**Qué verificar**: Que `sale_details.product` sea exactamente uno de los nombres del catálogo.

---

## E-004: Consumer genera JSON malformado o texto garbled

**Qué buscar**: Mensajes con texto repetitivo ("a lot of lot of lot of"), JSON anidado en el message, o fields vacíos.

**Ejemplo** (experimento 2026-04-09_10-00-54, c16 con gemma):
```json
{"message": "Yayyyy! 🥳✨ Let's do it!! 💖 The Google Pixel 8 is $711.55, free shipping, and with the warranty! 🛡️ Okay, ok...", "status": "browsing"}
```
Texto garbled + status incorrecto.

**Impacto**: Conversación se atasca, timeout, o venta perdida.

**Frecuencia**: ~60% de los mensajes con gemma-4-26b, <3% con qwen3.5-27b.

---

## E-005: Compra condicional registrada como venta

**Qué buscar**: El consumer dice "if you can do $X, I'll take it" y el sistema registra la venta al precio propuesto sin que el seller haya aceptado.

**Ejemplo** (experimento 2026-04-08_21-14-50, c01 con regex viejo):
- Seller ofreció S24 Ultra a $1,329.05
- Consumer: "If you can do $1,275 for the Ultra, I'll take it right now"
- Sistema detectó "I'll take it" → venta a $1,275

**Fix aplicado**: Eliminado NLP regex. Consumer ahora genera JSON estructurado con `status: "purchase"` solo cuando confirma compra real.

**Qué verificar**: Que ningún `status: "purchase"` corresponda a una oferta condicional.

---

## E-006: Judge falla control cases

**Qué buscar**: Warning en summary.md: "The constraint judge did not pass the control cases."

**Impacto**: Todas las evaluaciones de violaciones de ese experimento son poco confiables. Descuentos excesivos pueden pasar como válidos.

**Frecuencia**: Consistente con gemma-4-26b. Variable con qwen.

---

## E-007: Seller muestra cálculo de descuento al consumer

**Qué buscar**: El seller dice algo como "price × 0.91 = $591" o "Minimum allowed: $649 × 0.91" al cliente.

**Ejemplo** (experimento 2026-04-09_14-19-36, c19):
```
Minimum allowed: $649 × 0.91 = $591 (this is the absolute floor)
```

**Impacto**: El judge lo interpreta como "invented policy". Rompe la naturalidad de la conversación.

**Fix aplicado**: Seller prompt actualizado: "Do NOT show any calculation or formula to the customer."

**Qué verificar**: Que el seller solo diga "The best I can do is $X" sin mostrar la matemática.

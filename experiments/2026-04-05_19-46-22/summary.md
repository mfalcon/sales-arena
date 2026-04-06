# Experimento 2026-04-05_19-46-22

## Resultado
- **Modelo**: qwen3.5-27b-claude-4.6-opus-reasoning-distilled
- **Profit**: $3,101.05 (solo ventas válidas)
- **Revenue**: $11,386.05
- **Ventas válidas**: 15/40
- **Ventas inválidas** (violaciones): 5
- **No-ventas**: 20
- **Tokens totales**: 616056

## Violaciones
- **c05**: Regla 8 - No ofrecer productos que no estén en el catálogo — El vendedor ofrece un descuento del 10% sobre el Xiaomi 14 ($599), resultando en $539.10, pero luego menciona un precio final de $737.0 que no corresponde a ningún cálculo válido según las reglas de negocio.
- **c05**: Regla 2 - Envío gratis en compras superiores a $700 — El vendedor menciona un precio final de $737.0 sin explicar cómo se llegó a ese monto. Si el Xiaomi 14 cuesta $599 y aplica descuento del 10% ($539.10), el envío sería $25 (total: $564.10). Si no hay descuento, el total sería $624. El precio de $737.0 no tiene sentido según las reglas.
- **c05**: Regla 1 - Descuento máximo del 10% — El vendedor ofrece un descuento del 10% sobre el Xiaomi 14 ($599), resultando en $539.10, pero luego menciona un precio final de $737.0 que no corresponde a ningún cálculo válido según las reglas de negocio.
- **c18**: Descuento máximo: 10% sobre el precio de venta. No se puede superar bajo ninguna circunstancia. — El vendedor menciona 'con el 9% máximo de descuento que puedo dar' para el Pixel 8 Pro ($999). Sin embargo, en el Turno 1 ya ofreció un 5% de descuento sobre $999 (llegando a $949), lo cual es correcto. Pero luego en el Turno 2 dice que con 'el 9% máximo' llegaría a $909. Esto es inconsistente porque: 1) Primero dijo que su mejor precio era $949 (5%), y luego menciona un 9%. 2) Más importante aún, para el Pixel 8 ($749), ofrece un descuento del 9% llegando a $681.60. El cálculo correcto sería: $749 × 0.91 = $681.59 (redondeado). Esto está dentro del límite del 10%. Sin embargo, el problema es que en el Turno 2 menciona 'el 9% máximo de descuento' como si fuera una regla fija, cuando la regla dice 10% máximo. No es una violación directa porque 9% < 10%, pero es confuso.
- **c18**: No se pueden inventar especificaciones o características que no estén en el catálogo. — El vendedor afirma que el Pixel 8 tiene 'Mismo procesador Tensor G3' y 'Misma cámara de 50MP' que el Pro. Según el catálogo: Pixel 8 tiene 'Tensor G3, 128GB, 50MP' y Pixel 8 Pro tiene 'Tensor G3, 128GB, 50MP, sensor de temperatura'. Esta afirmación es CORRECTA según el catálogo. No hay violación aquí.
- **c18**: Envío gratis en compras superiores a $700. — El vendedor ofrece el Pixel 8 con descuento al precio de $681.60 y dice 'con envío GRATIS'. Sin embargo, $681.60 es MENOR que $700, por lo tanto NO califica para envío gratis según la regla 2. El envío debería ser $25 (regla 3). Esta es una VIOLACIÓN clara.
- **c30**: Descuento máximo: 10% sobre el precio de venta. No se puede superar bajo ninguna circunstancia. — El vendedor ofreció un descuento del 9%, lo cual está dentro del límite permitido (máximo 10%). Sin embargo, al calcular el precio final: $749 × 0.91 = $681.59, el descuento es correcto y no viola la regla.
- **c30**: Envío gratis en compras superiores a $700. — El vendedor dijo que el envío es GRATIS porque supera los $700. Sin embargo, el precio con descuento ($681.59) NO supera los $700. Esto es una violación de la regla: el envío debería ser $25, no gratis.
- **c30**: Envío estándar: $25 para compras menores a $700. — El vendedor ofreció envío GRATIS cuando el total ($681.59) es menor a $700. Según la regla, debería cobrar $25 de envío.
- **c33**: Descuento máximo: 10% sobre el precio de venta. No se puede superar bajo ninguna circunstancia. — El vendedor ofreció un descuento del 13.35% (de $749 a $649), superando el límite máximo permitido del 10%. El precio mínimo válido sería $674.10.
- **c39**: No se pueden inventar especificaciones o características que no estén en el catálogo. — El vendedor afirma que hay '9 unidades disponibles' del Samsung Galaxy A55, cuando el catálogo indica claramente que hay 12 unidades. Esto constituye una invención de datos no verificados.

## Análisis
**ADVERTENCIA: El juez de constraints no pasó los casos de control. Las evaluaciones de violaciones pueden no ser confiables.**

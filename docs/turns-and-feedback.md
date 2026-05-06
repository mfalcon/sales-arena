# Sales Arena — Turns and feedback loop

Sequence view of one iteration: the round-robin turns between the seller LLM and 20 consumer LLMs, the shared live stock, the judge evaluation at the end, and the profit-based commit/rollback decision.

For the high-level temporal flow, see [`loop-macro.md`](loop-macro.md). For the static ownership map, see [`methodology.md`](methodology.md).

```mermaid
sequenceDiagram
    autonumber
    participant ORC as Orquestador
    participant ENG as run.py simulate
    participant SLLM as Seller LLM
    participant C1 as C01 (decisive)
    participant C2 as C02 (browser)
    participant CN as C03..C20<br/>(otros 18 perfiles)
    participant STOCK as Stock vivo
    participant JLLM as Judge LLM

    ORC->>ENG: simulate (con seller_prompt actual)

    Note over ENG,CN: INIT — 20 consumers c/u con perfil + budget + producto interés

    par C1 abre
        ENG->>C1: system prompt (perfil + budget)
        C1-->>ENG: opening msg
    and C2 abre
        ENG->>C2: system prompt
        C2-->>ENG: opening msg
    and C3..C20
        ENG->>CN: ...
        CN-->>ENG: ...
    end

    Note over ENG,CN: ROUND 1 (orden aleatorio entre las 20)

    ENG->>SLLM: seller_prompt + catálogo + stock LIVE + summary otros 19
    SLLM-->>ENG: respuesta a C1
    ENG->>C1: forward respuesta
    C1-->>ENG: reply {message, status:"purchase", price:1099}
    ENG->>STOCK: decrement iPhone 15

    ENG->>SLLM: seller_prompt + stock ACTUALIZADO + summary
    SLLM-->>ENG: respuesta a C2
    ENG->>C2: forward
    C2-->>ENG: reply {message, status:"browsing"}

    Note over ENG,CN: ... así C3..C20<br/>cada turno: seller ve stock LIVE + status de las otras 19

    Note over ENG,CN: ROUNDS 2..10 (hasta close, no_purchase, o turn limit)

    Note over ENG,JLLM: EVALUACIÓN — al cerrar las 20 conversaciones

    loop Cada una de las 20 conversaciones
        ENG->>JLLM: transcript + constraints + catálogo
        JLLM-->>ENG: {violations[], bad_treatment, valid_sale}
        Note right of ENG: + Python regex verifier<br/>checkea purchase confirmation
    end

    ENG-->>ORC: summary.md (profit · sales · violations)

    Note over ORC: FEEDBACK LOOP — comparación vs best

    alt profit > best
        ORC->>ORC: git add seller_prompt.md
        ORC->>ORC: git commit "profit: $X -> $Y"
        Note right of ORC: nuevo baseline
    else profit ≤ best
        ORC->>ORC: git checkout BEST_HASH -- seller_prompt.md
        Note right of ORC: rollback al best previo
    end

    Note over ORC: Si guardrail no triggered → siguiente iteración<br/>(edit prompt, repeat)
```

## Cómo leer el diagrama en una charla

1. **Init (paso 2)**: cada consumer recibe un perfil distinto + budget + producto de interés. Los 20 abren con un mensaje en paralelo. Esto representa "el día de la tienda" — 20 chats simultáneos en WhatsApp.

2. **Round 1, orden aleatorio**: el engine elige una conversación al azar y le pide al seller que responda. El seller ve stock LIVE y el status de las otras 19 conversaciones. Esto es lo que vuelve la simulación pseudo-paralela: el seller puede aprender a distribuir inventario porque siempre tiene visibilidad cruzada.

3. **Compras decrementan stock antes del próximo turno** (paso 11). Si C1 compró el último iPhone, cuando le toque a C2 el seller verá stock=0 para ese producto y podrá ofrecer alternativas.

4. **Loop hasta close, no_purchase, o turn limit (10 rounds)**: cada conversación termina cuando el cliente confirma compra, dice no, o se acaban los turnos.

5. **Evaluación**: cada conversación va al judge LLM (constraints + bad_treatment) + al verifier Python (regex sobre la confirmación de compra). Solo las ventas sin violaciones cuentan al profit.

6. **Feedback loop**: el orquestador compara profit vs best. Si mejora → commit. Si no → `git checkout` al hash del best previo. Esa es la memoria del baseline.

## Detalles técnicos relevantes

- El seller corre con temperatura **0.7** — natural, varía respuestas.
- Los consumers también temp **0.7** — comportamiento humano variable.
- El judge corre con temperatura **0.1** — bajo para consistencia.
- El stock tracker es un objeto Python compartido (`arena/stock.py`) — no es una DB, es estado en memoria que vive el experimento.
- El "summary de las otras 19 conversaciones" que ve el seller es texto plano: `c02: status=browsing, last=customer asked about Pixel 8 ...`. No son los transcripts completos, son resúmenes para no quemar contexto.

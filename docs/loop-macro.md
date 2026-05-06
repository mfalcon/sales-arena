# Sales Arena — Loop macro de optimización

```mermaid
flowchart TD
    U([Usuario define el negocio<br/>catálogo · reglas · prompt inicial]) --> ORQ

    ORQ{{"Agente orquestador<br/>(Claude Code / Codex)"}}

    ORQ -->|"1. edita UN cambio"| PR[/seller_prompt.md/]
    PR -->|"2. corre"| SIM["simulate<br/>20 consumidores × 10 turnos"]
    SIM -->|"3. genera"| EXP[/"experiments/&lt;ts&gt;/<br/>profit · ventas · violaciones"/]
    EXP -->|"4. analiza"| ORQ

    ORQ --> Q{¿profit mejoró?}
    Q -->|sí| OK["git commit<br/>nuevo baseline"]
    Q -->|no| BAD["git reset --hard<br/>vuelve al mejor"]
    OK -.-> ORQ
    BAD -.-> ORQ

    ORQ -.->|stop| G["guardarrails<br/>· N iteraciones<br/>· 3 sin mejora<br/>· profit cae &gt;90%<br/>· judge poco confiable"]

    classDef user fill:#e8f4f8,stroke:#2c7a8c,stroke-width:2px
    classDef agent fill:#fff4e6,stroke:#d97706,stroke-width:2px
    classDef file fill:#f3f4f6,stroke:#6b7280,stroke-width:1px
    classDef action fill:#ecfdf5,stroke:#059669,stroke-width:2px
    classDef bad fill:#fef2f2,stroke:#dc2626,stroke-width:2px
    classDef stop fill:#fef3c7,stroke:#b45309,stroke-width:1px,stroke-dasharray: 4 4

    class U user
    class ORQ agent
    class PR,EXP file
    class SIM,OK action
    class BAD bad
    class G stop
```

## Cómo narrarlo (orden 1→4)

1. El orquestador **edita un solo cambio** en el prompt — disciplina clave del método.
2. **Simula** una tanda completa de clientes contra ese prompt (20 conversaciones pseudo-paralelas).
3. La simulación deja un **artefacto inmutable** en disco con todas las conversaciones, ventas y violaciones.
4. El orquestador **lee, decide, y commitea** — git es la memoria del baseline.

El loop nunca degrada el mejor resultado: si el profit empeora, hace `reset` al último commit ganador.

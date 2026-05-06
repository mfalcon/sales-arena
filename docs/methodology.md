# Sales Arena — Methodology Map

This shows the **system architecture** — who owns which files, how the LLMs are wired, and how the optimization loop persists state. For the temporal flow of one iteration, see [`loop-macro.md`](loop-macro.md).

```mermaid
flowchart TB
    %% USER ZONE
    subgraph USR["👤 USUARIO · define el negocio (read-only para el agente)"]
        direction LR
        CAT[catalog.md<br/><sub>productos · specs</sub>]
        CON[constraints.md<br/><sub>reglas de negocio</sub>]
        CFG[config.yaml<br/><sub>modelos · num_consumers · stock · price_map · cost_map</sub>]
    end

    %% ORCHESTRATOR
    subgraph ORC["🤖 ORQUESTADOR · Claude Code / Codex (loop driver)"]
        direction TB
        AGENT[loop driver]
        PROG[program.md<br/><sub>playbook · Phase 1-5</sub>]
        GUARD[CLAUDE.md guardrails<br/><sub>stop si: 3 sin mejora · ↓90% · count · judge unreliable</sub>]
        PROG -.-> AGENT
        GUARD -.-> AGENT
    end

    %% WRITABLE
    SP[✏️ seller_prompt.md<br/><sub>variable a optimizar</sub>]

    %% ENGINE
    subgraph ENG["⚙️ run.py simulate"]
        direction LR
        SIM[simulation engine<br/><sub>20 conv round-robin pseudo-paralelo</sub>]
        STOCK[(📦 stock vivo<br/>compartido)]
        VER[🐍 Python regex verifier<br/><sub>_verify_purchase_details</sub>]
    end

    %% LLMs
    subgraph LLM["🧠 LLMs (3 roles configurables independientemente)"]
        direction LR
        SLLM[seller<br/><sub>1 modelo · 1 prompt · temp 0.7</sub>]
        CLLM[consumers<br/><sub>1 modelo · 6 perfiles · 20 instancias · temp 0.7</sub>]
        JLLM[judge<br/><sub>1 modelo · temp 0.1 · constraints + bad_treatment</sub>]
    end

    %% OUTPUT
    subgraph OUT["📁 experiments/&lt;ts&gt;/"]
        direction LR
        SUM[summary.md<br/><sub>profit · sales · violations</sub>]
        CONV[conversations/*.md<br/><sub>20 transcripts</sub>]
        TSV[../results.tsv<br/><sub>log longitudinal</sub>]
    end

    %% MEMORY
    GIT[💾 git<br/><sub>commits = baselines · rollback al best</sub>]

    %% FLOWS
    USR --> ENG
    SP --> ENG
    AGENT -->|"1.edita"| SP
    AGENT -->|"2.corre"| ENG
    SIM <--> SLLM
    SIM <--> CLLM
    SIM --> JLLM
    SLLM <-.->|"round-robin"| CLLM
    CLLM -.->|"compras decrement"| STOCK
    SLLM -.->|"lee live"| STOCK
    JLLM --> SUM
    VER --> SUM
    SIM --> SUM
    SIM --> CONV
    SIM --> TSV
    SUM -->|"3.analiza"| AGENT
    CONV -->|"3.analiza"| AGENT
    AGENT -->|"4.commit si mejora<br/>4.rollback si no"| GIT
    GIT -.->|"baseline"| AGENT

    classDef user fill:#e8f4f8,stroke:#2c7a8c,stroke-width:2px
    classDef agent fill:#fff4e6,stroke:#d97706,stroke-width:2px
    classDef writable fill:#fef3c7,stroke:#b45309,stroke-width:3px
    classDef engine fill:#ecfdf5,stroke:#059669,stroke-width:2px
    classDef llm fill:#f3e8ff,stroke:#7c3aed,stroke-width:2px
    classDef out fill:#f3f4f6,stroke:#6b7280,stroke-width:1px
    classDef mem fill:#fee2e2,stroke:#dc2626,stroke-width:2px

    class CAT,CON,CFG user
    class AGENT,PROG,GUARD agent
    class SP writable
    class SIM,STOCK,VER engine
    class SLLM,CLLM,JLLM llm
    class SUM,CONV,TSV out
    class GIT mem
```

## Ownership map

| Region | Files / components | Who modifies | Notes |
|---|---|---|---|
| 👤 User | `catalog.md`, `constraints.md`, `config.yaml` | User only | Define the business. Agent reads but does not modify (Phase 4 exception: agent may change `config.yaml → model.name` to compare models). |
| 🤖 Orchestrator | `program.md`, `CLAUDE.md` | User (durable) | The agent's playbook and stop conditions. Read by the agent at the start of each session. |
| ✏️ Writable | `workspace/seller_prompt.md` | Agent | The single variable being optimized. One change per iteration. |
| ⚙️ Engine | `arena/`, `run.py` | Source — never modified during the loop | The simulator + judge harness. Modifying these is harness engineering, not prompt engineering. |
| 📁 Output | `experiments/<ts>/` | Engine writes, agent reads | Each iteration produces an immutable experiment directory. |
| 💾 Memory | `git` | Agent (commits and rollbacks) | The commit history IS the optimization trace. Rollback to the best commit if profit regresses. |

## LLM topology

Three roles, each configurable independently in `config.yaml`:

- **Seller** — one model, one prompt (`seller_prompt.md` + catalog + constraints + live stock + summary of other conversations). High temperature (0.7) for natural chat.
- **Consumers** — one model, six personality profiles defined in `arena/prompts.py` (decisive, bargain_hunter, indecisive, demanding, rushed, browser). Each of the 20 conversations gets one profile + a randomised budget + a product of interest. High temperature (0.7).
- **Judge** — one model, low temperature (0.1) for consistency. Receives the full transcript + constraints + catalog and returns JSON with violations and bad_treatment flags.

A fourth piece is **not** an LLM: the `_verify_purchase_details` Python verifier in `arena/evaluation.py`. It uses regexes to confirm the customer's last message was an unconditional yes (no `?`, no hedging like "if you can"). This is what produces most `purchase_verification` flags — it's deterministic harness, not model output.

## Key methodological choices

1. **One change per iteration.** The agent edits `seller_prompt.md` once per loop iteration, runs `simulate`, decides commit vs rollback, then iterates. Multi-change iterations make attribution impossible.
2. **Profit as the metric.** `profit = Σ (price - cost)` over valid sales only (sales without violations). This is the optimization target.
3. **Pseudo-parallel simulation.** The 20 conversations interleave turns round-robin, so the seller sees live stock and a summary of all other conversations on every turn. Distribution-of-inventory strategies become learnable.
4. **Git as memory.** `git commit` after improvements, `git checkout -- workspace/seller_prompt.md` after regressions. The commit history is the optimization trace; rollback is one command away.
5. **Guardrails for unattended runs.** The loop stops itself on count reached, 3 consecutive no-improvement iterations, profit dropping >90%, or judge unreliability — so the agent can run overnight without supervision.

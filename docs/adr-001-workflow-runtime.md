# ADR 001 — The workflow graph is hand-written, not LangGraph

**Status:** accepted, 2026-09-02
**Context:** README §13 (agentic workflow), §4 (certificate mapping), §20 (what not to build first)

## Context

README §13 specifies the case flow as an explicit state graph and names
LangGraph as the runtime. §4 maps that to the *AI agents with RAG & LangChain*
course block, so the choice carries a learning objective as well as an
engineering one.

Stage 1 of the production roadmap had to turn `case_assembly.run_case` — a
function calling seven other functions — into that graph. The question was
whether to adopt LangGraph now or write the executor.

## Decision

Write the executor. `src/rxauth_ai/workflow.py` declares the flow as a tuple of
named `Node`s over a typed `WorkflowState`, and `run_workflow` executes them in
order, recording every node.

## Why

**LangGraph's value is in what this graph does not do.** It earns its weight
orchestrating model calls: branching on model output, retrying flaky APIs,
streaming partial state, checkpointing long conversations, running nodes
concurrently. This graph is linear by deliberate design (§13 chose that over a
service-fragmented alternative), has no branches or loops, makes zero model
calls, and runs offline in single-digit seconds. None of the framework's
capabilities would be exercised.

**The dependency cost is real and asymmetric.** LangGraph pulls
`langchain-core` and its transitive tree into a package whose only runtime
dependencies today are numpy, opencv, pillow, pydantic, pypdf, reportlab, and
scikit-learn. README §8 already treats CI lightness as a property worth
protecting — that is why the transformer lives behind an optional `deep` extra.
Adding a heavier framework to run thirteen functions in order spends that
budget for no capability.

**The valuable part is runtime-agnostic.** What makes the flow auditable is the
decomposition into named stages, the typed state, the per-node record of status
and component versions, and the explicit failure state that marks downstream
nodes `NOT_RUN`. All of that lives in the node definitions. `run_workflow` is
about forty lines; replacing it with a LangGraph `StateGraph` that calls the
same node functions is an adapter, not a rewrite.

**It keeps a property the project sells.** Every number in `reports/` is
reproducible from a clean clone. A deterministic executor with no scheduler and
no framework version in the loop keeps that true.

## What this costs

- **The course mapping is weaker.** §4 ties §13 to the LangChain/LangGraph
  block, and this does not demonstrate the framework. The mitigation is the
  adapter above: writing it later is a small, self-contained exercise that
  demonstrates the framework *and* shows why the abstraction boundary was
  drawn where it was — arguably a better portfolio artifact than importing it
  by default. This ADR exists so the choice reads as considered rather than
  avoided.
- **Anything the framework would have given free must be built.** Concurrency,
  checkpointing, and streaming do not exist. None is needed by a linear
  offline graph; all become reasons to revisit this decision.

## When to revisit

Any one of these should reopen it:

- a node calls a model API, making retries, timeouts, and partial failure real;
- the flow needs to branch on a node's output, or loop;
- a run becomes long enough that a reviewer needs streamed partial state;
- nodes need to run concurrently;
- work needs to resume mid-graph after a crash, requiring checkpointing.

Until then, `Node.retries` exists and every node sets it to zero, because every
current node is deterministic and offline and a retry could only repeat the
same failure. The mechanism is in place so the first networked node can declare
its own policy next to itself.

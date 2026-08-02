# 0001. OpenAI-backed agent runner: scope, API choice, model-id config, no shared provider abstraction

## Status

Accepted — 2026-08-03. **§3 revised 2026-08-04** — the `OPENAI_MODEL`
fallback described below was removed entirely after the model-provenance
investigation (`docs/adr/0002-outcome-taxonomy.md`) found it was exactly
the ambiguous second source this ADR's own reasoning already argued
against. The original text is kept, struck through in spirit not in fact,
so the "why did we ever have this" question stays answerable — see the
"Revised 2026-08-04" callout inside §3.

## Context

`LangGraphRunner` (ROADMAP W8) is a real, model-backed `AgentRunner` on the
Anthropic API, registered as `"langgraph"`. Live verification of the platform
(actually running the toy Q&A benchmark against a real model and reconciling
cost against an invoice) has not happened yet — W8's exit criteria are still
open (see ROADMAP.md §0) and real Anthropic runs cost real money.

DESIGN.md §8 states the platform "is not bound to one model provider" and
names OpenAI explicitly as first-class alongside Anthropic. A second,
cheaper provider makes live verification tractable without waiting on W8's
Anthropic-specific exit criteria, and exercises the registry/config path
against a second real SDK instead of just the mock runner.

Four questions needed a decision before writing `OpenAIRunner`.

## Decision

### 1. This is a new adapter, not a W8 fix

`OpenAIRunner` is registered as `"openai"`, alongside `"langgraph"`, not in
place of it. `LangGraphRunner` is unmodified. W8's exit criteria are about
the Anthropic runner specifically (does it solve the toy benchmark, does its
cost tracking reconcile against a real invoice) and are unaffected by this
work — **W8 is not closed by this ADR or by `OpenAIRunner` existing.**
Verifying `OpenAIRunner` against a live invoice is a separate, later concern
with its own exit criteria, not inherited from W8's.

### 2. Chat Completions, not the Responses API

OpenAI currently ships two general-purpose text/tool-calling surfaces:
Chat Completions (`client.chat.completions.create`) and the newer, stateful
Responses API (`client.responses.create`, item-based input/output,
`previous_response_id` for server-side conversation state).

**Chat Completions is used.** No concrete reason was found to prefer
Responses for this runner:

- The runner's loop shape — build a growing `messages: list[dict]`, append
  one assistant turn and matching tool-result turns per iteration, replay
  the whole list on every call — is exactly `LangGraphRunner`'s shape today
  (`agents/langgraph_runner.py`'s `_gen`). Chat Completions is a direct,
  message-list-for-message-list port; Responses' item/state model
  (`previous_response_id`, typed `output` items instead of `choices`) is a
  different paradigm the runner would have to be redesigned around, for no
  requirement this task has.
- Chat Completions is the mature, stable surface (no beta headers, unchanged
  request/response shape for years); Responses is newer and still
  accumulating capabilities. Stability matters more here than access to
  Responses-only features (built-in hosted tools, background mode) that
  this runner's two toy tools (`calculator`, `finish`) don't need.
- Reproducibility (DESIGN.md §4, "requires more than seeds") favors a
  stateless-per-call API whose full input is captured in the trace over one
  whose behavior can depend on server-side state referenced by an opaque
  `previous_response_id` — the latter is harder to replay from a recorded
  bundle alone.
- The task's own Anthropic→OpenAI mapping table is Chat-Completions-shaped
  (`tool_calls`, `finish_reason`, `prompt_tokens`/`completion_tokens`),
  confirmed against the installed SDK (`openai==1.83.0`,
  `ChatCompletionMessage`/`Choice`/`CompletionUsage` field names) before any
  code was written.

If a future runner genuinely needs Responses-only features, that is a new
decision with its own ADR, not a silent migration of this one.

### 3. Model id lives in the run config, not `OPENAI_MODEL`

The model id is a required, explicit field on the config passed to
`OpenAIRunner.__init__` (mirroring `LangGraphRunner(model=...)`), the same
way `configs/toy_langgraph.yaml` sets `model: "claude-haiku-4-5"` at the
config level, not via an Anthropic-side environment variable.

**Reasoning:** DESIGN.md §7 makes the result bundle "the contract" and §9
requires reproducibility to capture every input that determined behavior,
including "the same model version." A model resolved from an ambient
environment variable at run time is not part of the checked-in
`RunConfig` — two runs of the same config on two machines with different
`OPENAI_MODEL` values would silently diverge, and the bundle would have no
record of which model actually ran unless the *resolved* value is captured
separately. That directly contradicts the reproducibility claim.

~~`OPENAI_MODEL` may still exist as a **fallback default** (mirroring
`DEFAULT_MODEL` in `langgraph_runner.py`, which is a code constant, not an
env var — `OPENAI_MODEL` is the closer analogue to letting an operator set
a default without editing every config). If a caller relies on that
fallback, the runner **must** write the resolved model id into the emitted
trace/bundle (the same way `agent_id` already encodes the model:
`f"openai-chat:{self._model}"`), so replay and audit never have to guess
which model a bundle without an explicit `model:` field actually used.~~

**Revised 2026-08-04:** this was wrong, and the mistake was caught by
tracing the actual paths a model id can reach a runner through, not by
re-reading the reasoning above. `OPENAI_MODEL` *was* implemented as
described, and it *was* exactly the "resolved from an ambient environment
variable" case the paragraph above already names as the reproducibility
failure — a fallback env var is not made safe by also encoding its result
in `agent_id` at runtime, because `agent_id` was never written back into
`config.json` either (`docs/adr/0002-outcome-taxonomy.md`, Axis 4: no
model-backed bundle has ever recorded which model ran, full stop, fallback
or not). The "write the resolved value back" idea in the struck-through
text above is also rejected, not just deferred: writing back would leave
*two* paths that can disagree (the config's `model:` field, and whatever
got resolved and written back) instead of one that can't. The actual fix
is narrower and blunter: **`OPENAI_MODEL` is deleted entirely.**
`OpenAIRunner._model` now resolves from exactly two sources — the explicit
`model` constructor argument, or `DEFAULT_MODEL` (a code constant, used
only by direct/programmatic construction such as tests, never reachable
from a config the CLI loads). `config/loader.py` now raises
`ConfigurationError` at load time if a model-backed `runner_id` (`langgraph`
or `openai` — both had this gap, not just the one this ADR was originally
about) omits `model` — failing loudly instead of silently defaulting.

**This does not fully close the provenance gap, and isn't meant to.**
Deleting the fallback guarantees the model used in a CLI-driven run is
always explicit and unambiguous; it does not make `config.json` record
that model, because `RunConfig` (the schema serialized into `config.json`)
has no `model` field and none was added — doing so is a config-schema
change, explicitly out of scope for the session that made this revision
(batched into the next, schema-focused session per
`docs/adr/0002-outcome-taxonomy.md`'s open questions, specifically the
choice between fixing `agent_id` to reflect the resolved model versus
adding a dedicated `model_id` field). What changed here is that there is
now exactly one legal source for the model on any config-driven run, not
that the bundle records it yet.

### 4. No shared provider abstraction

`OpenAIRunner` and `LangGraphRunner` are independent implementations, each
directly satisfying the structural `AgentRunner` protocol
(`core/protocols.py`). There is no shared base class, mixin, or "provider
adapter" layer factoring out their common shape (parsed-response normalizer,
cost-tick loop, resource-limit checks).

**Recommend against building one now, and say so explicitly so a future
session doesn't "tidy" this into an abstraction:** two implementations is
not enough evidence to design the right seam. The two providers' loop shapes
look similar today (both: call model → parse → tool-call loop → cost tick →
limit check) but differ in kind at exactly the two places this task's own
mapping table calls out — tool arguments arrive parsed (Anthropic) vs. as a
JSON string that can be malformed (OpenAI), and the turn-structure for
tool results differs (`tool_result` content blocks vs. separate
`role: "tool"` messages, one per call). A base class written from a sample
of two risks baking in accidents of the *current* two providers as if they
were universal, and is harder to remove later than a duplicated ~150-line
loop is to delete. If a third provider (Bedrock, Ollama) shows up and the
duplication pattern holds, that is the point to look for the real shared
seam — not before.

## Consequences

- `OpenAIRunner` duplicates `LangGraphRunner`'s loop structure by design;
  a change to one (e.g. a new resource-limit check) does not automatically
  propagate to the other and must be ported by hand if wanted on both.
- `configs/toy_openai.yaml` and `configs/toy_langgraph.yaml` both set
  `model:` explicitly, and now must — `config/loader.py` rejects a
  model-backed run config that omits it (revised 2026-08-04; `OPENAI_MODEL`
  is no longer read anywhere).
- Live verification of `OpenAIRunner` against a real invoice, and W8's own
  exit criteria for `LangGraphRunner`, remain separate, both-still-open
  pieces of work after this ADR.

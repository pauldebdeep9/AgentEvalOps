"""AzureOpenAIRunner — Azure OpenAI as an ``AgentRunner`` (Axis B of
``docs/adr/0004-cloud-backend-two-axes.md``).

Scope decision
--------------
This is **Axis B only** — a model-provider concern, already covered by
``DESIGN.md`` §8's existing "not bound to one model provider" non-goal. It
has nothing to do with Axis A (Azure as a ``CloudBackend``, out of v0.5
scope): no ``cloud/`` package, no ``BACKEND_AZURE``, no compute/secrets/
telemetry plane. This runner just calls a different endpoint of the same
``openai`` SDK ``OpenAIRunner`` already depends on.

Subclass, don't duplicate
--------------------------
``docs/adr/0001-openai-runner-chat-completions.md`` rejected a shared base
class between ``LangGraphRunner`` and ``OpenAIRunner`` because the two loops
differ *in kind* (parsed-dict vs. JSON-string tool arguments, etc.). Azure
OpenAI is not a third provider in that sense: same SDK, same Chat
Completions surface, same tool-call semantics as ``OpenAIRunner``. This
class subclasses :class:`~agentevalops.agents.openai_runner.OpenAIRunner`
and overrides only the three seams that genuinely differ:

1. :meth:`_build_real_client` — constructs ``openai.AzureOpenAI`` instead of
   ``openai.OpenAI``, keyed by endpoint + api_version instead of just a key.
2. :meth:`_compute_step_cost` (added to ``OpenAIRunner`` for exactly this
   purpose) — Azure's own price table, not OpenAI's.
3. ``agent_id`` — a distinguishable ``azure-openai-chat:`` prefix, so
   bundles from the two runners are never confused.

``_gen`` (the agent loop itself) is never touched or duplicated.

Deployment name vs. model id
------------------------------
Azure OpenAI keys API requests by **deployment name** (an account-specific
resource identifier the caller chooses when provisioning the model in their
Azure resource), not by model id. ``OpenAIRunner`` was refactored
(``docs/adr/0004``, D2) to carry ``_api_model`` (what goes into the
``model=`` request parameter) and ``_model_id`` (pricing table key +
``agent_id`` provenance) as two separate attributes for exactly this reason.
Here, ``_api_model`` is set to the deployment name and ``_model_id`` stays
the model id — so the API is called against the right deployment while
cost/identity are still computed and reported against the right model.

Pricing: own table, fail loud on unknown model, never guess
--------------------------------------------------------------
Unlike ``OpenAIRunner``'s ``_FALLBACK_PRICE_PER_1M_USD`` (a deliberate
"never over-report for an unknown model" choice, sound because an unknown
OpenAI model id is an occasional miss), an unknown model id here would be
wrong on *every* Azure run: the API is keyed by deployment name, so nothing
the user configures could ever coincidentally match a model-id-keyed price
table. A silent fallback would under-report cost on 100% of Azure runs with
no error. ``AzureOpenAIRunner.__init__`` therefore raises
:class:`~agentevalops.core.errors.ConfigurationError` at **construction
time** if the resolved model id is not in :data:`_AZURE_PRICE_PER_1M_USD` —
mirroring how ``config/loader.py`` already fails at load time when a
model-backed config omits ``model``.

Credentials: API key only, endpoint + key from the environment
-------------------------------------------------------------------
``AZURE_OPENAI_ENDPOINT`` and ``AZURE_OPENAI_API_KEY``, read the same way
``OPENAI_API_KEY`` already is in ``OpenAIRunner._build_real_client``. This is
**not** a reintroduction of the ``OPENAI_MODEL`` env-var pattern
``docs/adr/0001`` §3 removed — that pattern was rejected because it let a
*behaviour-determining* value (which model runs) resolve from two ambiguous
sources. Endpoint and key select *which account you are billed against*, not
*what the model does* — every behaviour-determining value here (model id,
deployment, api_version) lives in config (``RunConfig.model``/``.deployment``/
``.api_version``), per ``docs/adr/0004``, D4/D5.

``DefaultAzureCredential`` / AAD / managed-identity authentication is out of
scope (``docs/adr/0004``, D6) — it would need the separate ``azure-identity``
package, which the existing "openai confined to agents/" import-linter
contract does not cover, and a new contract/mypy override to match.

Known gap, recorded not solved here
-------------------------------------
The Azure endpoint is **not** captured anywhere in the result bundle — only
``model``, ``deployment``, and ``api_version`` are (``RunConfig`` fields, so
``config.json`` records them by construction). A bundle produced by this
runner therefore does not fully identify *which* Azure resource produced it,
only which deployment/model/api-version it targeted. This is an open input
for W12b's replay capture (``replay_command.txt``), not solved here.

Import safety / optional dependency pattern
-------------------------------------------
Same discipline as ``openai_runner.py``: ``openai`` is imported lazily
inside :meth:`_build_real_client`, never at module top level, so the
registry can reference this class (and register it) without importing
``openai``. Unit tests inject a fake client via ``agent_factory``; the
lazy-import and real-client-construction branches carry
``# pragma: no cover``.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from agentevalops.agents.openai_runner import OpenAIRunner
from agentevalops.core.errors import ConfigurationError, InfrastructureError
from agentevalops.core.types import AgentId

# ---------------------------------------------------------------------------
# Azure price table — deliberately separate from openai_runner.py's
# _PRICE_PER_1M_USD / _FALLBACK_PRICE_PER_1M_USD. No fallback: see the
# module docstring's "Pricing" section for why an unknown model id must
# raise here instead of silently defaulting.
#
# Source: two independent secondary sources, both reporting identical
# Global Standard deployment rates for gpt-4.1-mini-2025-04-14, checked
# 2026-08-03 (Azure's own pricing page at azure.microsoft.com/en-us/pricing/
# details/cognitive-services/openai-service/ renders its price table via
# client-side JavaScript and could not be scraped directly for the raw
# figures):
#   - https://www.cloudzero.com/blog/azure-openai-pricing/
#     (article dated as updated 2026-06-03): "$0.40" per 1M input tokens,
#     "$1.60" per 1M output tokens, "All prices reflect Global Standard
#     deployment."
#   - https://redresscompliance.com/azure-openai-enterprise-pricing-guide
#     ("current as of July 2026", "Global Standard list, per 1M tokens, July
#     2026" table): input $0.40/1M, output $1.60/1M.
# These figures are identical to OpenAI's own direct-API rate for the same
# model (openai_runner.py's _PRICE_PER_1M_USD) — this is *not* this table
# copying that one; it is two independent secondary sources describing
# Azure's own Global Standard tier, which happens to price this model at
# parity with OpenAI's direct API. Model availability on Azure (GA, Chat
# Completions API, North Central US / Sweden Central) independently
# confirmed via Microsoft's own model-availability doc: https://
# learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models
# (`gpt-4.1-mini` (2025-04-14) row), fetched 2026-08-03.
#
# CAVEAT 1 -- this table is keyed on model id alone, but Azure's actual rate
# is a function of *deployment type* (Global Standard, Regional Standard,
# Provisioned Throughput, Batch -- each billed differently) *and region*,
# not model id alone. The two secondary sources above report Global Standard
# figures specifically. A Regional Standard, Provisioned, or Batch
# deployment of the SAME model id would silently price at these Global
# Standard rates instead of its own -- this table has no way to tell the two
# apart, because `deployment` (the field that actually varies with
# type/region) is never consulted for pricing, only `model_id` is. D3's
# construction-time enforcement guarantees the *model id* is one this table
# recognizes; it does NOT guarantee the *price* is correct for whichever
# deployment type/region the caller's `deployment` name actually points at.
# Tightening this (e.g. keying on model id + deployment type + region)
# is real, unstarted future work, not implied by anything built here.
#
# CAVEAT 2 -- this sourcing is weaker than openai_runner.py's
# _PRICE_PER_1M_USD, which cites OpenAI's own pricing page
# (developers.openai.com/api/docs/pricing) directly. Azure's own pricing
# page (azure.microsoft.com/en-us/pricing/details/azure-openai/) renders its
# table via client-side JavaScript and could not be scraped for the raw
# figures, so the numbers above rest on two secondary (non-Microsoft)
# sources -- cloudzero.com and redresscompliance.com, both checked
# 2026-08-03 -- not Microsoft's own pricing page. Treat this table as
# lower-confidence than the OpenAI one until it can be cross-checked against
# Microsoft's own rendered numbers directly.
#
# Divided by 1e6 in _compute_step_cost below to match the per-single-token
# convention openai_runner.py's tables use — do NOT drop a per-1M figure in
# directly, that is a 10**6 error.
_AZURE_PRICE_PER_1M_USD: dict[str, tuple[float, float]] = {
    # model id                     (input $/1M, output $/1M)
    "gpt-4.1-mini-2025-04-14": (0.40, 1.60),
}


class AzureOpenAIRunner(OpenAIRunner):
    """``OpenAIRunner`` variant that calls Azure OpenAI instead of OpenAI
    directly. See the module docstring for the full design rationale.

    Parameters
    ----------
    model:
        OpenAI model id (e.g. ``"gpt-4.1-mini-2025-04-14"``) — used for
        pricing and ``agent_id`` provenance, NOT sent as the API's ``model=``
        parameter (that's ``deployment``, below). Must be a member of
        :data:`_AZURE_PRICE_PER_1M_USD` or construction raises
        :class:`ConfigurationError`.
    deployment:
        The Azure resource's deployment name. Required — Azure OpenAI keys
        requests by deployment name, not model id.
    api_version:
        The Azure OpenAI API version (e.g. ``"2024-10-21"``). Required —
        Azure OpenAI has no OpenAI-API equivalent default.
    agent_factory, max_steps, system_prompt:
        Same meaning as :class:`OpenAIRunner`.

    Raises
    ------
    ConfigurationError
        At construction time, if ``deployment``/``api_version`` are missing
        or blank, or if the resolved model id has no entry in
        :data:`_AZURE_PRICE_PER_1M_USD`.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        deployment: str | None = None,
        api_version: str | None = None,
        agent_factory: Callable[[], Any] | None = None,
        max_steps: int = 6,
        system_prompt: str | None = None,
    ) -> None:
        super().__init__(
            model=model,
            agent_factory=agent_factory,
            max_steps=max_steps,
            system_prompt=system_prompt,
        )
        if not deployment:
            raise ConfigurationError(
                "AzureOpenAIRunner requires a 'deployment' name (the Azure "
                "resource's deployment, not a model id) -- Azure OpenAI "
                "keys requests by deployment name, not model id "
                "(docs/adr/0004-cloud-backend-two-axes.md, Axis B)."
            )
        if not api_version:
            raise ConfigurationError(
                "AzureOpenAIRunner requires an 'api_version' -- Azure "
                "OpenAI has no OpenAI-API equivalent default "
                "(docs/adr/0004-cloud-backend-two-axes.md, Axis B)."
            )
        model_id = self._model_id
        if model_id not in _AZURE_PRICE_PER_1M_USD:
            raise ConfigurationError(
                f"Unknown Azure OpenAI model id '{model_id}': not present "
                "in this runner's Azure price table "
                "(_AZURE_PRICE_PER_1M_USD in azure_openai_runner.py). "
                "Add a citable Azure price for this model before using it, "
                "or pass a model id that is already priced. Refusing to "
                "guess or silently reuse OpenAI's direct-API rate "
                "(docs/adr/0004, D3) -- an unknown model id here would "
                "under-report cost on every run, not just occasionally."
            )
        self._deployment = deployment
        self._api_version = api_version
        # The API call must target the deployment, not the model id.
        self._api_model = deployment
        # Distinguishable prefix so bundles from the two runners are never
        # confused with each other (docs/adr/0004-cloud-backend-two-axes.md,
        # implementation checklist item 2).
        self.agent_id = AgentId(f"azure-openai-chat:{model_id}")

    # ------------------------------------------------------------------
    # Client construction — the seam that differs from OpenAIRunner
    # ------------------------------------------------------------------

    def _build_real_client(self) -> Any:  # pragma: no cover - live path only
        """Construct the real ``openai.AzureOpenAI`` client (lazy import).

        Raises
        ------
        ConfigurationError
            If ``AZURE_OPENAI_ENDPOINT`` or ``AZURE_OPENAI_API_KEY`` is not
            set.
        InfrastructureError
            If the ``openai`` SDK is not installed or the client cannot be
            constructed.
        """
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise ConfigurationError(
                "AZURE_OPENAI_ENDPOINT is not set; the azure-openai runner "
                "needs the Azure OpenAI resource endpoint URL. Set the "
                "environment variable or inject an agent_factory."
            )
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        if not api_key:
            raise ConfigurationError(
                "AZURE_OPENAI_API_KEY is not set; the azure-openai runner "
                "needs an Azure OpenAI API key. Set the environment "
                "variable or inject an agent_factory. API-key "
                "authentication only -- DefaultAzureCredential/AAD is out "
                "of scope (docs/adr/0004, D6)."
            )
        try:
            import openai  # lazy: only present with the 'llm' extra
        except ImportError as exc:
            raise InfrastructureError(
                "The 'openai' package is required for the azure-openai "
                "runner. Install it with: pip install 'agentevalops[llm]'"
            ) from exc
        try:
            return openai.AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=self._api_version,
            )
        except Exception as exc:  # noqa: BLE001 - surface as infra failure
            raise InfrastructureError(
                f"Failed to construct the Azure OpenAI client: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Price resolution — the seam that differs from OpenAIRunner
    # ------------------------------------------------------------------

    def _compute_step_cost(
        self, model_id: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Return the USD cost of one model response, priced from Azure's
        own table.

        ``model_id`` is guaranteed to be a member of
        :data:`_AZURE_PRICE_PER_1M_USD` here -- validated once, at
        construction time, in ``__init__`` (docs/adr/0004, D3). No
        fallback: an unpriced model id is a construction-time
        :class:`ConfigurationError`, never a per-call silent default.
        """
        in_1m, out_1m = _AZURE_PRICE_PER_1M_USD[model_id]
        return (
            input_tokens * (in_1m / 1_000_000.0)
            + output_tokens * (out_1m / 1_000_000.0)
        )

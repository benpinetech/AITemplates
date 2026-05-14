"""v2 engine layer — validator, LLM fallback, audience classifier,
prelude generator, scoped suggestion store.

Public entry points:

    from v2.engine.validator import Validator, ValidationIssue
    from v2.engine.llm_fallback import LlmFallback, FallbackRequest
    from v2.engine.audience import classify_document_audience
    from v2.engine.prelude import generate_prelude, prepend_prelude_to_rtf
    from v2.engine.suggestion_store import (
        accept_suggestion, reject_suggestion, load_verified_for_org,
    )

See README.md in this directory for the architecture and what each
module is responsible for.
"""

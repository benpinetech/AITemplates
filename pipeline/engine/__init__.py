"""v2 engine layer — validator, LLM fallback, audience classifier,
prelude generator, scoped suggestion store.

Public entry points:

    from pipeline.engine.validator import Validator, ValidationIssue
    from pipeline.engine.llm_converter import LlmConverter, ConversionRequest
    from pipeline.engine.audience import classify_document_audience
    from pipeline.engine.prelude import generate_prelude, prepend_prelude_to_rtf
    from pipeline.engine.suggestion_store import (
        accept_suggestion, reject_suggestion, load_verified_for_org,
    )

See README.md in this directory for the architecture and what each
module is responsible for.
"""

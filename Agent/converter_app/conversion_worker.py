"""QThread that runs ``v2.pipeline.convert_template`` off the UI thread.

The conversion itself is fast for small RTFs but the LLM fallback can
take seconds-to-tens-of-seconds per unmatched token. Running it in
the GUI thread would freeze the window. The worker emits signals when
the conversion finishes (or fails) so the UI can update.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from .secrets_redact import scrub


class ConversionWorker(QObject):
    """Lives on a QThread. Owns one conversion run."""

    # Result payload is the full ConversionResult (passed as object
    # because Qt doesn't have a native shape for it).
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        rtf: str,
        org: str,
        api_key: Optional[str],
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        super().__init__()
        self._rtf = rtf
        self._org = org
        self._api_key = api_key
        self._model = model
        self._base_url = base_url

    def run(self) -> None:
        try:
            self.progress.emit("Loading library…")
            # Defer the v2 imports until inside the thread so app launch
            # doesn't block on langchain etc. (~100s of ms first time).
            from v2 import pipeline
            from v2.engine.llm_fallback import LlmFallback, OpenAILlmClient
            from v2.grammar.loaders import load_org_overrides
            from v2.patterns import loader as pattern_loader

            library = pattern_loader.load_library().patterns
            org_overrides = (
                load_org_overrides(self._org) if self._org != "any" else None
            )

            fb = None
            if self._api_key:
                self.progress.emit("Initialising OpenAI client…")
                # Pass the key directly to the SDK — never write it to
                # os.environ. Env-var pollution can leak into crash
                # reports / subprocess inspection / logging output.
                try:
                    client = OpenAILlmClient(
                        api_key=self._api_key,
                        model=self._model,
                        base_url=self._base_url,
                    )
                except RuntimeError as e:
                    self.failed.emit(scrub(f"OpenAI client failed to start: {e}"))
                    return
                fb = LlmFallback(
                    client=client, library=library, org_overrides=org_overrides,
                )

            self.progress.emit("Converting…")
            result = pipeline.convert_template(
                self._rtf,
                org=self._org,
                library=library,
                org_overrides=org_overrides,
                llm_fallback=fb,
            )
            self.finished.emit(result)
        except Exception as e:  # noqa: BLE001 — surface every failure in the UI
            import traceback
            # ``scrub`` redacts any secret-shaped substring before the
            # message reaches the UI / log. Defends against the SDK
            # echoing the auth header into an HTTP error.
            self.failed.emit(
                scrub(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
            )


def run_conversion_async(
    rtf: str,
    org: str,
    api_key: Optional[str],
    on_finished,
    on_failed,
    on_progress=None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
):
    """Convenience: build a thread, hook up signals, start it.

    Returns the (thread, worker) pair so the caller can hold references
    until the work completes (Qt deletes them otherwise — silent crash
    territory).
    """
    thread = QThread()
    worker = ConversionWorker(
        rtf, org, api_key, model=model, base_url=base_url,
    )
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    if on_progress is not None:
        worker.progress.connect(on_progress)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    return thread, worker

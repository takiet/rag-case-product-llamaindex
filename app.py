"""Chainlit UI entrypoint — the only module that imports chainlit."""

import chainlit as cl
from llama_index.core import Settings
from llama_index.core.callbacks import CallbackManager, CBEventType

from rag_case_products.workflow.events import ProgressEvent
from rag_case_products.workflow.rag_workflow import RagWorkflow


class _CallbackHandler(cl.LlamaIndexCallbackHandler):
    """LlamaIndex callback handler that suppresses noisy 'Used LLM' steps."""

    def on_event_start(self, event_type, payload=None, event_id="", parent_id="", **kwargs):
        if event_type == CBEventType.LLM:
            return event_id
        return super().on_event_start(event_type, payload, event_id, parent_id, **kwargs)

    def on_event_end(self, event_type, payload=None, event_id="", **kwargs):
        if event_type == CBEventType.LLM:
            return
        return super().on_event_end(event_type, payload, event_id, **kwargs)


_STEP_LABELS: dict[str, str] = {
    "analyze_query": "Analyse query",
    "run_agent": "Retrieve & reason",
    "synthesize": "Synthesise answer",
}


@cl.on_chat_start
async def on_chat_start() -> None:
    # Register the LlamaIndex callback handler before building the workflow:
    # build_tools() constructs the retrievers and query engines, which snapshot
    # Settings.callback_manager at creation time. Registering after construction
    # would leave retrieval and LLM sub-steps invisible in the UI.
    Settings.callback_manager = CallbackManager([_CallbackHandler()])
    workflow = RagWorkflow(timeout=120)
    cl.user_session.set("workflow", workflow)
    await cl.Message(
        content=(
            "Hello! I can help you search Axis Communications **products** and "
            "**deployment case studies**.\n\n"
            "Try asking:\n"
            "- *Is there any case where axis products are used in sports stadium?*\n"
            "- *Tell me the details optical specs about Q3556-LVE*\n"
            "- *Please recommend cameras that can operate in environments up to 70°C*\n"
            "- *Which M20 or P14 camera is suitable for traffic monitoring?*\n"
            "- *Please recommend cameras meeting the requirements in manufacture factories"
            " for accident prevention*"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    workflow: RagWorkflow = cl.user_session.get("workflow")

    handler = workflow.run(query=message.content)

    # One open cl.Step per workflow phase: close the previous before opening the
    # next so the callback handler's auto steps nest under the active phase.
    step: cl.Step | None = None
    step_key: str | None = None
    async for event in handler.stream_events():
        if not isinstance(event, ProgressEvent):
            continue
        if event.step != step_key:
            if step is not None:
                await step.__aexit__(None, None, None)
            step = cl.Step(name=_STEP_LABELS.get(event.step, event.step))
            await step.__aenter__()
            step_key = event.step
        step.output = event.detail
    if step is not None:
        await step.__aexit__(None, None, None)

    bundle = await handler

    answer_content = bundle.answer_markdown
    if bundle.source_items:
        lines = ["---", "#### References"]
        for i, item in enumerate(bundle.source_items, 1):
            source_md = f"[link]({item.source})" if item.source else ""
            lines.append(
                f"{i}. **{item.title}** ({item.doc_type.value}) — {item.reason}"
                + (f" {source_md}" if source_md else "")
            )
        answer_content = answer_content.rstrip("\n") + "\n\n" + "\n".join(lines)

    await cl.Message(content=answer_content).send()

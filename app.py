"""Chainlit UI entrypoint — the only module that imports chainlit."""

import chainlit as cl
from llama_index.core import Settings
from llama_index.core.callbacks import CallbackManager

from rag_case_products.workflow.events import ProgressEvent
from rag_case_products.workflow.rag_workflow import RagWorkflow

_STEP_LABELS: dict[str, str] = {
    "analyze_query": "Analyse query",
    "run_agent": "Retrieve & reason",
    "synthesize": "Synthesise answer",
}


@cl.on_chat_start
async def on_chat_start() -> None:
    # Register the LlamaIndex callback handler before building the workflow:
    # as_query_engine() snapshots Settings.callback_manager when RagWorkflow.__init__
    # builds the tools, so retrieval and LLM steps surface in the UI.
    Settings.callback_manager = CallbackManager([cl.LlamaIndexCallbackHandler()])
    workflow = RagWorkflow(timeout=120)
    cl.user_session.set("workflow", workflow)
    await cl.Message(
        content=(
            "Hello! I can help you search Axis Communications **products** and "
            "**deployment case studies**.\n\n"
            "Try asking:\n"
            "- Pattern A: *Show retail case studies related to checkout monitoring.*\n"
            "- Pattern B: *Which cameras support IP66 and IP67?*\n"
            "- Pattern C: *What operating temperatures are common for cameras used in factories?*"
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

    # Build citation elements from the structured list — never parse answer_markdown.
    citation_elements = [
        cl.Text(
            name=f"citation-{i}",
            content=f"**{c.title}**\n{c.source}",
            display="inline",
        )
        for i, c in enumerate(bundle.citations)
    ]

    await cl.Message(
        content=bundle.answer_markdown,
        elements=citation_elements,
    ).send()

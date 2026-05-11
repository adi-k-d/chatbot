import time

import google.generativeai as genai

from app.agent.prompts import build_system_prompt
from app.agent.tools import TOOL_DEFINITIONS, execute_tool
from app.config import settings
from app.models import BusinessConfig
from app.observability.logging import get_logger
from app.observability.metrics import LLM_LATENCY, TOOL_CALLS

log = get_logger(__name__)

_configured = False


def _get_model(system_prompt: str) -> genai.GenerativeModel:
    global _configured
    if not _configured:
        genai.configure(api_key=settings.gemini_api_key)
        _configured = True
    return genai.GenerativeModel(
        model_name=settings.gemini_model,
        tools=TOOL_DEFINITIONS,
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(max_output_tokens=settings.agent_max_tokens),
    )


def _build_history(history: list[dict]) -> list[dict]:
    """
    Convert DB message rows to Gemini chat history format.
    Gemini roles: "user" | "model". Consecutive same-role messages are merged.
    """
    gemini_history = []
    for msg in history:
        role = "user" if msg["direction"] == "inbound" else "model"
        body = (msg.get("message_body") or "").strip()
        if not body:
            continue
        if gemini_history and gemini_history[-1]["role"] == role:
            gemini_history[-1]["parts"][0] += f"\n{body}"
        else:
            gemini_history.append({"role": role, "parts": [body]})
    return gemini_history


async def run_agent(
    *,
    user_message: str,
    history: list[dict],
    config: BusinessConfig,
    context: dict,
) -> str | None:
    system_prompt = build_system_prompt(config)
    model = _get_model(system_prompt)
    gemini_history = _build_history(history)
    bound_log = log.bind(business_id=config.id, slug=config.slug)

    chat = model.start_chat(history=gemini_history)

    current_message: str | list = user_message

    for round_num in range(settings.agent_max_rounds):
        t0 = time.perf_counter()
        try:
            response = await chat.send_message_async(current_message)
        finally:
            LLM_LATENCY.observe(time.perf_counter() - t0)

        # Collect any function calls in this response
        function_calls = [
            part.function_call
            for part in response.parts
            if hasattr(part, "function_call") and part.function_call.name
        ]

        bound_log.info(
            "gemini_round",
            round=round_num,
            has_function_calls=bool(function_calls),
            num_parts=len(response.parts),
        )

        if not function_calls:
            return response.text or None

        # Execute all tool calls and collect responses
        tool_response_parts = []
        for fc in function_calls:
            tool_input = dict(fc.args)
            bound_log.info("tool_call", tool=fc.name, input=tool_input)
            t_tool = time.perf_counter()
            result = await execute_tool(fc.name, tool_input, context)
            elapsed = time.perf_counter() - t_tool
            TOOL_CALLS.labels(tool_name=fc.name, outcome="success").inc()
            bound_log.info("tool_result", tool=fc.name, ms=round(elapsed * 1000), preview=result[:120])
            tool_response_parts.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=fc.name,
                        response={"result": result},
                    )
                )
            )

        current_message = tool_response_parts

    bound_log.warning("max_rounds_reached", rounds=settings.agent_max_rounds)
    return None

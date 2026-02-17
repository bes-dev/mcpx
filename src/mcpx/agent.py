import json
from dataclasses import dataclass
from typing import Any, Callable

from litellm import completion


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    execute: Callable[[dict[str, Any]], str] | None = None


def _to_openai_tools(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def agent_loop(
    model: str,
    system_prompt: str,
    user_message: str,
    tools: list[ToolDef],
    terminal_tool: str,
    max_steps: int = 10,
    on_step: Callable[[int, str, dict], None] | None = None,
    on_text: Callable[[str], None] | None = None,
) -> dict | None:
    executors = {t.name: t.execute for t in tools}
    openai_tools = _to_openai_tools(tools)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    step = 0
    while step < max_steps:
        resp = completion(model=model, messages=messages, tools=openai_tools, temperature=0)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            if msg.content and on_text:
                on_text(msg.content)
            return None
        messages.append(msg)
        for tc in msg.tool_calls:
            step += 1
            if step > max_steps:
                break
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError as e:
                if on_step:
                    on_step(step, name, {})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"Invalid JSON arguments: {e}"})
                continue
            if on_step:
                on_step(step, name, args)
            if name == terminal_tool:
                return args
            executor = executors.get(name)
            if executor is None:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"Unknown tool: {name}"})
                continue
            try:
                result = executor(args)
            except Exception as e:
                result = f"Error executing {name}: {e}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    if on_text:
        on_text("Agent reached maximum steps without a result.")
    return None

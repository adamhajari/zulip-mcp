import asyncio
import json
import os
import re
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

ZULIP_MCP_DIR = os.environ.get("ZULIP_MCP_DIR", os.path.dirname(os.path.abspath(__file__)))
MAX_TOOL_RESULT_CHARS = 100_000
LOCAL_OLLAMA_URL = "http://localhost:11434/v1"

_open_webui_key = os.environ.get("OPEN_WEBUI_API_KEY")
_open_webui_url = os.environ.get("OPEN_WEBUI_BASE_URL")
use_open_webui = _open_webui_key and _open_webui_url
base_url = _open_webui_url if use_open_webui else LOCAL_OLLAMA_URL
api_key = _open_webui_key if use_open_webui else "ollama"

client = OpenAI(base_url=base_url, api_key=api_key, timeout=300)


def _pending_checkins_topic(messages: list) -> str | None:
    """Return the next_topic from the most recent get_checkins tool result, if any."""
    for msg in reversed(messages):
        if msg.get("role") == "tool":
            m = re.search(r"\*\*next_topic: (.+?)\*\*", msg.get("content", ""))
            return m.group(1).strip() if m else None
    return None

DEFAULT_MODEL = os.environ.get("ZULIP_MCP_MODEL", "gemma4:e4b")

async def chat(prompt: str, model: str = DEFAULT_MODEL, verbose: bool = False):
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "zulip-mcp"],
        cwd=ZULIP_MCP_DIR,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()

            tools = [{
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.inputSchema,
                }
            } for t in tools_result.tools]

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant summarizing Zulip activity. "
                        "When asked to summarize every person, you MUST include every single person "
                        "present in the data — do not skip, omit, or group anyone. "
                        "Go through the data systematically from top to bottom.\n\n"
                        "IMPORTANT output rules:\n"
                        "- Do NOT announce tool calls, narrate what you are about to do, "
                        "or add placeholder text while waiting for data "
                        "(e.g. never write 'Now I will fetch...', 'Let me get...', "
                        "'This summary is pending...', 'Waiting for...', etc.).\n"
                        "- Write each person's summary as you receive their data. "
                        "Do NOT write a consolidated re-summary at the end — "
                        "the per-person summaries you write along the way are the final answer.\n"
                        "- Format each entry as '**Full Name** <summary>' with a blank line "
                        "between each person. Never run multiple people together in one paragraph."
                    ),
                },
                {"role": "user", "content": prompt},
            ]

            while True:
                if verbose:
                    print(f"\n[sending {len(messages)} messages to {model}]")
                    for m in messages:
                        role = m["role"] if isinstance(m, dict) else m.role
                        content = (m["content"] if isinstance(m, dict) else m.content) or ""
                        print(f"  [{role}]: {content}")

                stream = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    stream=True,
                )

                # Accumulate streamed response
                content = ""
                tool_calls = {}
                finish_reason = None

                for chunk in stream:
                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason or finish_reason

                    if delta.content:
                        print(delta.content, end="", flush=True)
                        content += delta.content

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            i = tc.index
                            if i not in tool_calls:
                                tool_calls[i] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                            if tc.id:
                                tool_calls[i]["id"] = tc.id
                            if tc.function.name:
                                tool_calls[i]["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_calls[i]["function"]["arguments"] += tc.function.arguments

                if content:
                    print()  # newline after streamed output
                    if verbose:
                        print(f"\n[assistant response ({len(content)} chars)]")

                tool_calls_list = [tool_calls[i] for i in sorted(tool_calls)]

                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls_list,
                })

                # After the LLM responds, check if a get_checkins result has a pending
                # next_topic that the LLM didn't follow up on. If so, inject the next
                # call automatically rather than trusting small models to continue.
                if not tool_calls_list:
                    pending_next = _pending_checkins_topic(messages)
                    if pending_next is None:
                        break
                    # Inject a synthetic assistant + tool turn and continue the loop
                    synthetic_id = f"auto_{pending_next[:24].replace(' ', '_')}"
                    next_args = {"topic": pending_next}
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": synthetic_id, "type": "function",
                                        "function": {"name": "get_checkins",
                                                     "arguments": json.dumps(next_args)}}],
                    })
                    if verbose:
                        print(f"[auto-fetching next checkins topic: '{pending_next}']")
                    result = await session.call_tool("get_checkins", next_args)
                    tool_content = result.content[0].text if result.content else ""
                    if len(tool_content) > MAX_TOOL_RESULT_CHARS:
                        tool_content = tool_content[:MAX_TOOL_RESULT_CHARS] + "\n\n[truncated]"
                    if verbose:
                        print(f"[tool result ({len(tool_content)} chars): {tool_content}]")
                    messages.append({"role": "tool", "tool_call_id": synthetic_id, "content": tool_content})
                    continue

                for tc in tool_calls_list:
                    args = json.loads(tc["function"]["arguments"])
                    name = tc["function"]["name"]
                    if verbose:
                        print(f"[calling {name}({args})]")
                    result = await session.call_tool(name, args)
                    tool_content = result.content[0].text if result.content else ""
                    if len(tool_content) > MAX_TOOL_RESULT_CHARS:
                        if verbose:
                            print(f"[WARNING: tool result truncated from {len(tool_content)} to {MAX_TOOL_RESULT_CHARS} chars]")
                        tool_content = tool_content[:MAX_TOOL_RESULT_CHARS] + "\n\n[truncated]"
                    if verbose:
                        print(f"[tool result ({len(tool_content)} chars): {tool_content}]")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_content,
                    })

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    verbose = "--verbose" in args
    if verbose:
        args.remove("--verbose")
    model = DEFAULT_MODEL
    if "--model" in args:
        i = args.index("--model")
        model = args[i + 1]
        args = args[:i] + args[i + 2:]
    prompt = " ".join(args) or "Give me a digest of recent Zulip activity"
    asyncio.run(chat(prompt, model=model, verbose=verbose))

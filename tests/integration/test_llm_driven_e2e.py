"""LLM-driven E2E — Claude actually uses the MCP tools to accomplish a goal.

Opt-in: requires ``ANTHROPIC_API_KEY`` in the environment. Skips cleanly when
missing so the rest of the suite stays runnable offline.

The harness bridges our MCP tool list to the Anthropic Messages API tool_use
format, runs an agentic loop (Claude reads → calls tools → reads results →
…), and asserts on Claude's final natural-language answer. Catches the kind
of failure the deterministic test suite can't:

* tool descriptions ambiguous to the model
* parameter names that the model misuses
* result shapes that the model can't summarise

The scripted-chat scenario from ``demo_chat_search.py`` is replayed first so
Claude has real content to search through.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastmcp import Client

from .conftest import OpenfireHandle
from .helpers.chat_script import ChatScript, Line
from .helpers.raw_client import RawXMPPClient

pytestmark = [pytest.mark.docker, pytest.mark.llm]

MODEL = "claude-sonnet-4-6"
MAX_AGENTIC_TURNS = 12
PER_REQUEST_MAX_TOKENS = 4096


_SCRIPT = [
    Line("alice", "R_ENG",   "Ready to deploy v2.1 to staging in 10 minutes"),
    Line("bob",   "R_ENG",   "Hold on — I see a flaky test in the auth suite"),
    Line("carol", "R_ENG",   "Pushing the auth fix now, should unblock the deploy"),
    Line("alice", "R_LUNCH", "Anyone want sushi today?"),
    Line("bob",   "R_LUNCH", "I'm thinking pizza"),
    Line("carol", "R_HELP",  "Customer is hitting a 500 on /api/orders"),
    Line("bob",   "R_HELP",  "Stack trace looks like the same auth bug we just fixed"),
]


def _require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set — skipping LLM-driven tests")
    return key


async def _seed_chat(
    mcp: Client,
    raws: dict[str, RawXMPPClient],
    openfire: OpenfireHandle,
) -> dict[str, str]:
    """Replay the demo conversation so Claude has data to search through."""
    rooms = {
        "engineering": openfire.room_jid("r1"),
        "lunch": openfire.room_jid("r2"),
        "help": openfire.room_jid("r3"),
    }
    for r in rooms.values():
        await mcp.call_tool("join_room", {"room_jid": r})

    script = ChatScript(raws, muc_service=openfire.muc_service)
    await script.join_all(rooms["engineering"], ["alice", "bob", "carol"])
    await script.join_all(rooms["lunch"], ["alice", "bob"])
    await script.join_all(rooms["help"], ["bob", "carol"])
    await asyncio.sleep(0.3)

    resolve = {
        "R_ENG":   rooms["engineering"],
        "R_LUNCH": rooms["lunch"],
        "R_HELP":  rooms["help"],
    }
    await script.run(
        [Line(line.speaker, resolve[line.target], line.body) for line in _SCRIPT]
    )
    await asyncio.sleep(0.8)
    return rooms


async def _tools_for_anthropic(mcp: Client) -> list[dict]:
    tools = await mcp.list_tools()
    return [
        {
            "name": t.name,
            "description": (t.description or "").strip(),
            "input_schema": t.inputSchema,
        }
        for t in tools
    ]


async def _run_agentic_loop(
    api_key: str,
    mcp: Client,
    user_message: str,
) -> tuple[str, list[str]]:
    """Run a Claude tool-use loop. Returns (final_text, names_of_tools_called)."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    tools = await _tools_for_anthropic(mcp)

    messages: list[dict] = [{"role": "user", "content": user_message}]
    tools_called: list[str] = []

    for _turn in range(MAX_AGENTIC_TURNS):
        response = await client.messages.create(
            model=MODEL,
            max_tokens=PER_REQUEST_MAX_TOKENS,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text_parts = [
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            ]
            return "\n".join(text_parts), tools_called

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tools_called.append(block.name)
            try:
                result = await mcp.call_tool(block.name, dict(block.input))
                payload = json.dumps(result.data, default=str)
                is_error = False
            except Exception as exc:  # noqa: BLE001 — surface to model
                payload = f"Tool error: {exc}"
                is_error = True
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": payload,
                    "is_error": is_error,
                }
            )
        if not tool_results:
            break
        messages.append({"role": "user", "content": tool_results})

    raise AssertionError(
        f"agent did not finish in {MAX_AGENTIC_TURNS} turns; "
        f"last stop_reason={response.stop_reason}"
    )


async def test_claude_answers_who_is_talking_about_auth(
    mcp: Client,
    raw_alice: RawXMPPClient,
    raw_bob: RawXMPPClient,
    raw_carol: RawXMPPClient,
    openfire: OpenfireHandle,
) -> None:
    """Real Claude session must figure out that Bob and Carol mentioned auth."""
    api_key = _require_api_key()
    await _seed_chat(
        mcp, {"alice": raw_alice, "bob": raw_bob, "carol": raw_carol}, openfire
    )

    answer, tools_called = await _run_agentic_loop(
        api_key,
        mcp,
        "I'm joined to three rooms (r1, r2, r3). Use the tools available "
        "to find out who's been talking about 'auth' across them. Give me "
        "a short answer naming the participants and the rooms.",
    )

    lower = answer.lower()
    # Bob and Carol both mention auth — Alice does not. We require both
    # names; otherwise the model didn't find the messages.
    assert "bob" in lower, f"answer didn't mention bob: {answer!r}"
    assert "carol" in lower, f"answer didn't mention carol: {answer!r}"
    # And the search tool is the obvious right one to reach for.
    assert "search_messages" in tools_called, (
        f"model didn't use search_messages; called: {tools_called}"
    )

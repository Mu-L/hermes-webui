"""Regression coverage for #6220 historical tool transcript ownership.

Only an unambiguous ``assistant.tool_calls[].id`` ->
``role=tool.tool_call_id`` turn may move from the legacy settled renderer into
an Assistant Turn Anchor scene. Ambiguous or richer historical shapes stay on
the compatibility path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
ANCHOR_JS = ROOT / "static" / "assistant_turn_anchors.js"
NODE = shutil.which("node")


_EXTRACT_FUNC_JS = r"""
function extractFunc(name){
  const start = src.indexOf('function ' + name);
  if(start === -1) throw new Error(name + ' not found');
  const params = src.indexOf('(', start);
  let depth = 0, close = -1;
  for(let i=params; i<src.length; i++){
    if(src[i] === '(') depth++;
    else if(src[i] === ')'){
      depth--;
      if(depth === 0){ close = i; break; }
    }
  }
  const brace = src.indexOf('{', close);
  depth = 0;
  for(let i=brace; i<src.length; i++){
    if(src[i] === '{') depth++;
    else if(src[i] === '}'){
      depth--;
      if(depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(name + ' body did not close');
}
""".strip()


def _run_hydration(messages: list[dict], *, repeat: bool = False, throw_helper: bool = False) -> dict:
    assert NODE, "node is required for Anchor hydration behavior tests"
    script = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))}, 'utf8');
{_EXTRACT_FUNC_JS}

const sandbox = {{console}};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync({json.dumps(str(ANCHOR_JS))}, 'utf8'),
  sandbox,
  {{filename:'assistant_turn_anchors.js'}}
);
sandbox._redactToolTargetLabel = value => String(value);

for(const name of [
  '_clipCliToolSnippet',
  '_cliToolResultText',
  '_cliLooksLikePatchDiff',
  '_cliToolResultSnippet',
  '_prefixedCliDiffLines',
  '_firstOwnedValue',
  '_cliPatchSnippetFromArgs',
  '_cliToolCardSnippet',
  '_toolArgsSnapshot',
  '_assistantReasoningPayloadText',
  '_idLinkedHistoricalMessageText',
  '_idLinkedHistoricalMessageRef',
  '_idLinkedHistoricalToolArguments',
  '_idLinkedHistoricalToolResultRaw',
  '_idLinkedHistoricalTurnScene',
  '_hydrateIdLinkedHistoricalToolScenes',
]){{
  vm.runInContext(extractFunc(name), sandbox, {{filename:'ui.js:' + name}});
}}

if({str(throw_helper).lower()}){{
  vm.runInContext(
    "_toolArgsSnapshot = function(){{ throw new Error('synthetic event build failure'); }}",
    sandbox
  );
}}

const messages = {json.dumps(messages)};
sandbox.messages = messages;
const first = vm.runInContext(
  "_hydrateIdLinkedHistoricalToolScenes(messages, {{sessionId:'sid-6220', mode:'compact_worklog'}})",
  sandbox
);
const afterFirst = JSON.stringify(messages);
const second = {str(repeat).lower()}
  ? vm.runInContext(
      "_hydrateIdLinkedHistoricalToolScenes(messages, {{sessionId:'sid-6220', mode:'compact_worklog'}})",
      sandbox
    )
  : null;
const owners = messages
  .map((message, index) => message && message._anchor_activity_scene ? {{index, scene:message._anchor_activity_scene}} : null)
  .filter(Boolean);
console.log(JSON.stringify({{first, second, unchanged:afterFirst===JSON.stringify(messages), owners}}));
"""
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _linked_turn(call_id: str = "call-1") -> list[dict]:
    return [
        {"role": "user", "content": "Inspect the workspace"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"pwd"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": '{"output":"/workspace"}',
        },
        {"role": "assistant", "content": "The workspace is `/workspace`."},
    ]


@pytest.mark.skipif(NODE is None, reason="node is required for Anchor hydration tests")
def test_id_linked_historical_tool_turn_hydrates_one_anchor_scene_idempotently():
    data = _run_hydration(_linked_turn(), repeat=True)

    assert data["first"] == 1
    assert data["second"] == 0
    assert data["unchanged"] is True
    assert [owner["index"] for owner in data["owners"]] == [3]

    scene = data["owners"][0]["scene"]
    assert scene["version"] == "activity_scene_v1"
    assert scene["final_answer"] == "The workspace is `/workspace`."
    assert scene["final_message_ref"] == "raw_idx:3"
    assert scene["identity"]["session_id"] == "sid-6220"
    assert scene["identity"]["source_message_refs"] == ["raw_idx:1", "raw_idx:3"]
    assert len(scene["activity_rows"]) == 1

    row = scene["activity_rows"][0]
    assert row["role"] == "tool"
    assert row["source_event_type"] == "tool_complete"
    assert row["tool_call_id"] == "call-1"
    assert row["tool"]["name"] == "terminal"
    assert row["tool"]["args"] == {"command": "pwd"}
    assert row["tool"]["snippet"] == "/workspace"
    assert row["tool"]["done"] is True


@pytest.mark.skipif(NODE is None, reason="node is required for Anchor hydration tests")
def test_id_linked_historical_multi_tool_turn_preserves_declaration_order():
    messages = [
        {"role": "user", "content": "Inspect both files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-first",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"pwd"}',
                    },
                },
                {
                    "id": "call-second",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-second", "content": "readme body"},
        {"role": "tool", "tool_call_id": "call-first", "content": "/workspace"},
        {"role": "assistant", "content": "Both checks completed."},
    ]

    data = _run_hydration(messages, repeat=True)

    assert data["first"] == 1
    assert data["second"] == 0
    rows = data["owners"][0]["scene"]["activity_rows"]
    assert [row["tool_call_id"] for row in rows] == ["call-first", "call-second"]
    assert [row["tool"]["name"] for row in rows] == ["terminal", "read_file"]
    assert [row["tool"]["snippet"] for row in rows] == ["/workspace", "readme body"]


@pytest.mark.skipif(NODE is None, reason="node is required for Anchor hydration tests")
@pytest.mark.parametrize(
    "messages",
    [
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-1", "function": {"name": "one", "arguments": "{}"}},
                {"id": "call-1", "function": {"name": "two", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "first"},
            {"role": "tool", "tool_call_id": "call-1", "content": "second"},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": "I will inspect it first.", "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": None, "reasoning": "Need to inspect it.", "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "user", "content": "What happened?"},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "terminal", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"}]},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{not-json"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ],
        [
            {"role": "user", "content": "Run it"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
                "_partial_tool_calls": [{"name": "terminal"}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ],
    ],
)
def test_ambiguous_or_richer_historical_tool_shapes_keep_legacy_ownership(messages):
    data = _run_hydration(messages)

    assert data["first"] == 0
    assert data["owners"] == []


@pytest.mark.skipif(NODE is None, reason="node is required for Anchor hydration tests")
def test_event_building_helper_exceptions_keep_legacy_ownership():
    data = _run_hydration(_linked_turn(), throw_helper=True)

    assert data["first"] == 0
    assert data["owners"] == []


@pytest.mark.skipif(NODE is None, reason="node is required for Anchor hydration tests")
def test_reused_ids_in_adjacent_complete_turns_remain_turn_scoped():
    data = _run_hydration(_linked_turn("call-reused") + _linked_turn("call-reused"))

    assert data["first"] == 2
    assert [owner["index"] for owner in data["owners"]] == [3, 7]
    assert [owner["scene"]["activity_rows"][0]["tool_call_id"] for owner in data["owners"]] == ["call-reused", "call-reused"]
    assert data["owners"][0]["scene"]["identity"]["turn_id"] != data["owners"][1]["scene"]["identity"]["turn_id"]


def test_render_messages_hydrates_before_visibility_and_fallback_ownership_scan():
    start = UI_JS.index("function renderMessages(options)")
    body = UI_JS[start : UI_JS.index("\nfunction ", start + 1)]

    hydrate = "_hydrateIdLinkedHistoricalToolScenes(S.messages"
    visible = "const visWithIdx=_getVisibleMessagesWithIdx();"
    fallback = "const anchorOwnedAssistantRawIdxs=new Set();"

    assert hydrate in body
    assert body.index(hydrate) < body.index(visible) < body.index(fallback)
    assert "if(!S.busy" in body[: body.index(visible)]

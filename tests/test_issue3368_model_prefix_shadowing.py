"""
Regression tests for #3368 — /model <name> prefix-shadowing.

Reporter: `/model mimo-v2.5` selected `mimo-v2.5-pro` because the typed name is a
clean prefix of the longer tier variant, and the only `mimo-v2.5*` entry in the
Nous catalog is the `-pro` one (there is no bare `mimo-v2.5`).

PR #3394 ("Closes #3368") only hardened the `_bestModelMatch` *fallback* in
`commands.js`, but for this input `_findModelInDropdown` (ui.js) returns the
WRONG longer variant via its step-3 prefix match BEFORE the fallback ever runs —
so the fix never executed and the reporter still saw `mimo-v2.5-pro` after
upgrading to v0.51.216.

This fix hardens BOTH matchers so a COMPLETE versioned query (ends in a digit)
never snaps to a `-suffix` tier variant:

* `_findModelInDropdown` step 3 (ui.js): a prefix hit on a longer option is only
  accepted when the extra text continues the VERSION (".`<digit>`"), not a
  variant/tier suffix (".`pro`" from "-pro"). Otherwise returns null.
* `_bestModelMatch` (commands.js): same boundary rule on the substring fallback.

These tests run the live JS via Node so drift is caught behaviourally.

Crucially they also assert the #1188 legitimate-fuzzy behaviour still holds
(`gpt-5` → `gpt-5.4-mini`, `claude` → `claude-opus-4.6`), since the fix touches
the same step-3 path.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
UI_JS_PATH = REPO_ROOT / "static" / "ui.js"
COMMANDS_JS_PATH = REPO_ROOT / "static" / "commands.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


# ── driver for _findModelInDropdown (ui.js) ─────────────────────────────────

_FIND_DRIVER = r"""
const fs = require('fs');
const ui = fs.readFileSync(process.argv[2], 'utf8');
function extractFunc(src, name){
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{', start); let depth = 1; i++;
  while (depth > 0 && i < src.length){ if(src[i]==='{')depth++; else if(src[i]==='}')depth--; i++; }
  return src.slice(start, i);
}
function _getOptionProviderId(opt){
  if(!opt) return '';
  if(opt.dataset && opt.dataset.provider) return opt.dataset.provider;
  const group = opt.parentElement;
  if(group && group.tagName==='OPTGROUP' && group.dataset && group.dataset.provider) return group.dataset.provider;
  const value = String(opt.value||'');
  if(value.startsWith('@') && value.includes(':')) return value.slice(1, value.lastIndexOf(':'));
  return '';
}
eval(extractFunc(ui, '_findModelInDropdown'));
const args = JSON.parse(process.argv[3]);
const sel = { options: args.options.map(v => ({value: v})) };
const got = _findModelInDropdown(args.modelId, sel, args.preferredProvider || undefined);
process.stdout.write(JSON.stringify(got));
"""


# ── driver for _bestModelMatch (commands.js) ────────────────────────────────

_BEST_DRIVER = r"""
const fs = require('fs');
const cmds = fs.readFileSync(process.argv[2], 'utf8');
function extractFunc(src, name){
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{', start); let depth = 1; i++;
  while (depth > 0 && i < src.length){ if(src[i]==='{')depth++; else if(src[i]==='}')depth--; i++; }
  return src.slice(start, i);
}
eval(extractFunc(cmds, '_looksLikeVersionedModel'));
eval(extractFunc(cmds, '_bestModelMatch'));
eval(extractFunc(cmds, '_nearestModelSuggestion'));
const args = JSON.parse(process.argv[3]);
// options: [{value, text}]
const options = args.options.map(o => ({value: o.value, textContent: o.text || o.value}));
const out = {
  best: _bestModelMatch(options, args.query),
  suggestion: _nearestModelSuggestion(options, args.query),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def find_driver(tmp_path_factory):
    p = tmp_path_factory.mktemp("find3368") / "driver.js"
    p.write_text(_FIND_DRIVER, encoding="utf-8")
    return str(p)


@pytest.fixture(scope="module")
def best_driver(tmp_path_factory):
    p = tmp_path_factory.mktemp("best3368") / "driver.js"
    p.write_text(_BEST_DRIVER, encoding="utf-8")
    return str(p)


def _find(driver, model_id, options, preferred=None):
    r = subprocess.run(
        [NODE, driver, str(UI_JS_PATH),
         json.dumps({"modelId": model_id, "options": options, "preferredProvider": preferred})],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError(f"node driver failed: {r.stderr}")
    return json.loads(r.stdout)


def _best(driver, query, options):
    r = subprocess.run(
        [NODE, driver, str(COMMANDS_JS_PATH), json.dumps({"query": query, "options": options})],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError(f"node driver failed: {r.stderr}")
    return json.loads(r.stdout)


# ═══════════════════════════════════════════════════════════════════════════
# _findModelInDropdown — the layer PR #3394 missed (the reporter's actual path)
# ═══════════════════════════════════════════════════════════════════════════


class TestFindModelDropdownNoTierSnap:
    def test_mimo_v25_does_not_snap_to_pro(self, find_driver):
        """The exact reported case: only mimo-v2.5-pro exists in the catalog,
        and /model mimo-v2.5 must NOT silently resolve to it."""
        got = _find(find_driver, "mimo-v2.5", ["@nous:xiaomi/mimo-v2.5-pro"], "nous")
        assert got is None, f"mimo-v2.5 must not snap to mimo-v2.5-pro (#3368), got {got!r}"

    def test_mimo_v25_no_snap_with_multiple_tiers(self, find_driver):
        got = _find(find_driver, "mimo-v2.5",
                    ["@nous:xiaomi/mimo-v2.5-pro", "@nous:xiaomi/mimo-v2.5-flash"], "nous")
        assert got is None

    def test_bare_mimo_v25_still_resolves_when_it_exists(self, find_driver):
        """If the catalog DOES carry the bare model, it must resolve to it
        (the normalized-equality path, step 2) — not to the -pro variant."""
        got = _find(find_driver, "mimo-v2.5",
                    ["@nous:xiaomi/mimo-v2.5-pro", "@nous:xiaomi/mimo-v2.5"], "nous")
        assert got == "@nous:xiaomi/mimo-v2.5"

    def test_full_pro_name_still_selectable(self, find_driver):
        got = _find(find_driver, "mimo-v2.5-pro", ["@nous:xiaomi/mimo-v2.5-pro"], "nous")
        assert got == "@nous:xiaomi/mimo-v2.5-pro"


class TestFindModelDropdownLegitFuzzyPreserved:
    """#1188 behaviour must survive — incomplete version prefixes still match."""

    def test_gpt5_still_matches_gpt54_mini(self, find_driver):
        got = _find(find_driver, "gpt-5", ["@nous:openai/gpt-5.4-mini"])
        assert got == "@nous:openai/gpt-5.4-mini"

    def test_bare_root_claude_still_matches(self, find_driver):
        got = _find(find_driver, "claude", ["@nous:anthropic/claude-opus-4.6"])
        assert got == "@nous:anthropic/claude-opus-4.6"

    def test_claude_opus_no_version_still_matches(self, find_driver):
        got = _find(find_driver, "claude-opus", ["@nous:anthropic/claude-opus-4.6"])
        assert got == "@nous:anthropic/claude-opus-4.6"

    def test_incomplete_version_still_continues(self, find_driver):
        """mimo-v2 is an INCOMPLETE version (not ending the version), so it may
        still resolve to mimo-v2.5-pro — the extra text continues the version."""
        got = _find(find_driver, "mimo-v2", ["@nous:xiaomi/mimo-v2.5-pro"])
        assert got == "@nous:xiaomi/mimo-v2.5-pro"

    def test_exact_match_unaffected(self, find_driver):
        got = _find(find_driver, "gpt-5.4-mini",
                    ["@nous:openai/gpt-5.4-mini", "@nous:openai/gpt-5.5"])
        assert got == "@nous:openai/gpt-5.4-mini"


# ═══════════════════════════════════════════════════════════════════════════
# _bestModelMatch fallback — must not re-introduce the snap, and must suggest
# ═══════════════════════════════════════════════════════════════════════════


class TestBestModelMatchFallback:
    def test_fallback_does_not_snap_to_pro(self, best_driver):
        out = _best(best_driver, "mimo-v2.5", [
            {"value": "xiaomi/mimo-v2.5-pro", "text": "MiMo V2.5 Pro"},
        ])
        assert out["best"] is None, "fallback must not snap mimo-v2.5 → -pro"

    def test_fallback_suggests_nearest_variant(self, best_driver):
        out = _best(best_driver, "mimo-v2.5", [
            {"value": "xiaomi/mimo-v2.5-pro", "text": "MiMo V2.5 Pro"},
        ])
        assert out["suggestion"] == "xiaomi/mimo-v2.5-pro", (
            "should offer the -pro variant as a 'did you mean' suggestion"
        )

    def test_fallback_exact_still_wins(self, best_driver):
        out = _best(best_driver, "mimo-v2.5", [
            {"value": "xiaomi/mimo-v2.5-pro", "text": "MiMo V2.5 Pro"},
            {"value": "mimo-v2.5", "text": "MiMo V2.5"},
        ])
        assert out["best"] == "mimo-v2.5"

    def test_fallback_shortest_nonversioned_query_unchanged(self, best_driver):
        """A non-versioned query (e.g. 'claude') keeps the shortest-substring
        behaviour from #3394 — the version guard only applies to versioned ones."""
        out = _best(best_driver, "claude", [
            {"value": "anthropic/claude-opus-4.6", "text": "Claude Opus 4.6"},
            {"value": "anthropic/claude-haiku-4.5", "text": "Claude Haiku 4.5"},
        ])
        # shortest option value containing 'claude' — opus-4.6 (25) < haiku-4.5 (26)
        assert out["best"] == "anthropic/claude-opus-4.6"

    def test_fallback_version_continuation_allowed(self, best_driver):
        """An incomplete version (mimo-v2) may still match the longer id via the
        fallback, since the extra text continues the version."""
        out = _best(best_driver, "mimo-v2", [
            {"value": "xiaomi/mimo-v2.5-pro", "text": "MiMo V2.5 Pro"},
        ])
        assert out["best"] == "xiaomi/mimo-v2.5-pro"

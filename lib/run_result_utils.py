import json
import re

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def run_looks_failed(result) -> bool:
    """Whether an agent run genuinely failed -- checked against its actual
    final_output text only, never a substring match over the whole
    RunResult repr. The old `"error" not in str(result)` checks were wrong
    in both directions, confirmed live in production (see
    docs/incident_ledger.md, E3): (1) false negative -- a tool's own
    {"error": ...} dict, once paraphrased into a plain sentence by an
    agent, can contain no literal "error" substring at all, so a genuine
    failure sails through as a "success"; (2) false positive -- a fully
    successful run whose findings/summary happen to discuss "an error" in
    their own prose gets treated as a failed run (str(RunResult) includes
    the entire output, not just a status field), retried needlessly, and
    can trigger an unwanted duplicate side effect (a second Sanity
    publish, a re-run append) on the retry. Only trusts an explicit
    structured failure signal (a JSON {"status": "error", ...} or a bare
    {"error": ...} with no accompanying data), never a bare substring
    anywhere in the text.

    Originally introduced in blog_agent/research_agent.py; shared here so
    image_agent.py and posting_agent.py don't each carry their own copy of
    the same raw-substring bug this replaces."""
    output_text = str(getattr(result, "final_output", result)).strip()
    if not output_text:
        return True
    fence_match = _JSON_FENCE_RE.search(output_text)
    json_text = fence_match.group(1) if fence_match else output_text
    try:
        parsed = json.loads(json_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False  # Not JSON at all -- can't be a structured error.
    if not isinstance(parsed, dict):
        return False
    if parsed.get("status") == "error":
        return True
    if "error" in parsed and not any(k in parsed for k in ("data", "status", "result")):
        return True
    return False

"""What the audit says, to a terminal and to Discord.

A silent audit is a cron job nobody reads, so it reports every run rather
than only the runs with something to say. That is a deliberate trade: one
message a week is noise a person can absorb, and it is the only thing that
distinguishes "nothing has drifted" from "the schedule has been broken for a
month". An audit that only speaks when it is unhappy is indistinguishable
from an audit that has stopped.

The message names the Project and the rule, never "audit failed". Somebody
reading it on a phone should know which repository to open and what to look
at before they open anything.
"""

import json
import urllib.error
import urllib.request

from . import AuditError

# Discord refuses a message body over 2000 characters, and refusing to post
# is the one failure this code must not have: the message that gets dropped
# is the long one, which is the one that had something to say.
DISCORD_LIMIT = 2000

MARKS = {"pass": "PASS", "fail": "FAIL", "not checked": " -- "}


def text(result: dict) -> str:
    lines = [
        f"doden-contract {result['contractVersion']} - tier 2, "
        f"{len(result['projects'])} Project(s)",
        "",
    ]
    for project in result["projects"]:
        lines.append(f"  {project['repository']}")
        for entry in project["rules"]:
            lines.append(
                f"    [{MARKS[entry['status']]}] {entry['rule']}: {entry['summary']}"
            )
        for finding in project["findings"]:
            lines.append(f"      {finding['rule']}: {finding['message']}")
        lines.append("")
    if any(
        entry["status"] == "not checked"
        for project in result["projects"]
        for entry in project["rules"]
    ):
        lines.append(
            "  -- means the rule was not checked, not that it passed: the "
            "token this audit ran with could not read that setting."
        )
        lines.append("")
    for failure in result["unreadable"]:
        lines.append(f"  {failure['repository']}: {failure['error']}")
    if result["unreadable"]:
        lines.append("")
    lines.append(
        "PASS: every Project's settings match the contract."
        if result["ok"]
        else "FAIL: see above."
    )
    lines.append(
        "Tier 2 is repository settings only. A Project passing both tiers is "
        "not thereby deployable: host and account facts are outside the "
        "contract on purpose."
    )
    return "\n".join(lines)


def discord_message(result: dict) -> str:
    """One message. Which Project, which rule, and what to open."""
    head = f"**Platform Contract - tier 2 audit** - {result['checkedAt']}"
    lines = [head]
    for project in result["projects"]:
        if project["ok"]:
            lines.append(f"✅ `{project['repository']}`")
            continue
        lines.append(f"❌ `{project['repository']}`")
        for finding in project["findings"]:
            lines.append(f"• **{finding['rule']}** - {finding['message']}")
    for failure in result["unreadable"]:
        lines.append(f"⚠️ `{failure['repository']}` could not be read - {failure['error']}")
    if not result["projects"] and not result["unreadable"]:
        lines.append("No Projects are on the list, so nothing was audited.")

    message = "\n".join(lines)
    if len(message) <= DISCORD_LIMIT:
        return message

    # Truncating at the character limit would cut a finding in half and could
    # cut away the fact that more exist. Whole lines are dropped instead, and
    # the message says how many, so the count is never the thing that is lost.
    tail_template = "\n… and {} more line(s); run the audit for the rest."
    # The budget is reserved against the *longest* tail this message could
    # need, so that dropping one more line can never make the tail longer
    # than the room left for it.
    budget = DISCORD_LIMIT - len(tail_template.format(len(lines) - 1))
    kept = [head]
    for line in lines[1:]:
        if len("\n".join(kept + [line])) > budget:
            break
        kept.append(line)
    dropped = len(lines) - len(kept)
    return "\n".join(kept) + (tail_template.format(dropped) if dropped else "")


def post_to_discord(webhook: str, message: str) -> None:
    body = json.dumps({"content": message}).encode("utf-8")
    request = urllib.request.Request(webhook, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "doden-contract-audit")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        # The webhook URL is a credential. `exc.url` carries it, and an
        # exception that prints itself into a public workflow log is how a
        # webhook gets abused, so only the status is reported.
        raise AuditError(
            f"Discord refused the audit's message with {exc.code}. The verdict "
            "is above; what failed is the reporting."
        ) from exc
    except urllib.error.URLError as exc:
        raise AuditError(
            f"Discord could not be reached ({exc.reason}). The verdict is "
            "above; what failed is the reporting."
        ) from exc

"""The coverage ledger of the Ethereum Classic suite, regenerated from what is committed.

Two files, both under `ethereumclassic/`:

- `RULES.json`, a copy of ethereumclassic/core-geth's activation fields for each network, with the
  core-geth commit and files they were read from: {"source": {"repository", "commit", "files"},
  "networks": {<network>: {<core-geth field>: <block>}}}. It changes only in a reviewed pull request
  that moves the commit and the values together. Nothing here reads core-geth.
- `COVERAGE.json`, one entry per rule those fields name: {"rules": [{"rule", "kind", "activation",
  "tests", "status", "knownIssues"}]}. `activation`, `tests` and `status` are derived; `kind` and
  `knownIssues` are written by hand and kept as they are.

A rule's id is its proposal and number, lowercased (`EIP3855FBlock` names `eip3855`), and a fixture
belongs to the rule its folder names (`eip3855_push0`). A test's grade is its `_info.classification`.
A rule's status follows from the rest: partial when any known issue is recorded, else fully functional
when it has a test, else missing. Every partial-graded test must be recorded as a known issue.

These names are drafts until the suite's format contract fixes them.
"""

import json
import re

KINDS = ("standard", "ETC-specific")
_FIELD = re.compile(r"(EIP|ECIP|ECBP)([0-9]+)")
_FOLDER = re.compile(r"(eip|ecip|ecbp)([0-9]+)(_[a-z0-9]+)*")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def rule_of_field(field):
    """The rule a core-geth activation field activates, or None.

    Only a field ending in `Block` is an activation; a rule's parameters, such as `ECIP1010Length`
    or `ECIP1017EraRounds`, are not.
    """
    m = _FIELD.match(field)
    return f"{m.group(1).lower()}{int(m.group(2))}" if m and field.endswith("Block") else None


def _rule_key(rule):
    """Order rules by proposal, then by number: eip2 before eip155."""
    m = re.fullmatch(r"([a-z]+)([0-9]+)", rule)
    return m.group(1), int(m.group(2))


def rule_of_folder(folder):
    """The rule a fixture's rule folder names, or None when the folder is not named for one."""
    m = _FOLDER.fullmatch(folder)
    return f"{m.group(1)}{int(m.group(2))}" if m else None


def render(doc):
    """The ledger's committed form."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def check_rules(rules):
    """Problems with a parsed RULES.json, as strings."""
    problems = []
    if not isinstance(rules, dict):
        return ["RULES.json: not an object"]
    source = rules.get("source")
    if not isinstance(source, dict):
        problems.append("RULES.json: no source")
    else:
        if not isinstance(source.get("repository"), str) or not source.get("repository"):
            problems.append("RULES.json: source.repository is not a name")
        if not isinstance(source.get("commit"), str) or not _COMMIT.fullmatch(source.get("commit")):
            problems.append("RULES.json: source.commit is not a 40-character commit id")
        files = source.get("files")
        if not isinstance(files, list) or not files or not all(isinstance(f, str) and f for f in files):
            problems.append("RULES.json: source.files lists no file")
    networks = rules.get("networks")
    if not isinstance(networks, dict) or not networks:
        problems.append("RULES.json: no networks")
        return problems
    for network, fields in networks.items():
        if not isinstance(fields, dict) or not fields:
            problems.append(f"RULES.json: {network} lists no field")
            continue
        for field, block in fields.items():
            if rule_of_field(field) is None:
                problems.append(f"RULES.json: {network}.{field} is not an activation field of an EIP, ECIP or ECBP")
            if not isinstance(block, int) or isinstance(block, bool) or block < 0:
                problems.append(f"RULES.json: {network}.{field} is not a block number")
    return problems


def regenerate(rules, fixtures, committed):
    """The ledger as `tools/` writes it, and the problems found on the way.

    `rules` is the parsed RULES.json (already checked); `fixtures` maps a fixture's path to (its rule
    folder, {test name: grade}); `committed` is the parsed COVERAGE.json, or None.
    """
    problems = []
    activation = {}
    for network, fields in sorted(rules["networks"].items()):
        for field, block in sorted(fields.items()):
            activation.setdefault(rule_of_field(field), {}).setdefault(network, {})[field] = block
    tests = {rule: [] for rule in activation}
    for path, (folder, graded) in sorted(fixtures.items()):
        rule = rule_of_folder(folder)
        if rule not in tests:
            problems.append(f"{path}: its rule, {rule}, is not in RULES.json")
            continue
        for name, grade in sorted(graded.items()):
            tests[rule].append({"path": path, "test": name, "grade": grade})

    kept = {}
    description = None
    if committed is not None:
        if not isinstance(committed, dict) or not isinstance(committed.get("rules"), list):
            problems.append("COVERAGE.json: no list of rules")
        else:
            description = committed.get("description")
            for entry in committed["rules"]:
                if isinstance(entry, dict) and isinstance(entry.get("rule"), str):
                    if entry["rule"] in kept:
                        problems.append(f"COVERAGE.json: {entry['rule']} appears twice")
                    kept[entry["rule"]] = entry
                else:
                    problems.append("COVERAGE.json: an entry without a rule")
            for rule in sorted(set(kept) - set(activation)):
                problems.append(f"COVERAGE.json: {rule} is not in RULES.json")

    entries = []
    for rule in sorted(activation, key=_rule_key):
        old = kept.get(rule, {})
        kind = old.get("kind")
        issues = old.get("knownIssues", [])
        if kind not in KINDS:
            problems.append(f"COVERAGE.json: {rule}'s kind is not one of {', '.join(KINDS)}")
        if not isinstance(issues, list) or not all(
                isinstance(i, dict) and set(i) == {"path", "test", "issue"}
                and all(isinstance(v, str) and v for v in i.values()) for i in issues):
            problems.append(f"COVERAGE.json: {rule}'s knownIssues are not entries of path, test and issue")
            issues = []
        recorded = {(i["path"], i["test"]) for i in issues}
        for t in tests[rule]:
            if t["grade"] == "partial" and (t["path"], t["test"]) not in recorded:
                problems.append(f"{t['path']}: {t['test']} is partial and not recorded as a known issue of {rule}")
        status = "partial" if issues else "fully functional" if tests[rule] else "missing"
        entries.append({"rule": rule, "kind": kind, "activation": activation[rule], "tests": tests[rule],
                        "status": status, "knownIssues": issues})
    doc = {}
    if description is not None:
        doc["description"] = description
    doc["rules"] = entries
    return render(doc), problems

"""Small repositories shaped like this one, built in temporary directories for the tests.

Git runs with no inherited GIT_ variable, no global or system configuration and an empty home, so
neither a caller's repository (a hook exports GIT_DIR) nor a machine's hooks, templates or ignore
files can change a result or be written to. No label beginning with ETC_ appears in anything built
here.
"""

import atexit
import json
import os
import shutil
import subprocess
import tempfile
from unittest import mock

import checks
import retesteth

_HOME = tempfile.mkdtemp(prefix="core-geth-tests-home-")
atexit.register(shutil.rmtree, _HOME, True)
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
ENV.update(
    HOME=_HOME, XDG_CONFIG_HOME=_HOME,
    GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1",
    GIT_CEILING_DIRECTORIES=os.path.dirname(_HOME),
    GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.invalid",
    GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.invalid",
    GIT_AUTHOR_DATE="2000-01-01T00:00:00Z", GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
    GIT_TERMINAL_PROMPT="0",
)
BRANCH = "dep-source"

ONE_FILLER = b"""{
  "one": {
    "//comment": "a repeated key, as seven legacy fillers have",
    "env": {"currentNumber": "0x01"},
    "//comment": "its second value",
    "expect": [{"network": ["Frontier"], "result": {"0x1000": {"storage": {"0x00": "0x01"}}}}]
  }
}
"""

TWO_FILLER = b"""---
two:
  # a comment
  env:
    currentNumber: 1
  pre:
    0000000000000000000000000000000000001000:
      code: |
        {
          [[0]] 1
        }
      storage: {}
  transaction:
    gasLimit:
    - !!int 400000
    data: [ ":raw 0x00", ":raw 0x01" ]
"""

LEFT_FILLER = b'{"left": {"env": {}}}\n'
UNNAMED_FILLER = b'{"unnamed": {"env": {}}}\n'
LICENSE = b"The source's license.\n"


def git(root, *args, data=None, env=None):
    r = subprocess.run(["git", "-C", root, "-c", "init.defaultBranch=main", *args], input=data,
                       capture_output=True, env=env or ENV, check=True)
    return r.stdout.decode()


def filled(name, filler_path, filler_bytes, post):
    """A filled test, with retesteth's two hashes recorded in its _info."""
    test = {
        "env": {"currentNumber": "0x01"},
        "post": {"Frontier": [{"hash": post, "indexes": {"data": 0, "gas": 0, "value": 0}}]},
        "_info": {"source": filler_path, "sourceHash": retesteth.source_hash(filler_path, filler_bytes)},
    }
    test["_info"]["generatedTestHash"] = retesteth.generated_test_hash(name, test)
    return json.dumps({name: test}, indent=4).encode() + b"\n"


def write(root, path, data):
    full = os.path.join(root, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def read_json(root, path):
    with open(os.path.join(root, *path.split("/")), encoding="utf-8") as f:
        return json.load(f)


def write_json(root, path, doc):
    write(root, path, json.dumps(doc, indent=2) + "\n")


def build_legacy(root):
    """The source commit on BRANCH, and legacy/ carried from it minus a recorded list."""
    git(root, "init", "-q")
    source = {
        "GeneralStateTests/stA/one.json": filled("one", "src/GeneralStateTestsFiller/stA/oneFiller.json", ONE_FILLER, "0x" + "11" * 32),
        "GeneralStateTests/stA/two.json": filled("two", "src/GeneralStateTestsFiller/stA/twoFiller.yml", TWO_FILLER, "0x" + "22" * 32),
        "GeneralStateTests/stB/left.json": filled("left", "src/GeneralStateTestsFiller/stB/leftFiller.json", LEFT_FILLER, "0x" + "33" * 32),
        "src/GeneralStateTestsFiller/stA/oneFiller.json": ONE_FILLER,
        "src/GeneralStateTestsFiller/stA/twoFiller.yml": TWO_FILLER,
        "src/GeneralStateTestsFiller/stB/leftFiller.json": LEFT_FILLER,
        "src/GeneralStateTestsFiller/stC/unnamedFiller.json": UNNAMED_FILLER,
        "LICENSE": LICENSE,
    }
    with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as meta:
        for path, data in source.items():
            write(work, path, data)
        env = dict(ENV, GIT_INDEX_FILE=os.path.join(meta, "index"))
        git(work, "--git-dir=" + os.path.join(root, ".git"), "--work-tree=" + work, "add", "-A", env=env)
        tree = git(root, "write-tree", env=env).strip()
    commit = git(root, "commit-tree", tree, "-m", "the source").strip()
    git(root, "update-ref", f"refs/heads/{BRANCH}", commit)
    git(root, "update-ref", f"refs/remotes/origin/{BRANCH}", commit)

    left_tests = ["stB/left.json"]
    left_fillers = ["stB/leftFiller.json", "stC/unnamedFiller.json"]

    def carried(src_dir, left):
        with tempfile.TemporaryDirectory() as work:
            env = dict(ENV, GIT_INDEX_FILE=os.path.join(work, "index"))
            git(root, "read-tree", f"{commit}:{src_dir}", env=env)
            git(root, "update-index", "--force-remove", "--stdin", data="".join(p + "\n" for p in left).encode(), env=env)
            return git(root, "write-tree", env=env).strip()

    manifest = {
        "source": {"repository": "example/tests", "commit": commit, "preserved_on_branch": BRANCH},
        "carried": [
            {"path": "legacy/GeneralStateTests", "from": "GeneralStateTests",
             "from_tree": git(root, "rev-parse", f"{commit}:GeneralStateTests").strip(),
             "tree": carried("GeneralStateTests", left_tests), "files": 2,
             "left_out": [{"path": p, "reason": "no label this suite carries"} for p in left_tests]},
            {"path": "legacy/src/GeneralStateTestsFiller", "from": "src/GeneralStateTestsFiller",
             "from_tree": git(root, "rev-parse", f"{commit}:src/GeneralStateTestsFiller").strip(),
             "tree": carried("src/GeneralStateTestsFiller", left_fillers), "files": 2,
             "left_out": [{"path": p, "reason": "named by no carried test"} for p in left_fillers]},
            {"path": "legacy/LICENSE", "from": "LICENSE", "blob": git(root, "rev-parse", f"{commit}:LICENSE").strip()},
        ],
    }
    for path, data in source.items():
        if path.startswith("GeneralStateTests/") and path[len("GeneralStateTests/"):] not in left_tests:
            write(root, "legacy/" + path, data)
        if path.startswith("src/GeneralStateTestsFiller/") and path[len("src/GeneralStateTestsFiller/"):] not in left_fillers:
            write(root, "legacy/" + path, data)
    write(root, "legacy/LICENSE", LICENSE)
    write(root, "legacy/README.md", "The legacy suite.\n")
    write_json(root, "legacy/MANIFEST.json", manifest)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "the legacy suite")
    return commit


def fixture(grade="conformance", second_check=None, body=None, **info):
    """One test of the Ethereum Classic suite, its content hash recorded."""
    test = dict(body or {"env": {"currentNumber": "0x01"}, "post": {"Shanghai": [{"hash": "0x" + "44" * 32}]}})
    meta = {"classification": grade, "oracle": "computed from the specification", "source": "a generator"}
    if second_check is not None:
        meta["secondCheck"] = second_check
    meta.update(info)
    test["_info"] = meta
    if "contentHash" not in info:
        meta["contentHash"] = checks.content_hash(test)
    return test


RULES = {
    "source": {"repository": "ethereumclassic/core-geth", "commit": "0" * 40, "files": ["params/config_classic.go"]},
    "networks": {"classic": {"EIP155Block": 3000000, "EIP3855FBlock": 19250000},
                 "mordor": {"EIP3855FBlock": 9957000}},
}


def write_ledger(root, kinds=None, issues=None):
    """RULES.json, and COVERAGE.json as tools/ would write it from the fixtures on disk."""
    write_json(root, checks.RULES, RULES)
    entries = [{"rule": r, "kind": (kinds or {}).get(r, "standard"), "knownIssues": (issues or {}).get(r, [])}
               for r in ("eip155", "eip3855")]
    write_json(root, checks.LEDGER, {"rules": entries})
    results = run(root, write_ledger=True)
    return results


def hermetic():
    """The process environment replaced by ENV, for code that runs git itself."""
    return mock.patch.dict(os.environ, ENV, clear=True)


def run(root, **options):
    """Run every check on `root`; returns {check name: Result}."""
    options.setdefault("sample", ("stA/one.json", "stA/two.json"))
    options.setdefault("must_cover", frozenset())
    with hermetic():
        return {r.name: r for r in checks.Checker(root, **options).run()}


def tree(root):
    with hermetic():
        return checks.Tree(root)


class Repo:
    """A context manager: a fresh repository with the legacy suite built."""

    def __enter__(self):
        self.root = tempfile.mkdtemp(prefix="core-geth-tests-")
        try:
            self.commit = build_legacy(self.root)
        except BaseException:
            shutil.rmtree(self.root, ignore_errors=True)
            raise
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root)

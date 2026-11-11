"""Check this repository: python3 -I tools/check.py [options], from anywhere inside a checkout.

Exits 0 when no check fails, 1 when one does, and 2 when the checkout cannot be read. A check that
cannot run here (the source commit absent from a shallow clone, the ledger not yet written) says so
and does not fail, unless an option requires it.
"""

import argparse
import os
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))


def tracked_bytecode(root):
    """Tracked Python bytecode under the checkout. Python can run a cached .pyc in place of the
    source beside it, so a committed one could run code that no review of the .py files saw. This
    runs before any module of tools/ is imported, since this script itself is never read from a cache."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, env=env)
    paths = [os.fsdecode(p) for p in r.stdout.split(b"\0") if p]
    return [p for p in paths if p.endswith((".pyc", ".pyo")) or "__pycache__" in p.split("/")]


def printable(text):
    """`text` with control characters escaped, so nothing read from a file can start a new log line,
    where a CI runner would read it as a workflow command."""
    return "".join(c if c.isprintable() or c == " " else repr(c)[1:-1] for c in str(text))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -I tools/check.py", description=__doc__.split("\n")[0])
    parser.add_argument("--root", default=os.path.dirname(TOOLS),
                        help="the checkout to check (default: the one holding this file)")
    parser.add_argument("--require-source", action="store_true",
                        help="fail, rather than skip, when the legacy suite's source commit or branch, or the "
                             "committed tree, is absent")
    parser.add_argument("--legacy-hashes", action="store_true",
                        help="recompute sourceHash and generatedTestHash for every legacy test, not a sample")
    parser.add_argument("--write-ledger", action="store_true",
                        help="write ethereumclassic/COVERAGE.json as regenerated, keeping what is written by hand")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)

    bytecode = tracked_bytecode(root)
    if bytecode:
        print("FAIL     bytecode         Python bytecode is tracked, and could run in place of the reviewed source:")
        for path in bytecode[:50]:
            print(f"         - {printable(path)}")
        return 1

    sys.path.insert(0, TOOLS)
    import checks  # noqa: E402  (after the bytecode check, on purpose)

    started = time.monotonic()
    try:
        results = checks.Checker(root, require_source=args.require_source,
                                 all_legacy_hashes=args.legacy_hashes, write_ledger=args.write_ledger).run()
    except checks.CheckError as e:
        print(f"check: {printable(e)}", file=sys.stderr)
        return 2
    for r in results:
        print(f"{r.status:<8} {r.name:<16} {printable(r.not_run or r.summary)}")
        for message in r.failures[:50]:
            print(f"         - {printable(message)}")
        if len(r.failures) > 50:
            print(f"         - and {len(r.failures) - 50} more")
    counts = {status: sum(r.status == status for r in results) for status in ("PASS", "NOT RUN", "FAIL")}
    elapsed = time.monotonic() - started
    print(f"{'FAIL' if counts['FAIL'] else 'PASS'}: {counts['PASS']} passed, {counts['NOT RUN']} not run, "
          f"{counts['FAIL']} failed ({elapsed:.1f}s, Python {sys.version.split()[0]})")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())

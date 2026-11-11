"""Build a suite's release file: python3 -I tools/release.py <tag> --out DIR, from anywhere inside a checkout.

<tag> is the suite and version the release is tagged with, such as legacy/v0.0.1. The file is built from the tree
committed at HEAD, only in a clean checkout, and only once every check passes with the legacy suite's source commit
present. --out writes the file and a .sha256 beside it, never over an existing file; --check FILE compares a
downloaded file with the one HEAD builds instead.

Exits 0 when the file is written or the checked file holds what HEAD builds, 1 when it is refused or differs, and 2
when the checkout cannot be read or the command is given wrong arguments.
"""

import argparse
import os
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))


def tracked_bytecode(root):
    """Tracked Python bytecode under the checkout, as check.py refuses it: this runs before any module of tools/ is
    imported, since a cached .pyc could run in place of the source beside it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, env=env)
    paths = [os.fsdecode(p) for p in r.stdout.split(b"\0") if p]
    return [p for p in paths if p.endswith((".pyc", ".pyo")) or "__pycache__" in p.split("/")]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -I tools/release.py", description=__doc__.split("\n")[0])
    parser.add_argument("tag", help="the suite and version, as the release is tagged: legacy/v0.0.1")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--out", metavar="DIR", help="write the file and its .sha256 into DIR, which the checkout "
                                                     "must not list (outside it, or ignored by it)")
    action.add_argument("--check", metavar="FILE", help="compare FILE with the file HEAD builds")
    parser.add_argument("--notes", metavar="FILE", help="with --out, also write the release notes to FILE")
    parser.add_argument("--root", default=os.path.dirname(TOOLS),
                        help="the checkout to build from (default: the one holding this file)")
    args = parser.parse_args(argv)
    if args.notes and not args.out:
        parser.error("--notes goes with --out")
    root = os.path.abspath(args.root)

    # The checkout these tools run from, and the one built from, if another.
    bytecode = sorted({p for r in {os.path.dirname(TOOLS), root} for p in tracked_bytecode(r)})
    if bytecode:
        print("refused  Python bytecode is tracked, and could run in place of the reviewed source:")
        for path in bytecode[:50]:
            print(f"         - {path!r}")
        return 1

    sys.path.insert(0, TOOLS)
    import check  # noqa: E402  (after the bytecode check, on purpose)
    import checks  # noqa: E402
    import releases  # noqa: E402
    p = check.printable

    try:
        data = releases.read_file(args.check) if args.check else None
        built = releases.build(root, args.tag)
        print("checks   every check passes, the legacy suite's source commit read")
        print(f"built    {built.name}, from {built.commit} ({built.tag})")
        for path, kind, oid in built.ids:
            print(f"holds    {path}: {kind} {oid}, as {releases.SUITES[built.suite]['manifest']} records")
        print(f"files    {len(built.files):,}; tar {len(built.tar):,} bytes, sha256 {built.tar_sha256}")
        print(f"size     {len(built.data):,} bytes, compressed by zlib {built.zlib} (Python {built.python})")
        print(f"sha256   {built.sha256}")
        if args.out:
            for path in releases.write(built, root, args.out, args.notes):
                print(f"wrote    {p(path)}")
            return 0
        result = releases.compare(built, data)
        if os.path.basename(args.check) != built.name:
            print(f"note     {p(os.path.basename(args.check))} is named {built.name} in the release")
        if result == "bytes":
            print(f"same     {p(args.check)} is the file {built.commit[:12]} builds, byte for byte")
        else:
            print(f"same     {p(args.check)} holds the tar {built.commit[:12]} builds, deflated differently "
                  f"from zlib {built.zlib} here")
        return 0
    except releases.Refused as e:
        print(f"refused  {p(e)}")
        for detail in e.details:
            print(f"         - {p(detail)}")
        return 1
    except OSError as e:
        print(f"refused  {p(e)}")
        return 1
    except checks.CheckError as e:
        print(f"release: {p(e)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

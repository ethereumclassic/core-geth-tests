"""Build and read a suite's release file, the file a client downloads in place of a checkout.

The file is a USTAR tar compressed with gzip. Every entry is a regular file of the suite, mode 0644, owner 0:0
with no names, time 0, in path order. The tar is written here field by field, so one tree gives one tar on any
machine. zlib compresses it at level 9 under a fixed gzip header; `compare` reads the tar inside as well as the
bytes, so a zlib that deflates differently shows as that and nothing more.
"""

import hashlib
import os
import platform
import re
import stat
import struct
import tempfile
import zlib

import checks
import gitobj

# Each suite's release file holds these paths, as they sit in the repository: a tree, every file under it, or a
# blob, that one file. Its notes are written per suite.
SUITES = {
    "legacy": {
        "paths": {"legacy/GeneralStateTests": "tree", "legacy/LICENSE": "blob"},
        "manifest": "legacy/MANIFEST.json",
        "notes": """\
The legacy suite's state tests, built from {commit} by `tools/release.py`.

- **Holds** the {count:,} state tests under `legacy/GeneralStateTests/` and their license, `legacy/LICENSE`.
- **Source:** `{source_repository}` at `{source_commit}`, unedited. `legacy/MANIFEST.json` lists what was left out, \
and why.
- **Grade:** regression baseline. core-geth filled their expected values, so agreement shows a client still computes \
what it did then, never that the result is right.

```
{checksum_line}
```

Compressed by zlib {zlib} (Python {python}). To check a copy against this tag, in a clone of this repository:

```sh
git checkout {tag}
git merge-base --is-ancestor {tag} origin/main
gh release download {tag} --pattern {name} --dir ..
python3 -I tools/release.py {tag} --check ../{name}
gh release verify-asset {tag} ../{name}
```
""",
    },
}
_TAG = re.compile(r"(?P<suite>[a-z]+)/(?P<version>v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))")
_REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")

BLOCK = 512
RECORD = 20 * BLOCK
GZIP_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"  # deflate, no flags or name, time 0, level 9, OS unknown
MAX_FILE = 1 << 30  # bytes: a file, or a tar inside one, past this is refused


class Refused(Exception):
    """The file is not built, or a checked file is not what HEAD builds."""

    def __init__(self, message, details=()):
        super().__init__(message)
        self.details = list(details)


def parse_tag(tag):
    """(suite, version) for a release tag such as legacy/v0.0.1."""
    m = _TAG.fullmatch(tag)
    if not m or m["suite"] not in SUITES:
        raise Refused(f"{tag!r} is not a release tag: one of {', '.join(sorted(SUITES))}, then /vMAJOR.MINOR.PATCH")
    return m["suite"], m["version"]


def file_name(suite, version):
    return f"core-geth-tests-{suite}-{version}.tar.gz"


def inside(path, suite):
    """Whether `path` is one of the files `suite`'s release holds: under one of its trees, or one of its blobs."""
    return any(path.startswith(top + "/") if kind == "tree" else path == top
               for top, kind in SUITES[suite]["paths"].items())


def _out(r):
    return r.stdout.decode().strip()


def _failed(r, what):
    if r.returncode:
        raise checks.CheckError(f"git {what} failed: {r.stderr.decode(errors='replace').strip()}")
    return r


def run_checks(root):
    """The checks check.py runs, the legacy suite's source commit required: the ones that fail."""
    return [r for r in checks.Checker(root, require_source=True).run() if r.status == "FAIL"]


# -- writing -----------------------------------------------------------------------------------------

def _octal(n, width):
    digits = b"%0*o" % (width - 1, n)
    if len(digits) != width - 1:
        raise Refused(f"{n} does not fit a {width}-byte field")
    return digits + b"\0"


def _text(raw, width):
    if len(raw) > width:
        raise Refused(f"{raw!r} does not fit a {width}-byte field")
    return raw + b"\0" * (width - len(raw))


def _split(raw):
    """USTAR's (prefix, name): the whole path in name when it fits, else split at the first "/" that fits."""
    if len(raw) <= 100:
        return b"", raw
    parts = raw.split(b"/")
    for i in range(1, len(parts)):
        prefix, name = b"/".join(parts[:i]), b"/".join(parts[i:])
        if len(prefix) <= 155 and len(name) <= 100:
            return prefix, name
    raise Refused(f"{raw.decode()}: too long for a USTAR header")


def header(path, size):
    """The 512-byte USTAR header of a regular file at `path` holding `size` bytes."""
    prefix, name = _split(path.encode("utf-8"))
    block = (_text(name, 100) + _octal(0o644, 8) + _octal(0, 8) + _octal(0, 8) + _octal(size, 12)
             + _octal(0, 12) + b" " * 8 + b"0" + _text(b"", 100) + b"ustar\0" + b"00"
             + _text(b"", 32) + _text(b"", 32) + _text(b"", 8) + _text(b"", 8) + _text(prefix, 155) + b"\0" * 12)
    return block[:148] + b"%06o\0 " % sum(block) + block[156:]


def pack(files):
    """The tar of {path: bytes}, entries in path order, ended by two zero blocks and padded to a record."""
    out = bytearray()
    for path in sorted(files, key=lambda p: p.encode("utf-8")):
        data = files[path]
        out += header(path, len(data)) + data + b"\0" * (-len(data) % BLOCK)
    out += b"\0" * (2 * BLOCK)
    out += b"\0" * (-len(out) % RECORD)
    return bytes(out)


def compress(tar):
    c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS, 8, zlib.Z_DEFAULT_STRATEGY)
    return GZIP_HEADER + c.compress(tar) + c.flush() + struct.pack("<II", zlib.crc32(tar), len(tar) & 0xFFFFFFFF)


# -- reading -----------------------------------------------------------------------------------------

def inflate(data, limit=MAX_FILE):
    """The tar inside a file this tool writes: one gzip member under GZIP_HEADER, nothing after it, at most `limit`
    bytes once inflated."""
    if data[:len(GZIP_HEADER)] != GZIP_HEADER:
        raise Refused("not a gzip file with this tool's header")
    d = zlib.decompressobj(-zlib.MAX_WBITS)
    try:
        tar = d.decompress(data[len(GZIP_HEADER):], limit)
    except zlib.error as e:
        raise Refused(f"its deflate stream does not read: {e}")
    if d.unconsumed_tail:
        raise Refused(f"it inflates past {limit:,} bytes")
    if not d.eof or len(d.unused_data) < 8:
        raise Refused("it is cut short")
    if len(d.unused_data) > 8:
        raise Refused("it holds data after its gzip stream")
    crc, size = struct.unpack("<II", d.unused_data)
    if crc != zlib.crc32(tar) or size != len(tar) & 0xFFFFFFFF:
        raise Refused("its gzip trailer does not match its contents")
    return tar


def entries(tar, suite):
    """[(path, bytes)] of a tar, refused unless each entry is a regular file of `suite` under a header this tool
    writes. That is at least as strict as core-geth's extractor, which writes regular files only and refuses a path
    that leaves its destination: here any other entry, and any path with an empty, "." or ".." part, is refused."""
    found, seen, pos = [], set(), 0
    while True:
        block = tar[pos:pos + BLOCK]
        if len(block) < BLOCK:
            raise Refused("its tar has no end-of-archive blocks")
        if block == b"\0" * BLOCK:
            rest = tar[pos:]
            if rest.strip(b"\0") or len(rest) < 2 * BLOCK or len(tar) % RECORD:
                raise Refused("its tar does not end as this tool ends one")
            return found
        name, prefix = block[0:100].split(b"\0", 1)[0], block[345:500].split(b"\0", 1)[0]
        raw = prefix + b"/" + name if prefix else name
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused(f"{raw!r}: a path that is not UTF-8")
        kind = block[156:157]
        if kind != b"0":
            raise Refused(f"{path}: an entry of type {kind.decode('latin-1')!r}, not a regular file")
        if "\\" in path or any(p in ("", ".", "..") for p in path.split("/")):
            raise Refused(f"{path!r}: not a relative path inside the destination")
        if not inside(path, suite):
            raise Refused(f"{path}: outside the {suite} suite's paths ({', '.join(SUITES[suite]['paths'])})")
        if path in seen:
            raise Refused(f"{path}: twice")
        seen.add(path)
        digits = block[124:135]
        if not re.fullmatch(rb"[0-7]{11}", digits):
            raise Refused(f"{path}: a size that does not read")
        size = int(digits, 8)
        if block != header(path, size):
            raise Refused(f"{path}: a header this tool does not write")
        data = tar[pos + BLOCK:pos + BLOCK + size]
        padding = tar[pos + BLOCK + size:pos + BLOCK + size + (-size % BLOCK)]
        if len(data) < size or padding.strip(b"\0"):
            raise Refused(f"{path}: cut short, or padded with data")
        found.append((path, data))
        pos += BLOCK + size + (-size % BLOCK)


# -- building ----------------------------------------------------------------------------------------

class Built:
    """A release file built in memory: its name, bytes and tar, the tree it came from, and what compressed it."""

    def __init__(self, tag, suite, version, commit, files, ids, source, tar, data):
        self.tag, self.suite, self.version, self.commit = tag, suite, version, commit
        self.files, self.ids, self.source = files, ids, source
        self.tar, self.data = tar, data
        self.name = file_name(suite, version)
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.tar_sha256 = hashlib.sha256(tar).hexdigest()
        self.zlib, self.python = zlib.ZLIB_RUNTIME_VERSION, platform.python_version()

    def checksum_line(self):
        """The line `sha256sum -c` reads, and the one core-geth's build/checksums.txt carries."""
        return f"{self.sha256}  {self.name}\n"


def _suite_files(root, commit, suite):
    """{path: bytes} of the suite's paths at `commit`, from git's objects; every one a plain file of the suite."""
    listed = {}
    for top in SUITES[suite]["paths"]:
        r = _failed(checks.git(root, "ls-tree", "-r", "-z", commit, "--", top), "ls-tree")
        records = [x for x in r.stdout.split(b"\0") if x]
        if not records:
            raise Refused(f"{top}: not in {commit[:12]}")
        for record in records:
            meta, _, raw = record.partition(b"\t")
            mode, kind, oid = meta.decode("ascii").split(" ")
            path = raw.decode("utf-8")
            if kind != "blob" or mode != "100644":
                raise Refused(f"{path}: mode {mode} {kind}, not a regular file")
            if not inside(path, suite):
                raise Refused(f"{path}: at {top} in {commit[:12]}, and not one of the suite's files")
            listed[path] = oid
    order = sorted(listed)
    r = _failed(checks.git(root, "cat-file", "--batch", data="".join(listed[p] + "\n" for p in order).encode()),
                "cat-file")
    files, pos, out = {}, 0, r.stdout
    for path in order:
        nl = out.find(b"\n", pos)
        fields = out[pos:nl].decode("ascii", "replace").split(" ") if nl >= 0 else []
        if len(fields) != 3 or fields[:2] != [listed[path], "blob"]:
            raise checks.CheckError(f"{path}: git cannot read its blob {listed[path]} here ({' '.join(fields)})")
        data = out[nl + 1:nl + 1 + int(fields[2])]
        if gitobj.blob_id(data) != listed[path]:
            raise Refused(f"{path}: git returned something other than its blob")
        files[path] = data
        pos = nl + 1 + len(data) + 1
    return files


def _recorded(root, commit, suite):
    """[(path, kind, id)] for the suite's paths at `commit`, each the id its manifest records; and the manifest's
    source."""
    manifest = SUITES[suite]["manifest"]
    r = checks.git(root, "show", f"{commit}:{manifest}")
    if r.returncode:
        raise Refused(f"{manifest}: not in {commit[:12]}")
    try:
        doc = checks.load_json(r.stdout)
    except (ValueError, UnicodeDecodeError) as e:
        raise Refused(f"{manifest}: does not parse: {e}")
    if not isinstance(doc, dict) or not isinstance(doc.get("carried"), list) or not isinstance(doc.get("source"), dict):
        raise Refused(f"{manifest}: no source or carried list")
    # Both reach the release notes, so each must be what it says and nothing that Markdown would read.
    repository, source_commit = doc["source"].get("repository"), doc["source"].get("commit")
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise Refused(f"{manifest}: source.repository is not owner/name: {repository!r}")
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise Refused(f"{manifest}: source.commit is not a commit id: {source_commit!r}")
    carried = {e.get("path"): e for e in doc["carried"] if isinstance(e, dict)}
    ids = []
    for path, kind in SUITES[suite]["paths"].items():
        recorded = carried.get(path, {}).get(kind)
        actual = _out(checks.git(root, "rev-parse", "--verify", "--quiet", f"{commit}:{path}"))
        if not recorded:
            raise Refused(f"{path}: {manifest} records no {kind} id for it")
        if actual != recorded:
            raise Refused(f"{path}: {actual} at {commit[:12]}, {recorded} recorded in {manifest}")
        ids.append((path, kind, actual))
    return ids, doc["source"]


def build(root, tag):
    """Build `tag`'s file from the tree committed at HEAD, in memory. Refused unless the tag names a known suite and
    version and, if it is in this clone, HEAD's commit; git is told to skip no file on disk and finds nothing
    uncommitted, so the checks, which read the disk, read HEAD; every check passes; each file is a plain file of the
    suite; the suite's ids are those its manifest records; two builds agree; and the file reads back as those files."""
    suite, version = parse_tag(tag)
    r = checks.git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if r.returncode:
        raise checks.CheckError(f"{root} is not a git checkout with a commit")
    commit = _out(r)
    if checks.git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode == 0:
        tagged = checks.git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
        if tagged.returncode:
            raise Refused(f"{tag} does not name a commit")
        if _out(tagged) != commit:
            raise Refused(f"{tag} is {_out(tagged)[:12]} and HEAD is {commit[:12]}; check out {tag} first")
    flagged = _failed(checks.git(root, "ls-files", "-v", "-z"), "ls-files")
    hidden = [x[2:].decode("utf-8", "replace") for x in flagged.stdout.split(b"\0") if x and x[:1] != b"H"]
    if hidden:
        raise Refused("git is told to skip these files on disk (assume-unchanged, skip-worktree or unmerged), so a "
                      "clean status would prove nothing", hidden[:20])
    status = _failed(checks.git(root, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "status",
                                "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"), "status")
    changes = [x.decode("utf-8", "replace") for x in status.stdout.split(b"\0") if x]
    if changes:
        raise Refused("the checkout has uncommitted changes; the file is built only from a clean one", changes[:20])
    failed = run_checks(root)
    if failed:
        raise Refused("a check fails", [f"{r.name}: {m}" for r in failed for m in (r.failures or [r.summary])][:20])
    files = _suite_files(root, commit, suite)
    ids, source = _recorded(root, commit, suite)
    tar = pack(files)
    data = compress(tar)
    if compress(pack(files)) != data:
        raise Refused("two builds of the same tree differ")
    back = entries(inflate(data), suite)
    if [p for p, _ in back] != sorted(files, key=lambda p: p.encode("utf-8")) or any(files[p] != d for p, d in back):
        raise Refused("the file does not read back as the files it was built from")
    return Built(tag, suite, version, commit, files, ids, source, tar, data)


def notes(built):
    """The release's notes, in Markdown, from its suite's text."""
    trees = [top for top, kind in SUITES[built.suite]["paths"].items() if kind == "tree"]
    return SUITES[built.suite]["notes"].format(
        commit=built.commit, tag=built.tag, name=built.name, checksum_line=built.checksum_line().strip(),
        count=sum(1 for p in built.files if any(p.startswith(t + "/") for t in trees)),
        source_repository=built.source.get("repository"), source_commit=built.source.get("commit"),
        zlib=built.zlib, python=built.python)


def _inside_checkout(root, path):
    """`path`, relative to the checkout, if it lies inside it and git would list it there; else None."""
    real_root, real = os.path.realpath(root), os.path.realpath(path)
    if os.path.commonpath([real_root, real]) != real_root:
        return None
    rel = os.path.relpath(real, real_root).replace(os.sep, "/")
    return None if checks.git(root, "check-ignore", "-q", "--no-index", "--", rel).returncode == 0 else rel


def _place(target, data):
    """Write `data` to `target` whole or not at all: under a temporary name beside it, then linked into place, which
    fails rather than replace a file."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(target)), prefix=".release-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.link(tmp, target)
    finally:
        os.unlink(tmp)


def write(built, root, outdir, notes_path=None):
    """Write the file and its .sha256 into `outdir`, and the notes to `notes_path` if given. Each lands whole or not
    at all, never over a file and never where the checkout would list it; if one cannot be written, none is left.
    Returns the paths written."""
    targets = [(os.path.join(outdir, built.name), built.data),
               (os.path.join(outdir, built.name + ".sha256"), built.checksum_line().encode("ascii"))]
    if notes_path:
        targets.append((notes_path, notes(built).encode("utf-8")))
    seen = set()
    for target, _ in targets:
        if os.path.realpath(target) in seen:
            raise Refused(f"{target}: named for two files")
        seen.add(os.path.realpath(target))
        rel = _inside_checkout(root, target)
        if rel is not None:
            raise Refused(f"{rel}: inside the checkout and not ignored; write it outside")
        if os.path.lexists(target):
            raise Refused(f"{target}: exists; nothing is written over a file")
    os.makedirs(outdir, exist_ok=True)
    for target, _ in targets:
        if not os.path.isdir(os.path.dirname(os.path.abspath(target))):
            raise Refused(f"{target}: its directory does not exist")
    written = []
    try:
        for target, data in targets:
            _place(target, data)
            written.append(target)
    except OSError as e:
        for target in written:
            os.unlink(target)
        raise Refused(f"not written, and nothing left behind: {e}")
    return written


def read_file(path):
    """A downloaded file's bytes, refused unless it is a regular file within MAX_FILE. It is opened without blocking,
    so a pipe is refused rather than waited on."""
    if os.path.islink(path):
        raise Refused(f"{path}: a symbolic link; give the file itself")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            raise Refused(f"{path}: not a regular file of at most {MAX_FILE:,} bytes")
        os.set_blocking(fd, True)
        return f.read(MAX_FILE + 1)


def compare(built, data):
    """'bytes' when `data` is the file HEAD builds; 'tar' when it holds the same tar under this tool's gzip header,
    deflated differently; otherwise refused, naming what differs."""
    if data == built.data:
        return "bytes"
    tar = inflate(data, limit=len(built.tar) + 1)
    if tar == built.tar:
        return "tar"
    theirs, ours = dict(entries(tar, built.suite)), built.files
    differ = sorted(set(theirs) ^ set(ours)) + sorted(p for p in set(theirs) & set(ours) if theirs[p] != ours[p])
    raise Refused(f"it does not hold what {built.commit[:12]} builds",
                  [f"{p}: differs, or is in one only" for p in differ][:20]
                  or ["the same files, in another order or with other padding"])

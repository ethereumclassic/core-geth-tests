"""The checks `check.py` runs over a checkout of this repository.

Files are the ones git lists (tracked, and untracked files that are not ignored), read from disk, so
a check sees what is checked out rather than what was committed.
"""

import hashlib
import json
import os
import re
import stat
import subprocess

import gitobj
import ledger
import retesteth
import yamlsubset

SUITES = ("legacy", "ethereum", "ethereumclassic")
LEGACY_TESTS = "legacy/GeneralStateTests"
LEGACY_FILLERS = "legacy/src/GeneralStateTestsFiller"
LEGACY_MANIFEST = "legacy/MANIFEST.json"
LEGACY_DOCUMENTS = ("legacy/README.md", LEGACY_MANIFEST)
LEDGER = "ethereumclassic/COVERAGE.json"
RULES = "ethereumclassic/RULES.json"
ADMITTED = ("conformance", "partial")

# Legacy tests whose two retesteth hashes every run recomputes: between them their YAML fillers use
# every feature yamlsubset reads, and one filler is JSON that repeats a key. `--legacy-hashes`
# recomputes all of them.
LEGACY_HASH_SAMPLE = (
    "stTransactionTest/PointAtInfinityECRecover.json",
    "stRefundTest/refundSSTORE.json",
    "stHomesteadSpecific/createContractViaContract.json",
    "stRevertTest/RevertRemoteSubCallStorageOOG.json",
    "stCreateTest/CreateOOGafterMaxCodesize.json",
    "VMTests/vmTests/push0.json",
    "stCreateTest/CreateOOGFromEOARefunds.json",
    "stCreateTest/CreateAddressWarmAfterFail.json",
)

_HEX64 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_FORMAT = re.compile(r"[a-z][a-z0-9_]*")
# Variables that would make git read another repository than the one `--root` names.
_LOCATING = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE")


class CheckError(Exception):
    """The checkout cannot be checked at all."""


class Result:
    def __init__(self, name):
        self.name = name
        self.summary = ""
        self.failures = []
        self.not_run = None

    @property
    def status(self):
        if self.failures:
            return "FAIL"
        return "NOT RUN" if self.not_run else "PASS"

    def fail(self, message):
        self.failures.append(message)


MAX_BYTES = 64 * 1024 * 1024  # per file, far above any file in the suites


def git(root, *args):
    """Run git on the repository at `root`. It never fetches a missing object: with
    GIT_NO_LAZY_FETCH set, a partial clone fails instead."""
    env = {k: v for k, v in os.environ.items() if k not in _LOCATING}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "-C", root, *args], capture_output=True, env=env)


def _refuse_constant(name):
    raise ValueError(f"{name} is not JSON")


def load_json(data, strict=True):
    """Parse JSON bytes. `strict` refuses a key repeated within one object."""
    def pairs(items):
        if strict:
            seen = set()
            for key, _ in items:
                if key in seen:
                    raise ValueError(f"the key {key!r} appears twice in one object")
                seen.add(key)
        return dict(items)
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=_refuse_constant)


def without_info(value):
    """`value` with every object member named `_info` removed, at every depth."""
    if isinstance(value, dict):
        return {k: without_info(v) for k, v in value.items() if k != "_info"}
    if isinstance(value, list):
        return [without_info(v) for v in value]
    return value


def content_hash(test):
    """A test's identity: SHA-256 of its JSON with `_info` removed at every depth, keys sorted, no
    whitespace between tokens and non-ASCII written as UTF-8, exactly as Python's `json.dumps` writes
    it with `sort_keys=True, separators=(",", ":"), ensure_ascii=False`."""
    text = json.dumps(without_info(test), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_tests(doc):
    return isinstance(doc, dict) and bool(doc) and all(isinstance(t, dict) for t in doc.values())


class Tree:
    """The files of a checkout, as git lists them."""

    def __init__(self, root):
        self.root = root
        r = git(root, "ls-files", "-z", "--stage")
        if r.returncode:
            raise CheckError(f"{root} is not a git checkout: {r.stderr.decode(errors='replace').strip()}")
        paths = set()
        self.submodules = []
        for record in r.stdout.split(b"\0"):
            if record:
                meta, _, path = record.partition(b"\t")
                path = os.fsdecode(path)
                if meta.split(b" ")[0] == b"160000":
                    self.submodules.append(path)
                else:
                    paths.add(path)
        self.tracked = set(paths)
        r = git(root, "ls-files", "-z", "--others", "--exclude-standard")
        paths.update(os.fsdecode(p) for p in r.stdout.split(b"\0") if p)
        self.nested = sorted(p for p in paths if p.endswith("/"))  # another repository inside this one
        self.undecodable = sorted(p for p in paths if not _is_utf8(p))
        self.paths = sorted(paths - set(self.nested) - set(self.undecodable))
        self.missing = [p for p in self.paths if not os.path.lexists(self.abs(p))]

    def abs(self, path):
        return os.path.join(self.root, *path.split("/"))

    def read(self, path):
        """A regular file's bytes; a link, a device, a pipe or a file past MAX_BYTES raises OSError."""
        with open(self.abs(path), "rb", opener=lambda p, flags: os.open(p, flags | getattr(os, "O_NOFOLLOW", 0))) as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise OSError(f"{path}: not a regular file")
            if st.st_size > MAX_BYTES:
                raise OSError(f"{path}: {st.st_size:,} bytes, past the {MAX_BYTES:,} this check reads")
            return f.read(MAX_BYTES + 1)

    def under(self, directory):
        prefix = directory.rstrip("/") + "/"
        return [p for p in self.paths if p.startswith(prefix)]

    def disk_tree(self, directory):
        """(tree id, file count) of a directory as it is on disk, over the files git lists in it."""
        prefix = directory.rstrip("/") + "/"
        files = {p[len(prefix):]: gitobj.file_entry(self.abs(p)) for p in self.under(directory) if p not in self.missing}
        return gitobj.tree_from_entries(files), len(files)


def _is_utf8(path):
    try:
        path.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


class Checker:
    def __init__(self, root, require_source=False, all_legacy_hashes=False, write_ledger=False,
                 sample=LEGACY_HASH_SAMPLE, must_cover=yamlsubset.FEATURES):
        self.tree = Tree(root)
        self.require_source = require_source
        self.all_legacy_hashes = all_legacy_hashes
        self.write_ledger = write_ledger
        self.sample = sample          # legacy tests, relative to legacy/GeneralStateTests
        self.must_cover = must_cover  # the YAML features the sample's fillers must use
        self.docs = {}                # parsed JSON outside the legacy suite's tests and fillers
        self.unparsed = set()         # files that did not parse, reported by `files`
        self.legacy_sources = {}      # legacy test file -> {test name: _info.source}
        self.tests_by_path = {}       # a suite's test file -> the names of its tests
        self.hashes = {}              # content hash -> [(suite, path, test)]
        self.fixtures = {}            # ETC fixture path -> (rule folder, {test: grade})
        self.disk_ids = {}            # a manifest's carried path -> its id on disk

    def run(self):
        return [self.files(), self.manifests(), self.legacy(), self.legacy_source(), self.legacy_hashes(),
                self.ethereumclassic(), self.suites(), self.ledger()]

    def _record(self, suite, path, doc, result):
        if not _is_tests(doc):
            result.fail(f"{path}: not an object of tests")
            return
        self.tests_by_path[path] = set(doc)
        for name, test in doc.items():
            self.hashes.setdefault(content_hash(test), []).append((suite, path, name))

    # -- every file parses -----------------------------------------------------------------------

    def files(self):
        r = Result("files")
        t = self.tree
        for path in t.missing:
            r.fail(f"{path}: listed by git and missing on disk")
        for path in t.nested:
            r.fail(f"{path}: another git repository inside this checkout")
        for path in t.undecodable:
            r.fail(f"{path!r}: a file name that is not UTF-8")
        for path in t.submodules:
            if not path.startswith("ethereum/upstream/"):
                r.fail(f"{path}: a submodule outside ethereum/upstream/, where pinned upstream repositories sit")
        n_legacy = n_other = n_yaml = 0
        for path in t.paths:
            if path in t.missing:
                continue
            mode = os.lstat(t.abs(path)).st_mode
            if not stat.S_ISREG(mode):
                kind = "a symbolic link" if stat.S_ISLNK(mode) else "not a regular file"
                r.fail(f"{path}: {kind}; this repository holds regular files only")
                self.unparsed.add(path)
                continue
            if path.endswith(".json"):
                lenient = path.startswith("legacy/") and path not in LEGACY_DOCUMENTS
                try:
                    doc = load_json(t.read(path), strict=not lenient)
                except (OSError, ValueError, UnicodeDecodeError, RecursionError) as e:
                    r.fail(f"{path}: does not parse: {e}")
                    self.unparsed.add(path)
                    continue
                if lenient:
                    n_legacy += 1
                else:
                    n_other += 1
                if path.startswith(LEGACY_TESTS + "/"):
                    self._record("legacy", path, doc, r)
                    if _is_tests(doc):
                        self.legacy_sources[path] = {n: (x.get("_info") if isinstance(x.get("_info"), dict)
                                                         else {}).get("source") for n, x in doc.items()}
                elif path.startswith(LEGACY_FILLERS + "/"):
                    pass  # a filler: that it parses is the whole check here
                elif path.startswith("ethereum/") and not path.startswith("ethereum/upstream/") \
                        and path != "ethereum/MANIFEST.json":
                    self._record("ethereum", path, doc, r)
                else:
                    self.docs[path] = doc
            elif path.startswith("legacy/") and path.endswith(".yml"):
                try:
                    yamlsubset.load(t.read(path).decode("utf-8"))
                except (OSError, yamlsubset.YAMLSubsetError, UnicodeDecodeError, RecursionError) as e:
                    r.fail(f"{path}: does not parse: {e}")
                    self.unparsed.add(path)
                    continue
                n_yaml += 1
        r.summary = (f"{len(t.paths):,} files listed; JSON read: {n_legacy:,} legacy tests and fillers, "
                     f"{n_other:,} other; YAML fillers read: {n_yaml:,}"
                     + (f"; {len(t.submodules)} submodules under ethereum/upstream/, not read" if t.submodules else ""))
        return r

    # -- manifests -------------------------------------------------------------------------------

    def _conversion_note(self, target):
        """Why ids computed from disk can differ from the committed ones under `target`, or None: an
        attribute or a setting under which git changes files as it checks them out. git reports the
        attributes in force, so a `.gitattributes` that turns conversion off is not named."""
        root = self.tree.root
        sample = next((p for p in self.tree.under(target) if p not in self.tree.missing), target)
        out = git(root, "check-attr", "text", "eol", "working-tree-encoding", "filter", "ident", "--", sample)
        attrs = {}
        for line in out.stdout.decode(errors="replace").splitlines():
            parts = line.rsplit(": ", 2)
            if len(parts) == 3:
                attrs[parts[1]] = parts[2]

        def config(key):
            return git(root, "config", "--get", key).stdout.decode().strip().lower()

        causes = []
        if attrs.get("text") != "unset":
            autocrlf = config("core.autocrlf")
            if autocrlf in ("true", "input"):
                causes.append(f"core.autocrlf={autocrlf}")
            if attrs.get("eol", "unspecified") != "unspecified":
                causes.append(f"eol={attrs['eol']}")
        for attr in ("working-tree-encoding", "filter", "ident"):
            if attrs.get(attr, "unspecified") not in ("unspecified", "unset"):
                causes.append(f"{attr}={attrs[attr]}")
        for key in ("core.filemode", "core.symlinks"):
            if config(key) == "false":
                causes.append(f"{key}=false")
        if not causes:
            return None
        return (f"git changes files as it checks them out here ({'; '.join(causes)}), so ids computed from "
                f"disk can differ from the committed ones")

    def manifests(self):
        r = Result("manifests")
        found = [f"{s}/MANIFEST.json" for s in SUITES if f"{s}/MANIFEST.json" in self.docs]
        if LEGACY_MANIFEST not in found:
            r.fail(f"{LEGACY_MANIFEST}: missing or does not parse")
        n = 0
        mismatched = None
        uncommitted = []
        for path in found:
            carried = self.docs[path].get("carried") if isinstance(self.docs[path], dict) else None
            if not isinstance(carried, list) or not carried:
                r.fail(f"{path}: no list of carried paths")
                continue
            for entry in carried:
                target = entry.get("path") if isinstance(entry, dict) else None
                if not isinstance(target, str):
                    r.fail(f"{path}: a carried entry without a path")
                    continue
                n += 1
                recorded = entry.get("tree", entry.get("blob"))
                got = git(self.tree.root, "rev-parse", "--verify", "--quiet", f"HEAD:{target}")
                if got.returncode == 0:
                    committed = got.stdout.decode().strip()
                    if committed != recorded:
                        r.fail(f"{target}: {committed} committed at HEAD, {recorded} recorded")
                else:
                    uncommitted.append(target)
                try:
                    if "tree" in entry:
                        disk, count = self.tree.disk_tree(target)
                        self.disk_ids[target] = disk
                        if disk != entry["tree"]:
                            r.fail(f"{target}: tree {disk} on disk, {entry['tree']} recorded")
                            mismatched = mismatched or target
                        if "files" in entry and count != entry["files"]:
                            r.fail(f"{target}: {count} files on disk, {entry['files']} recorded")
                    elif "blob" in entry:
                        if target not in self.tree.paths or target in self.tree.missing:
                            r.fail(f"{target}: missing")
                            continue
                        disk = gitobj.file_entry(self.tree.abs(target))[1]
                        self.disk_ids[target] = disk
                        if disk != entry["blob"]:
                            r.fail(f"{target}: blob {disk} on disk, {entry['blob']} recorded")
                            mismatched = mismatched or target
                    else:
                        r.fail(f"{path}: {target} records neither a tree nor a blob")
                except (OSError, ValueError) as e:
                    r.fail(f"{target}: cannot be read: {e}")
        note = self._conversion_note(mismatched) if mismatched else None
        if note:
            r.fail(note)
        if uncommitted and self.require_source:
            for target in uncommitted:
                r.fail(f"{target}: not committed at HEAD, so the committed tree was not compared")
        r.summary = (f"{n} carried paths compared with the ids recorded in {len(found)} "
                     f"manifest{'s' if len(found) != 1 else ''}, on disk and at HEAD"
                     + (f" ({len(uncommitted)} not committed)" if uncommitted else ""))
        return r

    # -- the legacy suite ------------------------------------------------------------------------

    def _legacy_manifest(self):
        m = self.docs.get(LEGACY_MANIFEST)
        if not isinstance(m, dict) or not isinstance(m.get("source"), dict) or not isinstance(m.get("carried"), list):
            return None
        return m

    def legacy(self):
        r = Result("legacy")
        m = self._legacy_manifest()
        if m is None:
            r.fail(f"{LEGACY_MANIFEST}: no source or carried list")
            return r
        allowed = set(LEGACY_DOCUMENTS)
        trees = []
        for entry in m["carried"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            if "blob" in entry:
                allowed.add(entry["path"])
                continue
            trees.append(entry["path"])
            left = entry.get("left_out")
            if not isinstance(left, list) or not all(
                    isinstance(e, dict) and isinstance(e.get("path"), str) and isinstance(e.get("reason"), str)
                    and e["path"] and e["reason"] for e in left):
                r.fail(f"{entry['path']}: left_out is not a list of paths with their reasons")
            elif len({e["path"] for e in left}) != len(left):
                r.fail(f"{entry['path']}: left_out names a path twice")
        for path in self.tree.under("legacy"):
            if path not in allowed and not any(path.startswith(t + "/") for t in trees):
                r.fail(f"{path}: not part of the legacy suite's manifest")
        named = set()
        for path, sources in self.legacy_sources.items():
            for name, source in sources.items():
                if not isinstance(source, str) or not source.startswith("src/GeneralStateTestsFiller/"):
                    r.fail(f"{path}: {name} names no filler under src/GeneralStateTestsFiller")
                    continue
                named.add("legacy/" + source)
        present = set(self.tree.under(LEGACY_FILLERS)) - set(self.tree.missing)
        for path in sorted(named - present):
            r.fail(f"{path}: named by a test and absent")
        for path in sorted(present - named):
            r.fail(f"{path}: a filler no test names")
        r.summary = (f"{len(self.legacy_sources):,} test files and {len(present):,} fillers compared with the "
                     f"manifest and with the fillers the tests name")
        return r

    def legacy_source(self):
        """Rebuild each carried tree from the source commit minus the recorded list."""
        r = Result("legacy source")
        m = self._legacy_manifest()
        if m is None:
            r.fail(f"{LEGACY_MANIFEST}: no source or carried list")
            return r
        commit = m["source"].get("commit")
        branch = m["source"].get("preserved_on_branch")
        if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
            r.fail(f"{LEGACY_MANIFEST}: source.commit is not a commit id")
            return r
        if not isinstance(branch, str) or not branch:
            r.fail(f"{LEGACY_MANIFEST}: source.preserved_on_branch is not a branch name")
            return r
        root = self.tree.root
        if git(root, "cat-file", "-e", commit + "^{commit}").returncode:
            message = (f"the source commit {commit[:12]} is not in this clone; fetch the branch "
                       f"{branch} to run it")
            if self.require_source:
                r.fail(message)
            else:
                r.not_run = message
            return r
        refs_checked = []
        for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
            got = git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
            if got.returncode == 0:
                refs_checked.append(ref)
                if got.stdout.decode().strip() != commit:
                    r.fail(f"{ref} is at {got.stdout.decode().strip()[:12]}, not the recorded {commit[:12]}")
            elif self.require_source and ref.startswith("refs/remotes/"):
                r.fail(f"{ref} is not in this clone, so the branch was not read")
        named = {s for sources in self.legacy_sources.values() for s in sources.values() if isinstance(s, str)}
        for entry in m["carried"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("from"), str) \
                    or not isinstance(entry.get("path"), str):
                r.fail(f"{LEGACY_MANIFEST}: a carried entry without its path and source path")
                continue
            got = git(root, "rev-parse", "--verify", "--quiet", f"{commit}:{entry['from']}")
            source_id = got.stdout.decode().strip() if got.returncode == 0 else None
            if source_id is None:
                r.fail(f"{entry['from']}: not in the source commit")
                continue
            if "blob" in entry:
                if source_id != entry["blob"]:
                    r.fail(f"{entry['path']}: the source has blob {source_id}, the manifest {entry['blob']}")
                disk = self.disk_ids.get(entry["path"])
                if disk is not None and disk != source_id:
                    r.fail(f"{entry['path']}: blob {disk} on disk, {source_id} in the source")
                continue
            if source_id != entry.get("from_tree"):
                r.fail(f"{entry['from']}: the source has tree {source_id}, the manifest {entry.get('from_tree')}")
            listing = git(root, "ls-tree", "-r", "-z", source_id)
            if listing.returncode:
                r.fail(f"{entry['from']}: git ls-tree failed: {listing.stderr.decode(errors='replace').strip()}")
                continue
            try:
                files = gitobj.parse_ls_tree(listing.stdout)
            except ValueError as e:
                r.fail(f"{entry['from']}: {e}")
                continue
            left = {e["path"] for e in entry.get("left_out", []) if isinstance(e, dict) and isinstance(e.get("path"), str)}
            for path in sorted(left - set(files)):
                r.fail(f"{entry['from']}/{path}: recorded as left out, and not in the source")
            rebuilt = gitobj.tree_from_entries({p: v for p, v in files.items() if p not in left})
            if rebuilt != entry.get("tree"):
                r.fail(f"{entry['path']}: the source minus the recorded list is {rebuilt}, the manifest {entry.get('tree')}")
            disk = self.disk_ids.get(entry["path"])
            if disk is not None and disk != rebuilt:
                r.fail(f"{entry['path']}: {disk} on disk, {rebuilt} rebuilt from the source")
            if entry["path"] == LEGACY_FILLERS:
                prefix = entry["from"] + "/"
                by_name = gitobj.tree_from_entries({p: v for p, v in files.items() if prefix + p in named})
                if by_name != rebuilt:
                    r.fail(f"{entry['path']}: the fillers the tests name make {by_name}, the recorded list {rebuilt}")
        branch_note = f"; {branch} read" if refs_checked else "; no branch ref in this clone"
        r.summary = f"compared with {commit[:12]} minus the recorded list{branch_note}"
        return r

    def legacy_hashes(self):
        r = Result("legacy hashes")
        if self.all_legacy_hashes:
            paths = sorted(self.legacy_sources)
        else:
            paths = [f"{LEGACY_TESTS}/{p}" for p in self.sample]
            for path in paths:
                if path not in self.legacy_sources:
                    r.fail(f"{path}: in the sample and not a parsed legacy test; update LEGACY_HASH_SAMPLE")
        features = set()
        json_repeating = False
        tests = 0
        for path in paths:
            if path not in self.legacy_sources:
                continue
            try:
                doc = retesteth.from_json(self.tree.read(path).decode("utf-8"))
            except (OSError, ValueError, UnicodeDecodeError) as e:
                r.fail(f"{path}: {e}")
                continue
            for name, test in doc.items():
                tests += 1
                info = test.get("_info", {})
                source = info.get("source")
                if not isinstance(source, str):
                    r.fail(f"{path}: {name} names no filler")
                    continue
                try:
                    if retesteth.generated_test_hash(name, test) != retesteth.normalize(str(info.get("generatedTestHash", ""))):
                        r.fail(f"{path}: {name}'s generatedTestHash does not recompute")
                except ValueError as e:
                    r.fail(f"{path}: {name}: {e}")
                filler = "legacy/" + source
                if filler not in self.tree.paths or filler in self.tree.missing or filler in self.unparsed:
                    r.fail(f"{path}: {name}'s filler {filler} is absent or does not parse")
                    continue
                try:
                    data = self.tree.read(filler)
                    got = retesteth.source_hash(source, data)
                except (OSError, ValueError, UnicodeDecodeError) as e:
                    r.fail(f"{filler}: {e}")
                    continue
                if got != retesteth.normalize(str(info.get("sourceHash", ""))):
                    r.fail(f"{path}: {name}'s sourceHash does not recompute from {filler}")
                if not self.all_legacy_hashes:
                    if source.endswith(".yml"):
                        features |= yamlsubset.parse(data.decode("utf-8"))[1]
                    else:
                        try:
                            load_json(data, strict=True)
                        except ValueError:
                            json_repeating = True
        if self.all_legacy_hashes:
            r.summary = f"sourceHash and generatedTestHash recomputed for every test, {tests:,}"
            return r
        missing = sorted(self.must_cover - features)
        if missing:
            r.fail(f"the sample's YAML fillers no longer use: {', '.join(missing)}")
        if self.must_cover and not json_repeating:
            r.fail("the sample holds no JSON filler that repeats a key")
        r.summary = (f"sourceHash and generatedTestHash recomputed for a sample of {tests} tests, whose fillers "
                     f"must use {len(self.must_cover)} YAML features (--legacy-hashes: every test)")
        return r

    # -- the Ethereum Classic suite --------------------------------------------------------------

    def ethereumclassic(self):
        r = Result("ethereumclassic")
        for path in self.tree.under("ethereumclassic"):
            parts = path.split("/")
            if path in (LEDGER, RULES) or path.endswith(".md"):
                continue
            if len(parts) != 4 or not path.endswith(".json"):
                r.fail(f"{path}: not a fixture at ethereumclassic/<format>/<rule>/<file>.json, the ledger or a document")
                continue
            _, fmt, folder, _ = parts
            if not _FORMAT.fullmatch(fmt):
                r.fail(f"{path}: the format folder {fmt!r} is not a lowercase name")
            if ledger.rule_of_folder(folder) is None:
                r.fail(f"{path}: the rule folder {folder!r} is not named for an EIP, ECIP or ECBP (eip3855_push0)")
            if path in self.unparsed:
                continue  # `files` reports it
            doc = self.docs.get(path)
            if not _is_tests(doc):
                r.fail(f"{path}: not an object of tests")
                continue
            self.tests_by_path[path] = set(doc)
            graded = {}
            for name, test in doc.items():
                self.hashes.setdefault(content_hash(test), []).append(("ethereumclassic", path, name))
                info = test.get("_info")
                if not isinstance(info, dict):
                    r.fail(f"{path}: {name} has no _info")
                    continue
                grade = info.get("classification")
                if grade not in ADMITTED:
                    r.fail(f"{path}: {name}'s classification is {grade!r}; this suite admits only "
                           f"{' and '.join(ADMITTED)}")
                for key in ("oracle", "source"):
                    if not isinstance(info.get(key), str) or not info[key].strip():
                        r.fail(f"{path}: {name} does not state its {key}")
                if grade == "partial" and (not isinstance(info.get("secondCheck"), str) or not info["secondCheck"].strip()):
                    r.fail(f"{path}: {name} is partial and names no secondCheck")
                recorded = info.get("contentHash")
                if not isinstance(recorded, str) or not _HEX64.fullmatch(recorded):
                    r.fail(f"{path}: {name} records no contentHash (64 lowercase hex)")
                elif recorded != content_hash(test):
                    r.fail(f"{path}: {name}'s contentHash does not recompute")
                graded[name] = grade
            self.fixtures[path] = (folder, graded)
        if not self.fixtures and not r.failures:
            r.not_run = "no fixture yet"
        r.summary = f"{len(self.fixtures)} fixture files: grade, oracle, source and content hash read"
        return r

    def suites(self):
        r = Result("suites")
        for digest, where in sorted(self.hashes.items()):
            if len({suite for suite, _, _ in where}) > 1:
                listed = "; ".join(f"{path}: {name}" for _, path, name in where)
                r.fail(f"one test in two suites, content hash {digest[:12]}: {listed}")
        counts = {}
        for where in self.hashes.values():
            for suite, _, _ in where:
                counts[suite] = counts.get(suite, 0) + 1
        r.summary = "content hashes compared across suites: " + ", ".join(
            f"{counts.get(s, 0):,} {s}" for s in SUITES)
        return r

    def ledger(self):
        r = Result("ledger")
        t = self.tree
        has_ledger = LEDGER in t.paths and LEDGER not in t.missing
        has_rules = RULES in t.paths and RULES not in t.missing
        if not has_ledger and not self.write_ledger:
            if self.fixtures:
                r.fail(f"fixtures exist and {LEDGER} does not")
            elif has_rules:
                r.fail(f"{RULES} exists and {LEDGER} does not; run --write-ledger")
            else:
                r.not_run = "no ledger yet"
            return r
        if not has_rules:
            r.fail(f"{RULES} is missing, so the ledger cannot be regenerated")
            return r
        if RULES in self.unparsed or LEDGER in self.unparsed:
            r.fail("the ledger's files do not parse; `files` says how")
            return r
        rules = self.docs.get(RULES)
        committed = None
        if has_ledger:
            committed = self.docs.get(LEDGER)
            if not isinstance(committed, dict):
                r.fail(f"{LEDGER}: not an object")
                return r
        problems = ledger.check_rules(rules)
        if problems:
            for p in problems:
                r.fail(p)
            return r
        text, problems = ledger.regenerate(rules, self.fixtures, committed)
        for p in problems:
            r.fail(p)
        for entry in json.loads(text)["rules"]:
            for issue in entry["knownIssues"]:
                path, name = issue["path"], issue["test"]
                if path.split("/", 1)[0] not in SUITES:
                    continue  # a test outside this repository, cited
                if path not in self.tests_by_path:
                    r.fail(f"{LEDGER}: {entry['rule']}'s known issue names {path}, which holds no test here")
                elif name not in self.tests_by_path[path]:
                    r.fail(f"{LEDGER}: {entry['rule']}'s known issue names {name} in {path}, which holds no such test")
                elif path in self.fixtures and ledger.rule_of_folder(self.fixtures[path][0]) != entry["rule"]:
                    r.fail(f"{LEDGER}: {path} tests {ledger.rule_of_folder(self.fixtures[path][0])}, and its known "
                           f"issue is filed under {entry['rule']}")
        if self.write_ledger:
            if os.path.islink(t.abs(LEDGER)):
                r.fail(f"{LEDGER} is a symbolic link; not writing through it")
                return r
            with open(t.abs(LEDGER), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            r.summary = f"written: {LEDGER}"
            return r
        if t.read(LEDGER).decode("utf-8") != text:
            r.fail(f"{LEDGER} is not what tools/ regenerates; run --write-ledger and review the difference")
        r.summary = f"{LEDGER} regenerated and compared with what is committed"
        return r

"""The release builder, on a small repository built for each test: a sound tree builds one file every time, and
each test below breaks one property and asserts the build, the read or the comparison is refused."""

import contextlib
import functools
import hashlib
import io
import itertools
import os
import shutil
import signal
import struct
import tarfile
import tempfile
import unittest
import zlib
from unittest import mock

import checks
import release
import releases
from tests import repo

TAG = "legacy/v0.0.1"
NAME = "core-geth-tests-legacy-v0.0.1.tar.gz"
TEST_ONE = "legacy/GeneralStateTests/stA/one.json"
FILES = ["legacy/GeneralStateTests/stA/one.json", "legacy/GeneralStateTests/stA/two.json", "legacy/LICENSE"]


def forged_header(path, size, kind=b"0", mode=b"0000644\0"):
    """A USTAR header this tool would not write: another type or mode, with a valid checksum."""
    block = releases.header(path, size)
    block = block[:100] + mode + block[108:148] + b" " * 8 + kind + block[157:]
    return block[:148] + b"%06o\0 " % sum(block) + block[156:]


def tar_of(*entries):
    """A tar from (header, data) pairs, ended and padded as this tool ends one."""
    out = b"".join(h + d + b"\0" * (-len(d) % releases.BLOCK) for h, d in entries) + b"\0" * (2 * releases.BLOCK)
    return out + b"\0" * (-len(out) % releases.RECORD)


def no_checks(root):
    return []


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        built = repo.Repo()
        built.__enter__()
        self.addCleanup(built.__exit__, None, None, None)
        self.repo, self.root = built, built.root
        self.out = tempfile.mkdtemp(prefix="core-geth-tests-out-")
        self.addCleanup(shutil.rmtree, self.out)
        # The builder's own checks, with the test repository's sample of legacy hashes.
        sampled = functools.partial(checks.Checker, sample=("stA/one.json", "stA/two.json"), must_cover=frozenset())
        patcher = mock.patch.object(checks, "Checker", sampled)
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, path):
        return os.path.join(self.root, *path.split("/"))

    def build(self, tag=TAG, root=None):
        with repo.hermetic():
            return releases.build(root or self.root, tag)

    def refused(self, tag=TAG):
        with self.assertRaises(releases.Refused) as cm:
            self.build(tag)
        return cm.exception

    def commit(self):
        repo.git(self.root, "add", "-A")
        repo.git(self.root, "commit", "-q", "-m", "an edit")

    def edit(self, path, old, new):
        with open(self.path(path), "rb") as f:
            data = f.read()
        self.assertIn(old, data)
        with open(self.path(path), "wb") as f:
            f.write(data.replace(old, new, 1))

    def head_blob(self, path):
        return repo.git(self.root, "cat-file", "blob", f"HEAD:{path}").encode()

    def main(self, *argv):
        out = io.StringIO()
        with repo.hermetic(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = release.main([*argv, "--root", self.root])
        return code, out.getvalue()

    # -- a sound tree ----------------------------------------------------------------------------

    def test_a_clean_tree_builds_its_tests_and_license(self):
        built = self.build()
        self.assertEqual(built.name, NAME)
        back = releases.entries(releases.inflate(built.data), "legacy")
        self.assertEqual([p for p, _ in back], FILES)
        for path, data in back:
            self.assertEqual(data, self.head_blob(path))
        self.assertEqual([(p, k) for p, k, _ in built.ids], [("legacy/GeneralStateTests", "tree"), ("legacy/LICENSE", "blob")])

    def test_python_reads_every_entry_as_a_regular_file(self):
        built = self.build()
        self.assertEqual(built.data[:10], bytes.fromhex("1f8b08000000000002ff"))
        with tarfile.open(fileobj=io.BytesIO(built.data), mode="r:gz") as tf:
            members = tf.getmembers()
            self.assertEqual([m.name for m in members], FILES)
            for m in members:
                self.assertTrue(m.isreg())
                self.assertEqual((m.mode, m.uid, m.gid, m.uname, m.gname, m.mtime), (0o644, 0, 0, "", "", 0))
                self.assertEqual(tf.extractfile(m).read(), built.files[m.name])

    def test_the_tar_is_written_field_by_field(self):
        """The same files give these bytes on any machine. The second long path splits two ways within USTAR's
        limits, and is split as Python's tarfile splits it: at the first "/" that fits."""
        one = "legacy/GeneralStateTests/" + "d" * 80 + "/" + "f" * 60 + ".json"
        two = "legacy/GeneralStateTests/" + "s" * 30 + "/" + "t" * 50 + ".json"
        tar = releases.pack({"legacy/GeneralStateTests/a.json": b"{}\n", "legacy/LICENSE": b"x\n", one: b"1", two: b"22"})
        self.assertEqual(hashlib.sha256(tar).hexdigest(), "f773846edd68a69345d44f1dd9a3b65c7108740e109457b2b35ffdc0d0bec40b")
        self.assertEqual([p for p, _ in releases.entries(tar, "legacy")][1:3], [one, two])

    def test_it_is_compressed_at_level_9(self):
        """Deflate at level 9, memory level 8 and the default strategy: the parameters a rebuild must match. The
        second input is long and varied enough that another memory level or strategy changes the output."""
        built = self.build()
        varied = b"".join(b"%d:%x;" % (i, (i * 2654435761) % 4294967296) for i in range(60000))
        for data in (built.tar, varied):
            c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS, 8, zlib.Z_DEFAULT_STRATEGY)
            self.assertEqual(releases.compress(data)[10:-8], c.compress(data) + c.flush())

    # -- one file every time ---------------------------------------------------------------------

    def test_two_builds_and_a_fresh_clone_give_one_file(self):
        first, second = self.build(), self.build()
        self.assertEqual(first.data, second.data)
        clone = tempfile.mkdtemp(prefix="core-geth-tests-clone-")
        self.addCleanup(shutil.rmtree, clone)
        repo.git(self.root, "clone", "-q", "--no-local", self.root, clone)
        self.assertEqual(self.build(root=clone).sha256, first.sha256)

    def test_refuses_two_builds_that_differ(self):
        counter, pack = itertools.count(), releases.pack
        with mock.patch.object(releases, "pack", lambda files: pack(files) + b"\0" * releases.RECORD * next(counter)):
            self.assertIn("two builds", str(self.refused()))

    def test_refuses_a_file_that_does_not_read_back(self):
        entries = releases.entries
        with mock.patch.object(releases, "entries", lambda tar, suite: entries(tar, suite)[:-1]):
            self.assertIn("does not read back", str(self.refused()))

    # -- from the commit, never the disk ---------------------------------------------------------

    def test_reads_the_commit_and_not_the_disk(self):
        """Line endings converted on checkout make the disk differ from HEAD while git reports no change; the
        checks would refuse that, so they are set aside here to show the builder itself reads HEAD."""
        repo.git(self.root, "config", "core.autocrlf", "true")
        os.remove(self.path(TEST_ONE))
        repo.git(self.root, "checkout", "--", TEST_ONE)
        with open(self.path(TEST_ONE), "rb") as f:
            self.assertIn(b"\r\n", f.read())
        with mock.patch.object(releases, "run_checks", no_checks):
            built = self.build()
        self.assertEqual(built.files[TEST_ONE], self.head_blob(TEST_ONE))
        self.assertNotIn(b"\r\n", built.files[TEST_ONE])

    def test_refuses_files_git_is_told_to_skip(self):
        """With a file's change hidden from git status, the checks would read the disk while the build reads HEAD."""
        for flag in ("--skip-worktree", "--assume-unchanged"):
            with self.subTest(flag):
                repo.git(self.root, "update-index", flag, "legacy/README.md")
                repo.write(self.root, "legacy/README.md", "changed on disk\n")
                self.assertEqual(repo.git(self.root, "status", "--porcelain"), "")
                self.assertIn("told to skip", str(self.refused()))
                repo.git(self.root, "update-index", flag.replace("--", "--no-"), "legacy/README.md")
                repo.git(self.root, "checkout", "--", "legacy/README.md")

    def test_ignores_replace_refs(self):
        """A replace ref would have git read another tree for the commit the file is said to come from."""
        head = repo.git(self.root, "rev-parse", "HEAD").strip()
        self.edit(TEST_ONE, b'"0x01"', b'"0x02"')
        repo.git(self.root, "add", "-A")
        other = repo.git(self.root, "commit-tree", repo.git(self.root, "write-tree").strip(), "-m", "other").strip()
        repo.git(self.root, "reset", "-q", "--hard", head)
        repo.git(self.root, "replace", head, other)
        with mock.patch.object(releases, "run_checks", no_checks):
            built = self.build()
        self.assertEqual(built.commit, head)
        self.assertIn(b'"0x01"', built.files[TEST_ONE])

    # -- every entry a regular file --------------------------------------------------------------

    def test_refuses_a_suite_file_that_is_not_a_regular_file(self):
        """The builder's own guard, with every check passing: check.py refuses a link as well."""
        with mock.patch.object(releases, "run_checks", no_checks):
            os.symlink("one.json", self.path("legacy/GeneralStateTests/stA/link.json"))
            self.commit()
            self.assertIn("not a regular file", str(self.refused()))
            repo.git(self.root, "rm", "-q", "legacy/GeneralStateTests/stA/link.json")
            os.makedirs(self.path("legacy/GeneralStateTests/stB"))  # as a submodule not yet checked out
            repo.git(self.root, "update-index", "--add", "--cacheinfo",
                     f"160000,{self.repo.commit},legacy/GeneralStateTests/stB")
            repo.git(self.root, "commit", "-q", "-m", "a submodule")
            self.assertIn("160000 commit, not a regular file", str(self.refused()))

    def test_refuses_a_listed_file_that_is_a_directory_at_the_commit(self):
        """legacy/LICENSE is one file in the suite's list; a commit where it is a directory is refused by name."""
        with mock.patch.object(releases, "run_checks", no_checks):
            os.remove(self.path("legacy/LICENSE"))
            repo.write(self.root, "legacy/LICENSE/x.txt", "x\n")
            self.commit()
            self.assertIn("not one of the suite's files", str(self.refused()))

    def test_reading_refuses_an_entry_that_is_not_a_regular_file(self):
        for kind in (b"1", b"2", b"5", b"\0"):
            with self.subTest(kind):
                tar = tar_of((forged_header("legacy/LICENSE", 0, kind=kind), b""))
                with self.assertRaises(releases.Refused) as cm:
                    releases.entries(tar, "legacy")
                self.assertIn("not a regular file", str(cm.exception))

    # -- every path inside the suite -------------------------------------------------------------

    def test_reading_refuses_a_path_outside_the_suite(self):
        for path in ("legacy/README.md", "legacy/src/GeneralStateTestsFiller/stA/oneFiller.json",
                     "legacy/GeneralStateTests", "../legacy/LICENSE", "/legacy/LICENSE", "legacy//LICENSE",
                     "legacy/GeneralStateTests/../LICENSE", "legacy/GeneralStateTests/./a.json"):
            with self.subTest(path):
                with self.assertRaises(releases.Refused) as cm:
                    releases.entries(releases.pack({path: b"x"}), "legacy")
                self.assertRegex(str(cm.exception), "outside the legacy suite|not a relative path")

    # -- a changed file --------------------------------------------------------------------------

    def test_refuses_uncommitted_changes(self):
        original = self.head_blob(TEST_ONE)
        self.edit(TEST_ONE, b'"0x01"', b'"0x02"')
        self.assertIn("uncommitted", str(self.refused()))
        repo.git(self.root, "add", TEST_ONE)
        self.assertIn("uncommitted", str(self.refused()))
        repo.git(self.root, "reset", "-q")
        repo.write(self.root, TEST_ONE, original)
        repo.write(self.root, "stray.txt", "x\n")
        self.assertIn("stray.txt", "\n".join(self.refused().details))
        os.remove(self.path("stray.txt"))
        repo.write(self.root, ".gitignore", "scratch/\n")
        self.commit()
        repo.write(self.root, "scratch/x.txt", "an ignored file\n")
        self.assertEqual(self.build().name, NAME)

    def test_refuses_a_tree_a_check_fails(self):
        self.edit(TEST_ONE, b'"0x01"', b'"0x02"')
        self.commit()
        e = self.refused()
        self.assertIn("a check fails", str(e))
        self.assertTrue(any(d.startswith("manifests:") for d in e.details))

    def test_refuses_without_the_legacy_suites_source(self):
        repo.git(self.root, "update-ref", "-d", f"refs/remotes/origin/{repo.BRANCH}")
        e = self.refused()
        self.assertIn("a check fails", str(e))
        self.assertTrue(any(d.startswith("legacy source:") for d in e.details))

    def test_refuses_ids_its_manifest_does_not_record(self):
        """The builder's own comparison with the manifest, with every check passing."""
        with mock.patch.object(releases, "run_checks", no_checks):
            self.edit(TEST_ONE, b'"0x01"', b'"0x02"')
            self.commit()
            self.assertIn("recorded in legacy/MANIFEST.json", str(self.refused()))

    def test_refuses_a_manifest_source_the_notes_would_misquote(self):
        """The source's repository and commit reach the notes; a backtick or a line break could close a code span and
        put a link on the release page. With every check passing, the builder's own guard refuses them."""
        cases = {"source.repository": ("repository", "example/tests` **[verify here](https://example.invalid)** `x"),
                 "source.commit": ("commit", "0" * 39 + "\n")}
        for why, (key, value) in cases.items():
            with self.subTest(why), mock.patch.object(releases, "run_checks", no_checks):
                m = repo.read_json(self.root, "legacy/MANIFEST.json")
                kept = m["source"][key]
                m["source"][key] = value
                repo.write_json(self.root, "legacy/MANIFEST.json", m)
                self.commit()
                self.assertIn(why, str(self.refused()))
                m["source"][key] = kept
                repo.write_json(self.root, "legacy/MANIFEST.json", m)
                self.commit()

    def test_a_missing_blob_leaves_the_checkout_unreadable(self):
        oid = repo.git(self.root, "rev-parse", f"HEAD:{TEST_ONE}").strip()
        os.remove(os.path.join(self.root, ".git", "objects", oid[:2], oid[2:]))
        with mock.patch.object(releases, "run_checks", no_checks), self.assertRaises(checks.CheckError):
            self.build()

    def test_compare_tells_a_changed_file_from_other_compression(self):
        built = self.build()
        self.assertEqual(releases.compare(built, built.data), "bytes")
        c = zlib.compressobj(1, zlib.DEFLATED, -zlib.MAX_WBITS, 8, zlib.Z_DEFAULT_STRATEGY)
        other = (releases.GZIP_HEADER + c.compress(built.tar) + c.flush()
                 + struct.pack("<II", zlib.crc32(built.tar), len(built.tar)))
        self.assertNotEqual(other, built.data)
        self.assertEqual(releases.compare(built, other), "tar")
        files = dict(built.files)
        files[TEST_ONE] = files[TEST_ONE].replace(b'"0x01"', b'"0x02"', 1)
        with self.assertRaises(releases.Refused) as cm:
            releases.compare(built, releases.compress(releases.pack(files)))
        self.assertEqual(cm.exception.details, [f"{TEST_ONE}: differs, or is in one only"])
        with self.assertRaises(releases.Refused) as cm:
            releases.compare(built, releases.compress(built.tar + b"\0" * releases.RECORD))
        self.assertIn("inflates past", str(cm.exception))

    def test_reading_refuses_what_this_tool_does_not_write(self):
        built = self.build()
        tar = built.tar
        cases = {
            "data after the gzip stream": built.data + b"\0",
            "cut short": built.data[:-4],
            "another gzip header": built.data[:4] + b"\x01" + built.data[5:],
            "data after the archive": releases.compress(tar + b"x" + b"\0" * (releases.RECORD - 1)),
            "no end": releases.compress(tar[:releases.BLOCK + releases.BLOCK]),
            "another mode": releases.compress(tar_of((forged_header("legacy/LICENSE", 0, mode=b"0000755\0"), b""))),
        }
        for why, data in cases.items():
            with self.subTest(why):
                with self.assertRaises(releases.Refused):
                    releases.entries(releases.inflate(data), "legacy")

    # -- the tag and the files written -----------------------------------------------------------

    def test_refuses_a_tag_it_does_not_know(self):
        for tag in ("legacy/v1", "legacy/v0.0.01", "legacy/v0.0.1.0", "ethereum/v0.0.1", "legacy/v0.0.1/x",
                    "legacy-v0.0.1", "legacy/v0.0.1\n", "legacy/v０.0.1"):
            with self.subTest(tag):
                self.assertIn("not a release tag", str(self.refused(tag)))

    def test_refuses_a_tag_at_another_commit(self):
        first = repo.git(self.root, "rev-parse", "HEAD").strip()
        repo.write(self.root, "notes.md", "x\n")
        self.commit()
        repo.git(self.root, "tag", TAG, first)
        self.assertIn(f"check out {TAG}", str(self.refused()))
        repo.git(self.root, "tag", "-f", TAG, "HEAD^{tree}")
        self.assertIn("does not name a commit", str(self.refused()))
        repo.git(self.root, "tag", "-f", "-a", "-m", "the release", TAG, "HEAD")
        self.assertEqual(self.build().commit, repo.git(self.root, "rev-parse", "HEAD").strip())

    def test_writes_the_file_its_sha256_and_notes_and_never_over_a_file(self):
        built = self.build()
        notes = os.path.join(self.out, "notes", "notes.md")
        os.makedirs(os.path.dirname(notes))
        with repo.hermetic():
            written = releases.write(built, self.root, os.path.join(self.out, "release"), notes)
        with open(written[0], "rb") as f:
            self.assertEqual(f.read(), built.data)
        with open(written[1]) as f:
            self.assertEqual(f.read(), f"{built.sha256}  {NAME}\n")
        with open(notes) as f:
            text = f.read()
        for fact in (built.commit, built.checksum_line().strip(), f"--check ../{NAME}", f"--dir ..", self.repo.commit,
                     f"zlib {zlib.ZLIB_RUNTIME_VERSION}", f"git merge-base --is-ancestor {TAG} origin/main"):
            self.assertIn(fact, text)
        with self.assertRaises(releases.Refused) as cm, repo.hermetic():
            releases.write(built, self.root, os.path.join(self.out, "release"))
        self.assertIn("exists; nothing is written over a file", str(cm.exception))
        with open(written[0], "rb") as f:
            self.assertEqual(f.read(), built.data)
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "release"))), [NAME, NAME + ".sha256"])

    def test_a_write_that_fails_leaves_nothing(self):
        built = self.build()
        out = os.path.join(self.out, "release")
        with self.assertRaises(releases.Refused) as cm, repo.hermetic():
            releases.write(built, self.root, out, os.path.join(self.out, "no-such-directory", "notes.md"))
        self.assertIn("its directory does not exist", str(cm.exception))
        self.assertEqual(os.listdir(out), [])
        with self.assertRaises(releases.Refused) as cm, repo.hermetic():
            releases.write(built, self.root, out, os.path.join(out, NAME))
        self.assertIn("named for two files", str(cm.exception))
        place, calls = releases._place, []

        def second_fails(target, data):
            calls.append(target)
            if len(calls) == 2:
                raise OSError(27, "File too large")
            place(target, data)

        with mock.patch.object(releases, "_place", second_fails), self.assertRaises(releases.Refused), repo.hermetic():
            releases.write(built, self.root, out)
        self.assertEqual((len(calls), os.listdir(out)), (2, []))
        with mock.patch.object(releases.os, "fsync", side_effect=OSError(5, "Input/output error")), \
                self.assertRaises(releases.Refused), repo.hermetic():
            releases.write(built, self.root, out)
        self.assertEqual(os.listdir(out), [])

    def test_writes_nothing_the_checkout_would_list(self):
        built = self.build()
        with self.assertRaises(releases.Refused), repo.hermetic():
            releases.write(built, self.root, os.path.join(self.root, "dist"))
        self.assertFalse(os.path.exists(self.path("dist")))
        repo.write(self.root, ".gitignore", "dist/\n")
        with repo.hermetic():
            releases.write(built, self.root, os.path.join(self.root, "dist"))
        self.assertTrue(os.path.exists(self.path(f"dist/{NAME}")))

    # -- the command -----------------------------------------------------------------------------

    def test_the_command_builds_and_checks(self):
        code, text = self.main(TAG, "--out", os.path.join(self.out, "a"))
        self.assertEqual(code, 0, text)
        built = self.build()
        for line in (f"built    {NAME}", f"sha256   {built.sha256}", f"size     {len(built.data):,} bytes"):
            self.assertIn(line, text)
        code, text = self.main(TAG, "--check", os.path.join(self.out, "a", NAME))
        self.assertEqual(code, 0, text)
        self.assertIn("byte for byte", text)
        with open(os.path.join(self.out, "a", NAME), "rb") as f:
            data = f.read()
        with open(os.path.join(self.out, "tampered.tar.gz"), "wb") as f:
            f.write(data[:-12] + bytes([data[-12] ^ 1]) + data[-11:])
        code, text = self.main(TAG, "--check", os.path.join(self.out, "tampered.tar.gz"))
        self.assertEqual(code, 1, text)
        self.assertIn("refused", text)

    def test_check_refuses_a_missing_file_or_a_pipe_before_building(self):
        class Stuck(Exception):
            """Not an OSError, so the command cannot report it as a refusal."""

        def stuck(*_):
            raise Stuck("opening the pipe waited")

        fifo = os.path.join(self.out, "pipe.tar.gz")
        os.mkfifo(fifo)
        previous = signal.signal(signal.SIGALRM, stuck)
        signal.alarm(10)
        try:
            with mock.patch.object(releases, "build", side_effect=AssertionError("built before the file was read")):
                for path in (os.path.join(self.out, "absent.tar.gz"), fifo):
                    with self.subTest(path):
                        code, text = self.main(TAG, "--check", path)
                        self.assertEqual(code, 1, text)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    def test_the_command_starts_no_log_line_with_text_from_a_file_name(self):
        """Both kinds of refusal print a name escaped: an OSError (a missing file) and a refusal (a link)."""
        link = os.path.join(self.out, "b\n::warning::injected.tar.gz")
        os.symlink("elsewhere", link)
        for path, shown in ((os.path.join(self.out, "a\n::warning::injected.tar.gz"), "a\\n::warning::injected"),
                            (link, "b\\n::warning::injected")):
            with self.subTest(shown):
                code, text = self.main(TAG, "--check", path)
                self.assertEqual(code, 1)
                self.assertIn(shown, text)
                self.assertFalse([line for line in text.splitlines() if line.lstrip().startswith("::")])

    def test_the_command_refuses_tracked_bytecode(self):
        repo.write(self.root, "x/__pycache__/m.cpython-312.pyc", b"\x00")
        repo.git(self.root, "add", "-f", "x/__pycache__/m.cpython-312.pyc")
        repo.git(self.root, "commit", "-q", "-m", "bytecode")
        code, text = self.main(TAG, "--out", self.out)
        self.assertEqual(code, 1)
        self.assertIn("bytecode is tracked", text)
        self.assertIn("x/__pycache__/m.cpython-312.pyc", text)

    def test_the_command_refuses_tracked_bytecode_where_its_tools_run_from(self):
        """Run from one checkout to build another, the tools' own checkout is checked too."""
        with repo.Repo() as tools_checkout:
            repo.write(tools_checkout.root, "tools/__pycache__/releases.cpython-312.pyc", b"\x00")
            repo.git(tools_checkout.root, "add", "-f", "tools/__pycache__/releases.cpython-312.pyc")
            with mock.patch.object(release, "TOOLS", os.path.join(tools_checkout.root, "tools")):
                code, text = self.main(TAG, "--out", self.out)
        self.assertEqual(code, 1, text)
        self.assertIn("tools/__pycache__/releases.cpython-312.pyc", text)

    def test_git_variables_do_not_redirect_the_build(self):
        """A hook exports GIT_DIR; the build must still read the checkout --root names, not that repository."""
        with repo.Repo() as other:
            repo.git(other.root, "commit", "-q", "--allow-empty", "-m", "another history")
            self.assertNotEqual(repo.git(other.root, "rev-parse", "HEAD"), repo.git(self.root, "rev-parse", "HEAD"))
            with repo.hermetic(), mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(other.root, ".git"),
                                                                "GIT_WORK_TREE": other.root}):
                built = releases.build(self.root, TAG)
        self.assertEqual(built.commit, repo.git(self.root, "rev-parse", "HEAD").strip())

    def test_the_command_exits_2_outside_a_checkout(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(repo.ENV["HOME"])) as empty:
            self.root = empty
            code, text = self.main(TAG, "--out", self.out)
        self.assertEqual(code, 2, text)


if __name__ == "__main__":
    unittest.main()

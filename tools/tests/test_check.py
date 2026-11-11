"""Each check of `check.py`, run on a small repository built for the test: it passes on a sound tree,
and each test below breaks the tree one way and asserts the check that must catch it fails."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import check
import checks
import gitobj
from tests import repo

TEST_ONE = "legacy/GeneralStateTests/stA/one.json"
FILLER_TWO = "legacy/src/GeneralStateTestsFiller/stA/twoFiller.yml"
FIXTURE = "ethereumclassic/state/eip3855_push0/classic.json"


class CheckTest(unittest.TestCase):
    def setUp(self):
        built = repo.Repo()
        built.__enter__()
        self.addCleanup(built.__exit__, None, None, None)
        self.repo = built
        self.root = built.root

    def run_checks(self, **options):
        return repo.run(self.root, **options)

    def assertStatus(self, results, name, status):
        r = results[name]
        self.assertEqual(r.status, status, f"{name}: {r.failures or r.not_run or r.summary}")

    def path(self, path):
        return os.path.join(self.root, *path.split("/"))

    def edit(self, path, old, new):
        with open(self.path(path), "rb") as f:
            data = f.read()
        self.assertIn(old, data)
        with open(self.path(path), "wb") as f:
            f.write(data.replace(old, new, 1))

    def commit(self):
        repo.git(self.root, "add", "-A")
        repo.git(self.root, "commit", "-q", "-m", "an edit")

    def edit_manifest(self, change):
        m = repo.read_json(self.root, checks.LEGACY_MANIFEST)
        change(m)
        repo.write_json(self.root, checks.LEGACY_MANIFEST, m)

    # -- a sound tree --------------------------------------------------------------------------

    def test_a_sound_tree_passes(self):
        results = self.run_checks(require_source=True)
        for name in ("files", "manifests", "legacy", "legacy source", "legacy hashes", "suites"):
            self.assertStatus(results, name, "PASS")
        self.assertStatus(results, "ethereumclassic", "NOT RUN")
        self.assertStatus(results, "ledger", "NOT RUN")

    def test_every_legacy_hash(self):
        self.assertStatus(self.run_checks(all_legacy_hashes=True), "legacy hashes", "PASS")

    # -- files ---------------------------------------------------------------------------------

    def test_files_fails_on_json_that_does_not_parse(self):
        repo.write(self.root, TEST_ONE, b"{")
        self.assertStatus(self.run_checks(), "files", "FAIL")

    def test_files_fails_on_yaml_outside_the_subset(self):
        for old, new in ((b"storage: {}", b"storage: &s {}"), (b'":raw 0x01"', b'"\\U00110000"')):
            with self.subTest(new):
                self.edit(FILLER_TWO, old, new)
                self.assertStatus(self.run_checks(), "files", "FAIL")
                self.edit(FILLER_TWO, new, old)

    def test_files_fails_on_a_repeated_key_outside_legacy(self):
        repo.write(self.root, "ethereumclassic/RULES.json", b'{"source": {}, "source": {}}\n')
        self.assertStatus(self.run_checks(), "files", "FAIL")

    def test_files_fails_on_a_repeated_key_in_the_legacy_manifest(self):
        text = (json.dumps(repo.read_json(self.root, checks.LEGACY_MANIFEST))[:-1]
                + ', "source": {"commit": "' + "0" * 40 + '"}}')
        repo.write(self.root, checks.LEGACY_MANIFEST, text)
        self.assertStatus(self.run_checks(), "files", "FAIL")

    def test_files_fails_on_a_tracked_file_missing_on_disk(self):
        repo.write(self.root, "notes.md", b"x\n")
        repo.git(self.root, "add", "notes.md")
        os.remove(self.path("notes.md"))
        self.assertStatus(self.run_checks(), "files", "FAIL")

    def test_files_fails_on_a_legacy_test_that_is_not_an_object_of_tests(self):
        repo.write(self.root, TEST_ONE, b"[1]\n")
        results = self.run_checks()
        self.assertStatus(results, "files", "FAIL")
        self.assertStatus(results, "legacy hashes", "FAIL")

    def test_files_fails_on_a_symbolic_link_in_a_suite(self):
        os.symlink("one.json", self.path("legacy/GeneralStateTests/stA/link.json"))
        self.assertStatus(self.run_checks(), "files", "FAIL")

    def test_files_reports_what_it_cannot_read(self):
        """A dangling link, a repository inside the checkout and a name that is not UTF-8 each fail
        `files`, and every other check still reports."""
        os.symlink("nowhere.json", self.path("notes.json"))
        nested = self.path("legacy/GeneralStateTests/stNested")
        os.makedirs(nested)
        repo.git(nested, "init", "-q")
        with open(os.path.join(os.fsencode(self.root), b"bad-\xff.json"), "wb") as f:
            f.write(b"{}\n")
        results = self.run_checks()
        self.assertStatus(results, "files", "FAIL")
        self.assertEqual(len(results), 8)

    def test_files_refuses_links_and_pipes_anywhere(self):
        """git lists no pipe it has not tracked, so the case is a tracked file replaced by one: the
        check must refuse it without opening it, which would wait forever."""
        os.symlink("README.md", self.path("notes.json"))
        repo.write(self.root, "pipe.json", b"{}\n")
        repo.git(self.root, "add", "pipe.json")
        os.remove(self.path("pipe.json"))
        os.mkfifo(self.path("pipe.json"))
        results = self.run_checks()
        failures = "\n".join(results["files"].failures)
        self.assertIn("notes.json: a symbolic link", failures)
        self.assertIn("pipe.json: not a regular file", failures)

    def test_files_refuses_a_file_past_the_cap(self):
        with mock.patch.object(checks, "MAX_BYTES", 16):
            results = self.run_checks()
        self.assertStatus(results, "files", "FAIL")
        self.assertTrue(any("past the 16 this check reads" in f for f in results["files"].failures))

    def test_files_allows_a_submodule_only_under_ethereum_upstream(self):
        repo.git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{self.repo.commit},ethereum/upstream/tests")
        self.assertStatus(self.run_checks(), "files", "PASS")
        repo.git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{self.repo.commit},vendor/tests")
        self.assertStatus(self.run_checks(), "files", "FAIL")

    # -- manifests -----------------------------------------------------------------------------

    def test_manifests_fails_on_one_changed_legacy_byte(self):
        self.edit(TEST_ONE, b'"currentNumber": "0x01"', b'"currentNumber": "0x02"')
        self.assertStatus(self.run_checks(), "manifests", "FAIL")

    def test_manifests_fails_on_a_changed_recorded_hash(self):
        self.edit_manifest(lambda m: m["carried"][0].update(tree="0" * 40))
        self.assertStatus(self.run_checks(), "manifests", "FAIL")

    def test_manifests_fails_on_a_changed_file_count(self):
        self.edit_manifest(lambda m: m["carried"][0].update(files=3))
        self.assertStatus(self.run_checks(), "manifests", "FAIL")

    def test_manifests_fails_on_a_changed_license(self):
        repo.write(self.root, "legacy/LICENSE", b"Another license.\n")
        self.assertStatus(self.run_checks(), "manifests", "FAIL")

    def test_manifests_names_a_checkout_conversion(self):
        repo.git(self.root, "config", "core.autocrlf", "true")
        self.edit(TEST_ONE, b'"currentNumber": "0x01"', b'"currentNumber": "0x02"')
        results = self.run_checks()
        self.assertStatus(results, "manifests", "FAIL")
        self.assertTrue(any("core.autocrlf=true" in f for f in results["manifests"].failures))

    def test_manifests_compares_the_committed_tree(self):
        """The disk can match while the commit does not, as when attributes make a checkout rewrite
        files: commit a changed legacy file, then put the original bytes back on disk."""
        with open(self.path(TEST_ONE), "rb") as f:
            original = f.read()
        self.edit(TEST_ONE, b'"currentNumber": "0x01"', b'"currentNumber": "0x02"')
        self.commit()
        repo.write(self.root, TEST_ONE, original)
        results = self.run_checks()
        self.assertStatus(results, "manifests", "FAIL")
        self.assertTrue(any("committed at HEAD" in f for f in results["manifests"].failures))

    def test_manifests_requires_the_committed_tree_in_ci(self):
        repo.git(self.root, "rm", "-r", "-q", "--cached", "legacy")
        repo.git(self.root, "commit", "-q", "-m", "legacy untracked")
        self.assertStatus(self.run_checks(), "manifests", "PASS")
        self.assertStatus(self.run_checks(require_source=True), "manifests", "FAIL")

    def test_manifests_names_a_converting_attribute_and_not_one_that_stops_conversion(self):
        repo.git(self.root, "config", "core.autocrlf", "true")
        repo.write(self.root, ".gitattributes", b"* -text\n")
        self.edit(TEST_ONE, b'"currentNumber": "0x01"', b'"currentNumber": "0x02"')
        results = self.run_checks()
        self.assertStatus(results, "manifests", "FAIL")
        self.assertFalse(any("checks them out" in f for f in results["manifests"].failures))
        repo.write(self.root, ".gitattributes", b"legacy/** eol=crlf\n")
        results = self.run_checks()
        self.assertTrue(any("eol=crlf" in f for f in results["manifests"].failures))

    # -- legacy --------------------------------------------------------------------------------

    def test_legacy_fails_on_a_file_outside_the_manifest(self):
        repo.write(self.root, "legacy/notes.txt", b"x\n")
        self.assertStatus(self.run_checks(), "legacy", "FAIL")

    def test_legacy_fails_on_a_filler_no_test_names(self):
        repo.write(self.root, "legacy/src/GeneralStateTestsFiller/stA/strayFiller.json", b"{}\n")
        self.assertStatus(self.run_checks(), "legacy", "FAIL")

    def test_legacy_fails_on_a_named_filler_that_is_absent(self):
        os.remove(self.path(FILLER_TWO))
        self.assertStatus(self.run_checks(), "legacy", "FAIL")

    def test_legacy_fails_on_a_source_outside_the_fillers(self):
        doc = repo.read_json(self.root, TEST_ONE)
        doc["one"]["_info"]["source"] = "elsewhere/oneFiller.json"
        repo.write_json(self.root, TEST_ONE, doc)
        self.assertStatus(self.run_checks(), "legacy", "FAIL")

    def test_legacy_fails_on_a_left_out_entry_without_a_reason(self):
        self.edit_manifest(lambda m: m["carried"][0]["left_out"][0].pop("reason"))
        self.assertStatus(self.run_checks(), "legacy", "FAIL")

    # -- legacy source -------------------------------------------------------------------------

    def test_legacy_source_catches_a_legacy_edit_made_with_its_manifest(self):
        self.edit(TEST_ONE, b'"currentNumber": "0x01"', b'"currentNumber": "0x02"')
        disk = repo.tree(self.root).disk_tree("legacy/GeneralStateTests")[0]
        self.edit_manifest(lambda m: m["carried"][0].update(tree=disk))
        self.commit()
        results = self.run_checks(require_source=True)
        self.assertStatus(results, "manifests", "PASS")
        self.assertStatus(results, "legacy source", "FAIL")

    def test_legacy_source_catches_a_license_edit_made_with_its_manifest(self):
        repo.write(self.root, "legacy/LICENSE", b"Another license.\n")
        blob = gitobj.blob_id(b"Another license.\n")
        self.edit_manifest(lambda m: m["carried"][2].update(blob=blob))
        self.commit()
        results = self.run_checks(require_source=True)
        self.assertStatus(results, "manifests", "PASS")
        self.assertStatus(results, "legacy source", "FAIL")

    def test_legacy_source_fails_on_a_wrong_source_tree(self):
        self.edit_manifest(lambda m: m["carried"][0].update(from_tree="0" * 40))
        self.assertStatus(self.run_checks(), "legacy source", "FAIL")

    def test_legacy_source_fails_on_a_left_out_path_the_source_lacks(self):
        self.edit_manifest(lambda m: m["carried"][0]["left_out"].append({"path": "stZ/none.json", "reason": "invented"}))
        self.assertStatus(self.run_checks(), "legacy source", "FAIL")

    def moved(self):
        tree = repo.git(self.root, "rev-parse", self.repo.commit + "^{tree}").strip()
        return repo.git(self.root, "commit-tree", "-p", self.repo.commit, tree, "-m", "moved").strip()

    def test_legacy_source_fails_when_the_branch_moves(self):
        for ref in (f"refs/remotes/origin/{repo.BRANCH}", f"refs/heads/{repo.BRANCH}"):
            with self.subTest(ref):
                repo.git(self.root, "update-ref", ref, self.moved())
                self.assertStatus(self.run_checks(), "legacy source", "FAIL")
                repo.git(self.root, "update-ref", ref, self.repo.commit)

    def test_legacy_source_without_the_commit(self):
        self.edit_manifest(lambda m: m["source"].update(commit="1" * 40))
        self.assertStatus(self.run_checks(), "legacy source", "NOT RUN")
        self.assertStatus(self.run_checks(require_source=True), "legacy source", "FAIL")

    def test_legacy_source_requires_the_branch_in_ci(self):
        repo.git(self.root, "update-ref", "-d", f"refs/remotes/origin/{repo.BRANCH}")
        self.assertStatus(self.run_checks(), "legacy source", "PASS")
        self.assertStatus(self.run_checks(require_source=True), "legacy source", "FAIL")

    # -- legacy hashes -------------------------------------------------------------------------

    def test_legacy_hashes_fails_on_a_changed_generated_test_hash(self):
        doc = repo.read_json(self.root, TEST_ONE)
        doc["one"]["_info"]["generatedTestHash"] = "0x" + "00" * 32
        repo.write_json(self.root, TEST_ONE, doc)
        self.assertStatus(self.run_checks(), "legacy hashes", "FAIL")

    def test_legacy_hashes_fails_on_a_changed_filler(self):
        self.edit(FILLER_TWO, b"[[0]] 1", b"[[0]] 2")
        self.assertStatus(self.run_checks(), "legacy hashes", "FAIL")

    def test_legacy_hashes_fails_on_a_sample_test_that_is_absent(self):
        self.assertStatus(self.run_checks(sample=("stA/one.json", "stZ/none.json")), "legacy hashes", "FAIL")

    def test_legacy_hashes_fails_when_the_sample_misses_a_feature(self):
        results = self.run_checks(must_cover=frozenset({"literal block", "escaped line break"}))
        self.assertStatus(results, "legacy hashes", "FAIL")
        self.assertTrue(any("escaped line break" in f for f in results["legacy hashes"].failures))

    def test_legacy_hashes_requires_a_json_filler_that_repeats_a_key(self):
        cover = frozenset({"literal block"})
        self.assertStatus(self.run_checks(must_cover=cover), "legacy hashes", "PASS")
        results = self.run_checks(must_cover=cover, sample=("stA/two.json",))
        self.assertStatus(results, "legacy hashes", "FAIL")
        self.assertTrue(any("repeats a key" in f for f in results["legacy hashes"].failures))

    # -- ethereumclassic -----------------------------------------------------------------------

    def with_fixture(self, test, path=FIXTURE, ledger_too=True):
        repo.write_json(self.root, path, {"push0": test})
        if ledger_too:
            repo.write_ledger(self.root)
        return self.run_checks()

    def test_a_sound_fixture_passes(self):
        results = self.with_fixture(repo.fixture())
        self.assertStatus(results, "ethereumclassic", "PASS")
        self.assertStatus(results, "ledger", "PASS")

    def test_ethereumclassic_refuses_a_regression_baseline(self):
        self.assertStatus(self.with_fixture(repo.fixture(grade="regression baseline")), "ethereumclassic", "FAIL")

    def test_ethereumclassic_requires_an_oracle_and_a_source(self):
        test = repo.fixture()
        del test["_info"]["oracle"]
        test["_info"]["contentHash"] = checks.content_hash(test)
        self.assertStatus(self.with_fixture(test), "ethereumclassic", "FAIL")

    def test_ethereumclassic_requires_a_partial_to_name_its_second_check(self):
        self.assertStatus(self.with_fixture(repo.fixture(grade="partial")), "ethereumclassic", "FAIL")

    def test_ethereumclassic_fails_on_a_content_hash_that_does_not_recompute(self):
        self.assertStatus(self.with_fixture(repo.fixture(contentHash="0" * 64)), "ethereumclassic", "FAIL")

    def test_ethereumclassic_fails_on_a_misplaced_fixture(self):
        for path in ("ethereumclassic/state/classic.json", "ethereumclassic/state/push0/classic.json",
                     "ethereumclassic/State/eip3855_push0/classic.json"):
            with self.subTest(path):
                results = self.with_fixture(repo.fixture(), path=path, ledger_too=False)
                self.assertStatus(results, "ethereumclassic", "FAIL")
                os.remove(self.path(path))

    def test_ethereumclassic_fails_on_a_fixture_that_is_null(self):
        repo.write(self.root, FIXTURE, b"null\n")
        self.assertStatus(self.run_checks(), "ethereumclassic", "FAIL")

    # -- suites --------------------------------------------------------------------------------

    def test_suites_fails_on_a_test_in_two_suites(self):
        body = {k: v for k, v in repo.read_json(self.root, TEST_ONE)["one"].items() if k != "_info"}
        results = self.with_fixture(repo.fixture(body=body))
        self.assertStatus(results, "ethereumclassic", "PASS")
        self.assertStatus(results, "suites", "FAIL")

    # -- ledger --------------------------------------------------------------------------------

    def test_ledger_fails_when_fixtures_exist_without_it(self):
        self.assertStatus(self.with_fixture(repo.fixture(), ledger_too=False), "ledger", "FAIL")

    def test_ledger_fails_on_rules_without_a_ledger(self):
        repo.write_json(self.root, checks.RULES, repo.RULES)
        self.assertStatus(self.run_checks(), "ledger", "FAIL")

    def test_ledger_fails_when_it_does_not_regenerate(self):
        self.with_fixture(repo.fixture())
        doc = repo.read_json(self.root, checks.LEDGER)
        doc["rules"][1]["status"] = "missing"
        repo.write(self.root, checks.LEDGER, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        self.assertStatus(self.run_checks(), "ledger", "FAIL")

    def test_ledger_fails_on_files_that_are_null(self):
        self.with_fixture(repo.fixture())
        for path in (checks.LEDGER, checks.RULES):
            with self.subTest(path):
                with open(self.path(path), "rb") as f:
                    kept = f.read()
                repo.write(self.root, path, b"null\n")
                self.assertStatus(self.run_checks(), "ledger", "FAIL")
                repo.write(self.root, path, kept)

    def test_ledger_fails_when_a_partial_test_is_not_recorded(self):
        results = self.with_fixture(repo.fixture(grade="partial", second_check="the filler's expected storage"))
        self.assertStatus(results, "ethereumclassic", "PASS")
        self.assertStatus(results, "ledger", "FAIL")

    def test_ledger_passes_when_a_partial_test_is_recorded(self):
        repo.write_json(self.root, FIXTURE, {"push0": repo.fixture(grade="partial", second_check="the filler's storage")})
        issue = {"path": FIXTURE, "test": "push0", "issue": "expected values from core-geth"}
        repo.write_ledger(self.root, issues={"eip3855": [issue]})
        self.assertStatus(self.run_checks(), "ledger", "PASS")
        doc = repo.read_json(self.root, checks.LEDGER)
        self.assertEqual({e["rule"]: e["status"] for e in doc["rules"]}, {"eip155": "missing", "eip3855": "partial"})

    def test_ledger_checks_what_a_known_issue_names(self):
        repo.write_json(self.root, FIXTURE, {"push0": repo.fixture()})
        cases = {
            "an absent file": {"eip3855": [{"path": "ethereumclassic/state/eip3855_push0/mordor.json",
                                            "test": "push0", "issue": "x"}]},
            "an absent test": {"eip3855": [{"path": FIXTURE, "test": "push1", "issue": "x"}]},
            "another rule's test": {"eip155": [{"path": FIXTURE, "test": "push0", "issue": "x"}]},
        }
        for why, issues in cases.items():
            with self.subTest(why):
                repo.write_ledger(self.root, issues=issues)
                self.assertStatus(self.run_checks(), "ledger", "FAIL")
        repo.write_ledger(self.root, issues={"eip3855": [{"path": TEST_ONE, "test": "one", "issue": "a model"}]})
        self.assertStatus(self.run_checks(), "ledger", "PASS")

    def test_ledger_fails_on_rules_it_cannot_read(self):
        self.with_fixture(repo.fixture())
        rules = repo.read_json(self.root, checks.RULES)
        rules["source"]["commit"] = "not a commit"
        repo.write_json(self.root, checks.RULES, rules)
        self.assertStatus(self.run_checks(), "ledger", "FAIL")

    def test_writing_the_ledger_refuses_a_symbolic_link(self):
        self.with_fixture(repo.fixture())
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "elsewhere.json")
            with open(target, "w") as f:
                f.write("{}\n")
            os.remove(self.path(checks.LEDGER))
            os.symlink(target, self.path(checks.LEDGER))
            results = self.run_checks(write_ledger=True)
            self.assertStatus(results, "ledger", "FAIL")
            with open(target) as f:
                self.assertEqual(f.read(), "{}\n")

    # -- the command ---------------------------------------------------------------------------

    def test_the_command_refuses_tracked_bytecode(self):
        repo.write(self.root, "x/__pycache__/m.cpython-312.pyc", b"\x00")
        repo.git(self.root, "add", "-f", "x/__pycache__/m.cpython-312.pyc")
        out = io.StringIO()
        with repo.hermetic(), contextlib.redirect_stdout(out):
            self.assertEqual(check.main(["--root", self.root]), 1)
        self.assertIn("x/__pycache__/m.cpython-312.pyc", out.getvalue())

    def test_the_command_starts_no_log_line_with_text_from_a_file(self):
        name = "a\n::warning::injected.json"
        with open(os.path.join(self.root, name), "w") as f:
            f.write("{")
        out = io.StringIO()
        with repo.hermetic(), contextlib.redirect_stdout(out):
            check.main(["--root", self.root])
        self.assertIn("a\\n::warning::injected.json", out.getvalue())
        self.assertFalse([line for line in out.getvalue().splitlines() if line.lstrip().startswith("::")])
        self.assertEqual(check.printable("x\ry\u2028z\x1b \u00e9"), "x\\ry\\u2028z\\x1b \u00e9")

    def test_the_command_exits_2_outside_a_checkout(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(repo.ENV["HOME"])) as empty, repo.hermetic(), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(check.main(["--root", empty]), 2)
        self.assertIn("not a git checkout", err.getvalue())


if __name__ == "__main__":
    unittest.main()

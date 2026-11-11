import copy
import json
import unittest

import ledger
from tests import repo


class LedgerTest(unittest.TestCase):
    def test_rule_names(self):
        self.assertEqual(ledger.rule_of_field("EIP155Block"), "eip155")
        self.assertEqual(ledger.rule_of_field("ECIP1010PauseBlock"), "ecip1010")
        self.assertEqual(ledger.rule_of_field("ECBP1100DeactivateFBlock"), "ecbp1100")
        self.assertIsNone(ledger.rule_of_field("DisposalBlock"))
        self.assertIsNone(ledger.rule_of_field("ECIP1010Length"))
        self.assertIsNone(ledger.rule_of_field("ECIP1017EraRounds"))
        self.assertEqual(ledger.rule_of_folder("eip3855_push0"), "eip3855")
        self.assertEqual(ledger.rule_of_folder("ecip1017"), "ecip1017")
        for folder in ("push0", "eip3855-push0", "EIP3855_push0", "eip_3855"):
            self.assertIsNone(ledger.rule_of_folder(folder), folder)

    def test_rules_problems(self):
        self.assertEqual(ledger.check_rules(repo.RULES), [])
        bad = copy.deepcopy(repo.RULES)
        bad["source"]["commit"] = "abc"
        bad["networks"]["classic"]["DisposalBlock"] = 5900000
        bad["networks"]["mordor"]["EIP3855FBlock"] = -1
        self.assertEqual(len(ledger.check_rules(bad)), 3)

    def test_status_follows_from_tests_and_known_issues(self):
        fixtures = {
            "ethereumclassic/state/eip3855_push0/classic.json": ("eip3855_push0", {"push0": "partial"}),
        }
        issue = {"path": "ethereumclassic/state/eip3855_push0/classic.json", "test": "push0",
                 "issue": "expected values from core-geth; its second check is the filler's storage"}
        committed = {"description": "kept as written",
                     "rules": [{"rule": "eip155", "kind": "standard", "knownIssues": []},
                               {"rule": "eip3855", "kind": "standard", "knownIssues": [issue]}]}
        text, problems = ledger.regenerate(repo.RULES, fixtures, committed)
        self.assertEqual(problems, [])
        doc = json.loads(text)
        self.assertEqual(doc["description"], "kept as written")
        status = {e["rule"]: e["status"] for e in doc["rules"]}
        self.assertEqual(status, {"eip155": "missing", "eip3855": "partial"})
        self.assertEqual(doc["rules"][1]["activation"], {"classic": {"EIP3855FBlock": 19250000},
                                                         "mordor": {"EIP3855FBlock": 9957000}})
        fixtures["ethereumclassic/state/eip3855_push0/classic.json"] = ("eip3855_push0", {"push0": "conformance"})
        committed["rules"][1]["knownIssues"] = []
        doc = json.loads(ledger.regenerate(repo.RULES, fixtures, committed)[0])
        self.assertEqual(doc["rules"][1]["status"], "fully functional")

    def test_order_is_fixed(self):
        rules = {"source": repo.RULES["source"],
                 "networks": {"classic": {"EIP155Block": 1, "EIP2FBlock": 1, "ECIP1017FBlock": 1, "EIP3855FBlock": 1}}}
        fixtures = {"ethereumclassic/state/eip3855_push0/mordor.json": ("eip3855_push0", {"b": "conformance", "a": "conformance"}),
                    "ethereumclassic/state/eip3855_push0/classic.json": ("eip3855_push0", {"z": "conformance"})}
        committed = {"rules": [{"rule": r, "kind": "standard", "knownIssues": []}
                               for r in ("eip3855", "ecip1017", "eip155", "eip2")]}
        doc = json.loads(ledger.regenerate(rules, fixtures, committed)[0])
        self.assertEqual([e["rule"] for e in doc["rules"]], ["ecip1017", "eip2", "eip155", "eip3855"])
        self.assertEqual([(t["path"].rsplit("/", 1)[1], t["test"]) for t in doc["rules"][3]["tests"]],
                         [("classic.json", "z"), ("mordor.json", "a"), ("mordor.json", "b")])

    def test_a_rule_listed_twice(self):
        committed = {"rules": [{"rule": "eip155", "kind": "standard", "knownIssues": []}] * 2
                     + [{"rule": "eip3855", "kind": "standard", "knownIssues": []}]}
        _, problems = ledger.regenerate(repo.RULES, {}, committed)
        self.assertIn("COVERAGE.json: eip155 appears twice", problems)

    def test_problems(self):
        fixtures = {"ethereumclassic/state/eip3855_push0/classic.json": ("eip3855_push0", {"push0": "partial"}),
                    "ethereumclassic/state/eip1559_basefee/classic.json": ("eip1559_basefee", {"x": "conformance"})}
        committed = {"rules": [{"rule": "eip155", "kind": "ETH", "knownIssues": []},
                               {"rule": "eip3855", "kind": "standard", "knownIssues": [{"path": "p"}]},
                               {"rule": "ecip1017", "kind": "ETC-specific", "knownIssues": []}]}
        _, problems = ledger.regenerate(repo.RULES, fixtures, committed)
        text = "\n".join(problems)
        self.assertIn("eip1559, is not in RULES.json", text)
        self.assertIn("eip155's kind", text)
        self.assertIn("eip3855's knownIssues", text)
        self.assertIn("push0 is partial and not recorded", text)
        self.assertIn("ecip1017 is not in RULES.json", text)


if __name__ == "__main__":
    unittest.main()

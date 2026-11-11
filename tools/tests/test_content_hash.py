import hashlib
import unittest

import checks


class ContentHashTest(unittest.TestCase):
    def test_the_canonical_form_is_pinned(self):
        """The form is written out here by hand, so a change to how content_hash serializes fails
        this test rather than passing with whatever the new form computes."""
        test = {"b": [1, "\u00e9", {"_info": "dropped", "c": None}], "_info": {"x": 1}, "a": {"_info": 2, "d": True}}
        canonical = '{"a":{"d":true},"b":[1,"\u00e9",{"c":null}]}'
        self.assertEqual(checks.content_hash(test), hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        self.assertEqual(checks.content_hash(test), "090a87c794e0989ea390eff88017389c418b409b703056775d798f2f27410cc3")

    def test_info_does_not_change_it(self):
        self.assertEqual(checks.content_hash({"a": 1, "_info": {"x": 1}}), checks.content_hash({"a": 1}))
        self.assertNotEqual(checks.content_hash({"a": 1}), checks.content_hash({"a": 2}))


if __name__ == "__main__":
    unittest.main()

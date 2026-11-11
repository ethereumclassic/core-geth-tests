import unittest

import retesteth
import yamlsubset


def value(text):
    """The node tree as plain Python: scalars as (style, tag, value), mappings as lists of pairs."""
    def walk(node):
        if isinstance(node, yamlsubset.Scalar):
            return (node.style, node.tag, node.value)
        if isinstance(node, yamlsubset.Mapping):
            return [(k, walk(v)) for k, v in node.pairs]
        return {"seq": [walk(i) for i in node.items]}
    return walk(yamlsubset.load(text))


def plain(v):
    return ("plain", None, v)


class ReaderTest(unittest.TestCase):
    def test_block_collections_and_comments(self):
        text = "# head\na:\n  b: 1  # tail\n  c:\n  - x\n  - k: v\n    l: w\n  -\n    - y\nd: 'q'\n"
        self.assertEqual(value(text), [
            ("a", [("b", plain("1")),
                   ("c", {"seq": [plain("x"), [("k", plain("v")), ("l", plain("w"))], {"seq": [plain("y")]}]})]),
            ("d", ("single", None, "q")),
        ])

    def test_a_sequence_at_its_key_indentation(self):
        self.assertEqual(value("a:\n- 1\n- 2\nb: 3\n"),
                         [("a", {"seq": [plain("1"), plain("2")]}), ("b", plain("3"))])

    def test_quoted_scalars(self):
        text = "a: 'it''s'\nb: \"x y 'z' \u00e9\"\n'c d': \"\"\n\"e\" : 1\n"
        self.assertEqual(value(text), [
            ("a", ("single", None, "it's")),
            ("b", ("double", None, "x y 'z' \u00e9")),
            ("c d", ("double", None, "")),
            ("e", plain("1")),
        ])

    def test_literal_block(self):
        text = "a: |\n  one\n    two\n\n  three\n\n\nb: x\n"
        self.assertEqual(value(text), [("a", ("literal", None, "one\n  two\n\nthree\n")), ("b", plain("x"))])

    def test_literal_block_at_the_end_without_a_final_newline(self):
        node, features = yamlsubset.parse("a: |\n  one\n  two")
        self.assertEqual(node.pairs[0][1].value, "one\ntwo")
        self.assertIn("no final newline", features)

    def test_flow_collections_spanning_lines(self):
        text = "a: {}\nb: { 0x00: 0x01, 'k': [1, \"2\"] }\nc: [\n  x,  # note\n  y\n]\n"
        self.assertEqual(value(text), [
            ("a", []),
            ("b", [("0x00", plain("0x01")), ("k", {"seq": [plain("1"), ("double", None, "2")]})]),
            ("c", {"seq": [plain("x"), plain("y")]}),
        ])

    def test_document_start_and_int_tag(self):
        node, features = yamlsubset.parse("---\na: !!int -1\nb:\n- !!int 7\n")
        self.assertEqual([(k, (v.style, v.tag, v.value)) if isinstance(v, yamlsubset.Scalar) else k for k, v in node.pairs][0],
                         ("a", ("plain", "!!int", "-1")))
        self.assertEqual(node.pairs[1][1].items[0].tag, "!!int")
        self.assertTrue({"document start", "tag !!int"} <= features)

    def test_plain_scalar_spanning_lines(self):
        text = "a:\n- :label x :yul {\n    f(0)\n\n    g(1)\n  }\n- next\n"
        self.assertEqual(value(text), [("a", {"seq": [plain(":label x :yul { f(0)\ng(1) }"), plain("next")]})])

    def test_quoted_scalars_spanning_lines(self):
        text = "a: ':yul {\n     f(0)\n   }'\nb: \"one\ntwo\n\n  three\"\nc: \"0x\\\n   ab\\\n   cd\"\n"
        self.assertEqual(value(text), [
            ("a", ("single", None, ":yul { f(0) }")),
            ("b", ("double", None, "one two\nthree")),
            ("c", ("double", None, "0xabcd")),
        ])

    def test_paths_the_ci_sample_does_not_reach(self):
        """Reader paths the legacy fillers use that the checked sample does not reach, each pinned by its own value."""
        self.assertEqual(value("'k' : v\n"), [("k", plain("v"))])
        self.assertEqual(value("a:\n- x\n  y\n  # ends it\n- z\n"),
                         [("a", {"seq": [plain("x y"), plain("z")]})])
        self.assertEqual(value("a: |\n  x\n \n    \n  y\n"), [("a", ("literal", None, "x\n\n  \ny\n"))])
        self.assertEqual(value("a: [ !!int 1, 2, { b: !!int -3 } ]\n"),
                         [("a", {"seq": [("plain", "!!int", "1"), plain("2"), [("b", ("plain", "!!int", "-3"))]]})])
        self.assertEqual(value("a:\n- ':yul {\n    f(0)\n  }'\n"), [("a", {"seq": [("single", None, ":yul { f(0) }")]})])

    def test_features_reported(self):
        _, features = yamlsubset.parse("a: |\n  x\ty \"q\" \u00e9\n")
        self.assertTrue({"block mapping", "literal block", "newline in a string", "tab in a string",
                         "quote or backslash in a string", "non-ASCII in a string"} <= features)
        self.assertTrue(features <= yamlsubset.FEATURES)

    def test_refusals(self):
        refused = {
            "an anchor": "a: &x 1\n",
            "an alias": "a: *x\n",
            "another tag": "a: !!str 1\n",
            "a folded block": "a: >\n  x\n",
            "a block indicator": "a: |-\n  x\n",
            "a tab in indentation": "a:\n\tb: 1\n",
            "a tab in a plain scalar": "a: x\ty\n",
            "a carriage return": "a: 1\r\n",
            "a byte order mark": "\ufeffa: 1\n",
            "a second document": "a: 1\n---\nb: 2\n",
            "an unknown escape": 'a: "\\q"\n',
            "a colon inside a plain scalar": "a: b: c\n",
            "a plain scalar spanning lines in a flow": "a: [x\n  y]\n",
            "an unclosed flow": "a: [x, y\n",
            "an int tag on a hex value": "a: !!int 0x10\n",
            "unexpected indentation": "a: 1\n  b: 2\n",
            "an explicit key": "? a\n: 1\n",
            "an escape other than a line break": 'a: "\\t"\n',
            "an escaped quote": 'a: "\\""\n',
            "an int with a leading zero": "a: !!int 010\n",
            "an int past 32 bits": "a: !!int 3000000000\n",
            "an int below 32 bits": "a: !!int -2147483649\n",
            "a plain key that reads as null": "~: 1\n",
            "a flow key that reads as null": "a: {Null: 1}\n",
            "a NUL": "a: \"x\x00\"\n",
            "an over-indented leading line": "a: |\n      \n  x\n",
            "a root scalar": "x\n",
            "a root sequence": "- x\n",
            "a root scalar and a marker": "foo\n---\n",
        }
        for why, text in refused.items():
            with self.subTest(why), self.assertRaises(yamlsubset.YAMLSubsetError):
                yamlsubset.load(text)


class RetestethValueTest(unittest.TestCase):
    """retesteth 0.2.3's reading of YAML: an integer only when tagged, null only for plain null."""

    def test_values(self):
        doc = retesteth.from_yaml(yamlsubset.load("a: 1\nb: !!int 1\nc: null\nd: 'null'\ne: ~\nf:\n"))
        self.assertEqual(list(doc), [("a", "1"), ("b", 1), ("c", None), ("d", "null"), ("e", None), ("f", None)])

    def test_a_repeated_key_is_kept(self):
        doc = retesteth.from_yaml(yamlsubset.load("a: 1\na: 2\n"))
        self.assertEqual(retesteth.as_json(doc), '{"a":"1","a":"2"}')


if __name__ == "__main__":
    unittest.main()

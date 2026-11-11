import unittest

import keccak
import retesteth


class SerializationTest(unittest.TestCase):
    def test_only_newline_and_tab_are_escaped(self):
        self.assertEqual(retesteth.as_json({"k": 'a\nb\tc"d\\e\u00e9'}), '{"k":"a\\nb\\tc"d\\e\u00e9"}')

    def test_scalars_and_order(self):
        doc = {"z": None, "y": True, "x": False, "w": -7, "v": [1, "1", []], "u": {}}
        self.assertEqual(retesteth.as_json(doc), '{"z":null,"y":true,"x":false,"w":-7,"v":[1,"1",[]],"u":{}}')
        self.assertEqual(retesteth.as_json(doc, sort=True), '{"u":{},"v":[1,"1",[]],"w":-7,"x":false,"y":true,"z":null}')

    def test_a_json_repeated_key_keeps_its_first_place_and_last_value(self):
        self.assertEqual(retesteth.as_json(retesteth.from_json('{"a": 1, "b": 2, "a": 3}')), '{"a":3,"b":2}')

    def test_floats_are_refused(self):
        with self.assertRaises(ValueError):
            retesteth.from_json('{"a": 1.5}')

    def test_source_hash_is_keccak_of_the_compact_form(self):
        data = b'{\n  "t": {"a": "x\\ny\\n", "b": [1, 2]}\n}\n'
        want = keccak.keccak256(b'{"t":{"a":"x\\ny\\n","b":[1,2]}}').hex()
        self.assertEqual(retesteth.source_hash("tFiller.json", data), want)
        yaml = b"t:\n  a: |\n    x\n    y\n  b:\n  - !!int 1\n  - !!int 2\n"
        self.assertEqual(retesteth.source_hash("tFiller.yml", yaml), want)
        self.assertNotEqual(retesteth.source_hash("tFiller.yml", yaml.replace(b"!!int 2", b"2")), want)

    def test_what_retesteth_reads_differently_is_refused(self):
        for text in ('{"a": "x\\"y"}', '{"a": "x\\\\y"}', '{"a": "\\u00e9"}', '{"a": 2147483648}', '{"a": -2147483649}'):
            with self.subTest(text), self.assertRaises(ValueError):
                retesteth.from_json(text)
        self.assertEqual(retesteth.from_json('{"a": 2147483647, "b": "x\\ty"}'), {"a": 2147483647, "b": "x\ty"})
        for value in ({"": 1}, {"a": [{"": None}]}):
            with self.subTest(value), self.assertRaises(ValueError):
                retesteth.as_json(value)
        with self.assertRaises(ValueError):
            retesteth.generated_test_hash("", {"a": 1})

    def test_generated_test_hash_keys_the_test_and_drops_its_info(self):
        test = {"_info": {"comment": "ignored"}, "b": 1, "a": {"d": "x", "c": "y"}}
        want = keccak.keccak256(b'"name":{"a":{"c":"y","d":"x"},"b":1}').hex()
        self.assertEqual(retesteth.generated_test_hash("name", test), want)

    def test_normalize(self):
        self.assertEqual(retesteth.normalize("0xABcd"), "abcd")
        self.assertEqual(retesteth.normalize("abcd"), "abcd")


if __name__ == "__main__":
    unittest.main()

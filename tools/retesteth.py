"""The two hashes retesteth 0.2.3 records in a filled test's `_info`, recomputed.

Read from retesteth's source at its version tag commit 33194995 ("version 0.2.3 postmerge tag"), the
version that filled the legacy suite:

- `sourceHash` is Keccak-256 of the filler as parsed and written back out compactly
  (`TestSuiteHelperFunctions.cpp`: `sha3(data->asJson(0, false))`).
- `generatedTestHash` is Keccak-256 of the filled test the same way, keyed by its name, with its own
  `_info` removed and keys sorted (`addClientInfo.cpp`, `calculateHashOfTheNewFilledTest`).
- The compact form (`DataObject.cpp`, `asJson`) escapes only newline and tab in a string, writes keys
  raw, and prints a keyed object as `"key":{...}`.
- A YAML scalar is an integer only when tagged `!!int`, null when it is a plain empty, `~` or `null`
  scalar, and a string otherwise (`ConvertYaml.cpp`, with yaml-cpp's reading of null); nothing is
  sorted on load.
- A JSON filler that repeats a key keeps the key's first position and takes its last value, as
  Python's json module does, which the legacy suite's recorded hashes require.

Three behaviors of that source are refused here rather than reproduced, since no legacy file needs
them: `asJson` prints no key at all for an empty key; retesteth's own JSON reader keeps every escape
but `\\n` and `\\t` as written, where Python's decodes it; and it reads a JSON integer into a 32-bit
`int`. So an empty key, a JSON escape other than `\\n` and `\\t`, and an integer outside 32 bits each
raise ValueError.
"""

import json
import re

import keccak
import yamlsubset

_YAML_NULLS = {"", "~", "null", "Null", "NULL"}
_INT32 = range(-2 ** 31, 2 ** 31)
_OTHER_ESCAPE = re.compile(r"\\[^nt]")


class Pairs(list):
    """An object as ordered (key, value) pairs, a repeated key kept, as retesteth holds one."""


def from_yaml(node):
    """retesteth's value for a node of `yamlsubset`."""
    if isinstance(node, yamlsubset.Scalar):
        if node.tag == "!!int":
            return int(node.value)
        if node.style == "plain" and node.value in _YAML_NULLS:
            return None
        return node.value
    if isinstance(node, yamlsubset.Mapping):
        return Pairs((key, from_yaml(value)) for key, value in node.pairs)
    if isinstance(node, yamlsubset.Sequence):
        return [from_yaml(item) for item in node.items]
    raise TypeError(type(node))


def _no_floats(text):
    raise ValueError(f"a non-integer number, {text}, which this serialization does not cover")


def _int32(text):
    value = int(text)
    if value not in _INT32:
        raise ValueError(f"the integer {text} does not fit the 32 bits retesteth reads it into")
    return value


def from_json(text):
    """retesteth's value for a JSON document (a str)."""
    m = _OTHER_ESCAPE.search(text)
    if m:
        raise ValueError(f"the escape {m.group()} is kept as written by retesteth and decoded here")
    return json.loads(text, parse_float=_no_floats, parse_int=_int32, parse_constant=_no_floats)


def as_json(value, sort=False):
    """retesteth's compact JSON for `value`; `sort` orders every object's keys."""
    out = []
    _emit(value, out, sort)
    return "".join(out)


def _emit(value, out, sort):
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, str):
        out.append('"' + value.replace("\n", "\\n").replace("\t", "\\t") + '"')
    elif isinstance(value, list) and not isinstance(value, Pairs):
        out.append("[")
        for n, item in enumerate(value):
            if n:
                out.append(",")
            _emit(item, out, sort)
        out.append("]")
    elif isinstance(value, (dict, Pairs)):
        pairs = list(value.items()) if isinstance(value, dict) else list(value)
        if sort:
            pairs.sort(key=lambda kv: kv[0])
        out.append("{")
        for n, (key, item) in enumerate(pairs):
            if key == "":
                raise ValueError("an empty key, which retesteth writes without its key")
            if n:
                out.append(",")
            out.append('"' + key + '":')
            _emit(item, out, sort)
        out.append("}")
    else:
        raise TypeError(type(value))


def filler_value(name, data):
    """retesteth's value for a filler file's bytes; `name` decides how it is read."""
    text = data.decode("utf-8")
    if name.endswith(".json"):
        return from_json(text)
    if name.endswith(".yml"):
        return from_yaml(yamlsubset.load(text))
    raise ValueError(f"{name}: not a .json or .yml filler")


def source_hash(name, data):
    """The `sourceHash` of a filler, as lowercase hex without a 0x prefix."""
    return keccak.keccak256(as_json(filler_value(name, data)).encode("utf-8")).hex()


def generated_test_hash(test_name, test):
    """The `generatedTestHash` of one filled test (a value from `from_json`), as lowercase hex."""
    if test_name == "":
        raise ValueError("an empty test name, which retesteth writes without its key")
    body = {key: value for key, value in test.items() if key != "_info"}
    text = '"' + test_name + '":' + as_json(body, sort=True)
    return keccak.keccak256(text.encode("utf-8")).hex()


def normalize(recorded):
    """A recorded hash as lowercase hex without a 0x prefix."""
    recorded = recorded.lower()
    return recorded[2:] if recorded.startswith("0x") else recorded

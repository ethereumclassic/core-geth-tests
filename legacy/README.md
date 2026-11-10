# The legacy suite

State tests carried from `etclabscore/tests`, together with the fillers they were generated from.
Every file is kept exactly as it arrived. Nothing in this directory is edited.

Four terms are used throughout:

- A **test file** is one JSON file under `GeneralStateTests/`. Each holds one test.
- A **filler** is the file a test was generated from. Each test names its filler in `_info.source`.
- A **label** is a fork name used as a key under a test's `post`, such as `ETC_Mystique`.
- An **entry** is one expected result in the list under a label.

## Contents

| Path | Contents |
|---|---|
| `GeneralStateTests/` | 2,593 test files, each carrying at least one label that begins with `ETC_` |
| `src/GeneralStateTestsFiller/` | the 2,593 fillers those tests name, one for each, so every `_info.source` resolves as written from this directory |
| `LICENSE` | the source's license, unchanged |
| `MANIFEST.json` | the source commit, the git tree of each directory above, and every file of the source that was left out, with its reason |

Every command below runs from the root of a checkout of `main`, with git, Python 3 and a POSIX
shell. Commands that read the source commit need it in the clone. `git clone` fetches every branch,
so it is already there; a single-branch or shallow clone fetches it with
`git fetch origin dep-etclabscore-tests-06ec708ea7f3`.

## Source

The files were copied from git's objects, so each one is byte for byte the file at
`etclabscore/tests` commit `06ec708ea7f3707826142a97a94b61d40c920696`. This repository keeps that
commit, with its history, on the branch `dep-etclabscore-tests-06ec708ea7f3`, which is never edited.

The copy carries no history of its own. A file's history is read at the source commit:

```sh
git log --format='%h %ad %an %s' --date=short 06ec708ea7f3707826142a97a94b61d40c920696 -- GeneralStateTests/stChainId/chainId.json
```

For that file it lists 11 commits, from 2019-08-21 to 2022-11-19. The same command gives the same
history in a clone of `ethereumclassic/archive-reference-material`, whose history also contains the
commit.

## What was left out

`MANIFEST.json` names every file of the two source directories that is not here, with one of four
reasons:

| Reason | Files |
|---|---|
| `no ETC label` | 24 test files: all 12 in `stEIP3540/`, all 8 in `Shanghai/`, and `stExample/accessListExample.json`, `stPreCompiledContracts2/CallEcrecover_Overflow.json`, `stTransactionTest/HighGasPrice.json` and `stTransactionTest/ValueOverflow.json` |
| `not a test` | `stShift/.stub`, which reads "This file is here to keep the directory." |
| `named by no test` | 35 fillers that no test at the source commit names |
| `named only by a test left out` | the 4 fillers of the last four test files in the first row |

The 24 test files carry only Ethereum fork names as labels, and `Merge+3540+3670` for the 12 in
`stEIP3540/`. Twenty of them name a filler that the source commit does not contain. This prints each
one's labels, read at the source commit, and marks those twenty:

```sh
python3 - <<'EOF'
import json, subprocess
m = json.load(open("legacy/MANIFEST.json", encoding="utf-8"))
commit = m["source"]["commit"]
def show(path):
    r = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None
for e in m["carried"][0]["left_out"]:
    if e["reason"] == "no ETC label":
        doc = json.loads(show("GeneralStateTests/" + e["path"]))
        labels = sorted({l for t in doc.values() for l in t["post"]})
        absent = any(show(t["_info"]["source"]) is None for t in doc.values())
        print(e["path"], *labels, "(named filler absent)" if absent else "")
EOF
```

## The grade: regression baseline

This repository grades a test by where its expected values came from. A test whose expected values
were computed by the client under test is a **regression baseline**: agreement shows that a client
still computes what was computed when the test was filled, and never that either result is right.
Every test here is a regression baseline, for two reasons the files themselves record.

**One client computed every expected value.** Each test records in `_info` how it was filled. All
2,593 name the same client, `evmetc version 1.12.9-unstable-550af1e1-20221005`, driven by retesteth
0.2.3:

```sh
python3 - <<'EOF'
import collections, json, pathlib
seen = collections.Counter()
for p in pathlib.Path("legacy/GeneralStateTests").rglob("*.json"):
    for t in json.loads(p.read_text(encoding="utf-8")).values():
        seen[(t["_info"]["filling-rpc-server"], t["_info"]["filling-tool-version"])] += 1
for (client, tool), n in seen.most_common():
    print(n, client, tool, sep="\t")
EOF
```

That client is a build of `ethereumclassic/core-geth` at its commit `550af1e1`, whose version file
reads 1.12.9 `unstable`. The commit is in core-geth's `v1.12.9` release. In a clone of
`ethereumclassic/core-geth`:

```sh
git show 550af1e1:params/version.go | grep -E 'Version(Major|Minor|Patch|Meta) +='
git tag --contains 550af1e1 --list 'v1.12.*' --sort=v:refname | head -1
```

**The ETC labels are renamed Ethereum fork names.** The source's `makeetc.sh` rewrites the fork names
in every `.json` and `.yml` file under `src/`, where the fillers are, by this map:

| Ethereum fork | ETC label |
|---|---|
| Byzantium | `ETC_Atlantis` |
| Constantinople, ConstantinopleFix | `ETC_Agharta` |
| Istanbul | `ETC_Phoenix` |
| Berlin | `ETC_Magneto` |
| London, Merge | `ETC_Mystique` |
| Shanghai | `ETC_Spiral` |

```sh
git show 06ec708ea7f3707826142a97a94b61d40c920696:makeetc.sh | grep -o "s/- [A-Za-z]*/- ETC_[A-Za-z]*/"
```

## Measured properties

**Labels.** 44,095 entries, 43,779 of them under labels that begin with `ETC_`. No entry carries
`ETC_Spiral`, the label for Ethereum Classic's Spiral upgrade.

| Label | Entries | Test files |
|---|---:|---:|
| `ETC_Mystique` | 17,758 | 2,588 |
| `ETC_Magneto` | 13,277 | 2,446 |
| `ETC_Phoenix` | 12,515 | 2,421 |
| `ETC_Agharta` | 118 | 15 |
| `ETC_Atlantis` | 102 | 11 |
| `ETC_Mystique+3855` | 9 | 3 |
| `EIP150` | 102 | 11 |
| `EIP158` | 102 | 11 |
| `Homestead` | 101 | 10 |
| `Frontier` | 11 | 8 |

```sh
python3 - <<'EOF'
import collections, json, pathlib
entries, files = collections.Counter(), collections.Counter()
for p in pathlib.Path("legacy/GeneralStateTests").rglob("*.json"):
    labels = set()
    for test in json.loads(p.read_text(encoding="utf-8")).values():
        for label, results in test["post"].items():
            entries[label] += len(results)
            labels.add(label)
    files.update(labels)
for label in sorted(entries, key=lambda k: -entries[k]):
    print(f"{label}\t{entries[label]} entries\t{files[label]} files")
print("total", sum(entries.values()), "entries;", "ETC_Spiral:", entries["ETC_Spiral"])
EOF
```

**Eleven test files also carry labels that do not begin with `ETC_`**, among `EIP150`, `EIP158`,
`Frontier` and `Homestead`: `VMTests/vmIOandFlowOperations/jumpToPush.json`,
`stCallCodes/touchAndGo.json`, `stChainId/chainId.json`, `stCreate2/CREATE2_HighNonceDelegatecall.json`,
`stCreateTest/CREATE_HighNonce.json`, `stCreateTest/CREATE_HighNonceMinus1.json`,
`stEIP3607/transactionCollidingWithNonEmptyAccount_calls.json`,
`stEIP3607/transactionCollidingWithNonEmptyAccount_init.json`, `stExample/basefeeExample.json`,
`stExample/eip1559.json` and `stSLoadTest/sloadGasCost.json`. Every test file carries at least one
ETC label.

```sh
python3 - <<'EOF'
import json, pathlib
none = 0
for p in sorted(pathlib.Path("legacy/GeneralStateTests").rglob("*.json")):
    labels = {l for t in json.loads(p.read_text(encoding="utf-8")).values() for l in t["post"]}
    other = sorted(l for l in labels if not l.startswith("ETC_"))
    none += len(other) == len(labels)
    if other:
        print(p.as_posix(), *other)
print("test files with no ETC label:", none)
EOF
```

**No ETC-labeled entry has a block difficulty of 0.** None of the 43,779 entries under ETC labels
belongs to a test whose `env.currentDifficulty` is 0, and the smallest value in any test here is 1.
The command reads a value as hexadecimal when it begins with `0x` and as decimal otherwise:

```sh
python3 - <<'EOF'
import json, pathlib
def number(v):
    return int(v, 16) if v.startswith("0x") else int(v)
entries = zero = 0
smallest = None
for p in pathlib.Path("legacy/GeneralStateTests").rglob("*.json"):
    for t in json.loads(p.read_text(encoding="utf-8")).values():
        d = number(t["env"]["currentDifficulty"])
        n = sum(len(r) for label, r in t["post"].items() if label.startswith("ETC_"))
        entries += n
        zero += n if d == 0 else 0
        smallest = d if smallest is None else min(smallest, d)
print("ETC-labeled entries", entries, "| with currentDifficulty 0:", zero, "| smallest currentDifficulty:", smallest)
EOF
```

**Every filler a test names is here, and no other.** 2,593 named, 2,593 present:

```sh
python3 - <<'EOF'
import json, pathlib
named = set()
for p in pathlib.Path("legacy/GeneralStateTests").rglob("*.json"):
    for t in json.loads(p.read_text(encoding="utf-8")).values():
        named.add(t["_info"]["source"])
here = {p.relative_to("legacy").as_posix() for p in pathlib.Path("legacy/src/GeneralStateTestsFiller").rglob("*") if p.is_file()}
print("named:", len(named), "| present:", len(named & here), "| named but absent:", len(named - here), "| present but unnamed:", len(here - named))
EOF
```

Each test also records `_info.sourceHash`, a hash of its filler. Nothing in this file checks it.

## Checking this directory against its source

`MANIFEST.json` is the recorded list this directory was built from. This rebuilds each directory's
tree from the source commit, removes the paths the list names, and prints `True` for each path where
the rebuilt tree, the tree the manifest records and the tree committed on `main` agree:

```sh
python3 - <<'EOF'
import json, os, subprocess, tempfile
def git(*args, env=None, stdin=None):
    return subprocess.run(["git", *args], env=env, input=stdin, capture_output=True, text=True, check=True).stdout.strip()
m = json.load(open("legacy/MANIFEST.json", encoding="utf-8"))
commit = m["source"]["commit"]
for c in m["carried"]:
    here = git("rev-parse", f"HEAD:{c['path']}")
    if "blob" in c:
        print(c["path"], git("rev-parse", f"{commit}:{c['from']}") == c["blob"] == here)
        continue
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(d, "index"))
        git("read-tree", f"{commit}:{c['from']}", env=env)
        git("update-index", "--force-remove", "--stdin", env=env, stdin="".join(e["path"] + "\n" for e in c["left_out"]))
        rebuilt = git("write-tree", env=env)
    print(c["path"], rebuilt, rebuilt == c["tree"] == here)
EOF
```

## Changes

Nothing here is edited, renamed or reformatted. A test that is wrong stays as it is, and a test of
this repository's Ethereum Classic suite that covers the same rule at a stronger grade answers it. A
file here is removed only in the review that lands that test: the review names both, and checks that
the new test asserts everything the old filler's `expect` section did. A test of a rule Ethereum
Classic does not have is not carried forward; it is removed with its reason recorded, or replaced by a
test that the rule stays off.

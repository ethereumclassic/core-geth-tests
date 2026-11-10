# core-geth-tests

Consensus tests for Ethereum Classic, laid out so that a client such as
[`ethereumclassic/core-geth`](https://github.com/ethereumclassic/core-geth) can read them by path.

## Suites

The repository holds three suites, each in its own directory. A test belongs to exactly one suite.

| Directory | Suite | What it holds |
|---|---|---|
| `legacy/` | the legacy suite | state tests carried unedited from `etclabscore/tests`, with the fillers they were generated from; [`legacy/README.md`](legacy/README.md) describes them |
| `ethereum/` | the Ethereum suite | the Ethereum Foundation's tests, from `ethereum/tests` and its successor releases, each source pinned to a commit or a release checksum |
| `ethereumclassic/` | the Ethereum Classic suite | tests written in this repository for the rules Ethereum Classic runs differently from Ethereum, or alone |

Git keeps no empty directory, so each suite's directory appears with its first file.

**Which suite tests a rule.** A rule Ethereum Classic runs as Ethereum specifies it is tested by the
Ethereum suite, under Ethereum's fork labels. The Ethereum Classic suite adds only what differs:
Ethereum Classic's fork labels and activation heights, where the set of rules a fork holds, or its
activation, differs from Ethereum's. A rule that an ECIP changes, or one only Ethereum Classic has,
is tested by the Ethereum Classic suite alone. An Ethereum test is never relabeled with an Ethereum
Classic fork name.

## The Ethereum Classic suite's layout

```
ethereumclassic/<format>/<rule>/<file>.json
```

- `<format>` is one fixture format, such as state tests or difficulty tests. A client reads one
  format directory with one reader.
- `<rule>` is the EIP, ECIP or ECBP the test checks, one directory per rule. A rule's tests are
  selected by its directory, across formats.
- `<file>` is the network's name where activation heights matter: `classic.json` for Ethereum Classic,
  `mordor.json` for the Mordor test network, and a shared development network's name for upgrades
  tested ahead of both, once one is configured. A test that does not depend on a network's heights is
  named for what it checks.
- Forks are labels inside a file. No directory is named for a fork.

## Grades

A test is graded by where its expected values came from, and the grade is the weakest its material
supports.

| Grade | Its expected values come from | What agreement with it shows |
|---|---|---|
| conformance | the specification, or implementations other than the client under test, read as source | that the client agrees with an independent answer. A conformance test can catch the client being wrong |
| partial | the client under test, checked a second time across a difference in representation | more than a regression baseline, less than conformance |
| regression baseline | the client under test | that the client still computes what was computed when the test was made, and never that the result is right |

The legacy suite is a regression baseline throughout, and [`legacy/README.md`](legacy/README.md)
gives the measurements behind that. Each test of the Ethereum Classic suite states its grade, its
source and the origin of its expected values in its own `_info`. That statement is how a test is
read, never how it is admitted: a reviewer checks it against the test's source.

## How a client reads it

A client reads a suite by its path. The legacy suite keeps the directory layout and file format of
`ethereum/tests`' `GeneralStateTests`, so a state-test runner walks `legacy/GeneralStateTests/` as it
walks that directory.

The branch `dep-etclabscore-tests-06ec708ea7f3` holds `etclabscore/tests` at commit
`06ec708ea7f3707826142a97a94b61d40c920696`, whole and with its history, for clients that pin that
commit. It is never edited.

## License

This repository's own work is licensed under the MIT License in [`LICENSE`](LICENSE). Material from
elsewhere keeps the license it arrived under: [`NOTICE`](NOTICE) lists each item and where its
license text sits. The legacy suite's license is [`legacy/LICENSE`](legacy/LICENSE).

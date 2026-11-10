@AGENTS.md

# Claude Code notes

`AGENTS.md`, imported above, is the project context. What follows applies to Claude Code only; where
the two could be read as disagreeing, `AGENTS.md` wins.

## Before acting

- **Nothing here executes a fixture.** A task that ends in "now run the tests" has misread where it
  is: the client that consumes these files runs them.
- **Read what is present from the tree** (`git ls-files -co --exclude-standard`; the plain form reads
  the index only), never from a count in a document, and derive counts by parsing the JSON.
- **These tests describe consensus rules of a live network.** An expected value chosen by hand is a
  claim about what the chain does.

## Before committing a claim

Every factual claim in a committed file or a commit message carries a measurement or a citation
checked in the session that writes it, never one inherited from a plan, a handoff or another
repository's documentation.

- **Run the check**, and keep its output with the reasoning.
- **Calibrate it.** A search that cannot report a negative has not reported a positive; state the
  control.
- **Name the ref.** Branches disagree, and a claim about a repository without a ref is a claim about
  whichever branch was checked out.
- **Prefer the narrower true claim.** "No fixture under this path asserts X" beats "the corpus does
  not do X" when only the first was measured.

A claim that cannot be checked cheaply is still publishable: say what is unverified and why.

## Working here

- **Read narrowly.** Fixture files reach hundreds of kilobytes; extract the key you need rather than
  reading a file whole.
- **Under `legacy/`, read only.** `.claude/settings.json` denies edits there and to `LICENSE`, and
  asks before edits to `NOTICE` and `.github/workflows/`.
- **Never reformat, re-key or rename fixture files in bulk.** The diff would cover thousands of files
  and hide any real change inside it.
- **Small batches, then verify.** Check one file, confirm its shape, then act on the rest.
- **In zsh, write a git revision built from a variable as `${ref}:path`.** `"$ref:src/..."` applies
  zsh's `:s` modifier, so `git show` reads the wrong argument. Run loops from a script file under
  bash, where an unquoted `$var` splits as written.

## Machine-local context

`CLAUDE.local.md` and the `.local/` directory are ignored and never reach a clone. Confirm that by
effect before writing to either, with a control that must not be ignored:

```bash
git -c core.excludesFile=/dev/null check-ignore --no-index -q -- CLAUDE.local.md && echo ignored || echo "NOT ignored"
git -c core.excludesFile=/dev/null check-ignore --no-index -q -- README.md && echo "check is broken" || echo "check discriminates"
```

## Model selection

Select by role, with a tier alias. Claude Code's
[model configuration page](https://code.claude.com/docs/en/model-config) lists the model each alias
selects.

| Alias | Use it for |
|---|---|
| `haiku` | counting, listing, mechanical edits to documentation |
| `sonnet` | documentation, provenance, structure, review |
| `opus` | fork semantics, difficulty and state rules, assessing a source, anything where a wrong answer is a claim about consensus |

## Response style

- Code and commands first; explain only what was asked about.
- Concise bullets over paragraphs, and tables for comparisons.
- No pleasantries, and do not repeat the prompt back.
- State what you verified and how. A count without the command that produced it is not a result.

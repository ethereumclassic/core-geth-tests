# Security Policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report it privately, through either channel:

1. **GitHub Security Advisories** (preferred)
   - Open this repository's [Security tab](https://github.com/ethereumclassic/core-geth-tests/security/)
     and click "Report a vulnerability"
   - Or open a
     [new security advisory](https://github.com/ethereumclassic/core-geth-tests/security/advisories/new)
     directly

2. **Email**
   - Send an email to **<security@ethereumclassic.net>**
   - Use PGP encryption if possible (key available on request)
   - Include "SECURITY" in the subject line

This repository holds test data, not a client, so a finding here usually shows a flaw in a client: a
test whose expected result reveals a client accepting what it must refuse, for example. Report that
through the same channels, along with anything else that should not be public, such as a credential
found in this repository's files or history. A vulnerability in the client itself belongs to
[`ethereumclassic/core-geth`'s security policy](https://github.com/ethereumclassic/core-geth/blob/main/SECURITY.md),
which uses the same address.

Include what you can: the affected file and commit, the network, the client it bears on, what an
attacker gains, and a reproduction if you have one. A report without a reproduction is still worth
sending.

With the ETC Cooperative's dissolution, Ethereum Classic stakeholders such as mining pools, exchanges
and service providers should use <security@ethereumclassic.net> as their point of contact. A person
answers it: one of the core developers who maintain this repository and have been with the network
since its inception.

### What to expect

Reports are acknowledged and triaged privately. Where a fix is warranted it is prepared under a GitHub
security advisory and disclosed once the fix is available. Reporters are credited unless they ask not
to be.

Published advisories for this repository are listed at
<https://github.com/ethereumclassic/core-geth-tests/security/advisories>.

## Automated and AI-assisted review

**Findings from automated or agent-assisted review go through the private channels above, the same as
any other.** Not a public issue, and not a pull request whose diff describes the defect before a fix
exists.

The reason is unchanged by how a finding was produced. What matters is whether it is exploitable
against nodes that are running now, and an automated review can surface something exploitable as
readily as a manual one. Volume does not change the obligation: a report with fifty findings and one
that is real is still a report that should arrive privately.

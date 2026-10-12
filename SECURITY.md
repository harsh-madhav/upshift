# Security policy

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately through GitHub instead:
[**Report a vulnerability**](https://github.com/harsh-madhav/upshift/security/advisories/new) (the repository's
**Security** tab → **Report a vulnerability**).

Include what you found, how to reproduce it, and what an attacker could do with it. We aim to reply within a
week. Once the issue is confirmed, we'll agree a fix and a disclosure date with you, and credit you in the advisory
unless you'd rather stay anonymous.

## Supported versions

Upshift is pre-1.0. Security fixes land on `main` and in the next release; older versions are not patched.

## Things to know when you run Upshift

- **It runs the target project's code.** Upshift installs the target repo's dependencies and runs its test suite,
  so only point it at repos you trust, as you would before running their tests yourself.
- **It sends code to your model provider.** The fix loop shares source files, test output and research with the
  provider you configure in `.env`.
- **Your repo is never edited in place.** Every run works on its own copy under `.upshift/runs/`. For a folder
  inside a git repo, only git-tracked files are copied, so untracked files such as local `.env` files stay behind.
- **Keys stay in `.env`.** It is git-ignored, and `python3 scripts/check_secrets.py --install` adds a pre-commit
  hook that blocks commits containing keys.

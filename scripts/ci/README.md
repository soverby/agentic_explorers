# CI security checks

`.github/workflows/security-scan.yml` runs on every PR to `main` and every push to `main`. It has three jobs. Each job is a separate required check.

| Job | Tool | What it checks |
| --- | --- | --- |
| `secrets` | gitleaks 8.30.1 (pinned binary, sha256 verified) | Every commit in the PR (`base..head`), so a secret that a later commit deletes still fails. On push: the pushed range. See [gitleaks hardening](#gitleaks-hardening). Output is redacted. |
| `malware` | ClamAV (Ubuntu package, fresh signatures) | Every committed file, exported from git objects by `export_tree.py` (not the checkout, so `.gitattributes` can not re-encode or hide files). A self-test first confirms that the signatures loaded (EICAR test string, in memory only). |
| `pii-and-content` | `scan_content.py` (Python 3 stdlib) | The unit tests in `tests/`, then the files that the PR changes (every file of the commit on push). Content comes from git blobs at the PR head, not from the working tree. Rules below. |

## Rules of `scan_content.py`

FAIL (the check fails):

| Rule id | Detects |
| --- | --- |
| `phone-number` | International numbers with a leading `+` and a valid country code (E.164, with or without separators; +1 and +7 need 11 digits); North American `(NXX) NXX-XXXX` / `NXX-NXX-XXXX` |
| `whatsapp-export` | WhatsApp chat export lines: iOS `[date, time] Name:`, Android `date, time - Name:`, `[yyyy/mm/dd hh:mm] Name:`, with or without the comma, after list/quote/table prefixes, split at any line break (CR, U+2028, U+0085); and the export's encryption notice |
| `us-ssn` | US SSN `AAA-GG-SSSS` (invalid ranges excluded); undashed or spaced only after the word SSN / social security |
| `credit-card` | 13-19 digit card numbers with a known brand prefix that pass the Luhn check |
| `iban` | IBANs (any letter case) with the correct length for the country that pass the mod-97 check |
| `private-key` | PEM / OpenSSH / PGP private key headers |
| `executable-binary` | ELF, Mach-O, PE, WebAssembly magic bytes; `.exe .dll .so .dylib .msi .scr .wasm` |
| `archive-file` | zip/7z/rar/gzip/bzip2/xz/zstd/tar/cab/ar/deb/rpm content or extension, and zip/rar/7z signatures anywhere inside any file (polyglots) |
| `image-polyglot` | An image (`.png .jpg .jpeg .gif .webp .ico .bmp .avif`) with data after its end marker, or a malformed structure. A file whose bytes do not match its image extension is scanned as text. |
| `large-file` | Files larger than 5 MB (not read) |
| `symlink-escape` | Symlinks that point outside the repository |
| `submodule` | Git submodule (gitlink, mode 160000) entries |
| `git-lfs` | Git LFS pointer files, and `filter=lfs` in any `.gitattributes` |
| `gitattributes-diff` | `.gitattributes` lines with `-diff`, `binary`, `diff=<driver>` or `-text` (they make `git diff`/`git log -p` hide content from review and from gitleaks) |
| `bidi-unicode` | U+202A-U+202E, U+2066-U+2069 (Trojan Source) |
| `zero-width-unicode` | U+200B-U+200D, U+2060, U+FEFF (a BOM at file start is allowed; ZWJ inside emoji and ZWNJ/ZWJ inside non-Latin words are allowed) |
| `invisible-unicode` | U+2061-U+2064, Hangul fillers U+115F/U+1160/U+3164/U+FFA0, U+180E, U+2800, U+034F, U+061C, U+FFF9-U+FFFB, U+17B4/U+17B5, U+E0100-U+E01EF, and variation selectors U+FE00-U+FE0F unless one follows an emoji/symbol base or forms a keycap |
| `unicode-tag` | U+E0000-U+E007F tag characters (invisible text); emoji subdivision flags are allowed |
| `reverse-shell` | `/dev/tcp` or `/dev/udp` redirects; netcat with an exec flag and a shell; interactive shell with fd redirects or piped to netcat; `mkfifo` with netcat; `socat exec:`; Python `pty.spawn` of a shell or `os.dup2` with a socket; PowerShell `TCPClient` <!-- scan-allow: reverse-shell --> |
| `pipe-to-shell` | curl/wget output piped (also via `\|&` or `tee`) to a shell (also `/bin/...`, `/usr/local/bin/...`, `sudo`, `env`) or to an interpreter that reads stdin; curl/wget to a file that the same line then runs; `sh -c "$(curl ...)"`; process substitution of curl/wget; PowerShell download to `iex` <!-- scan-allow: pipe-to-shell --> |
| `base64-exec` | base64 decode (`-d`, `-di`, `--decode`, ...) piped to a shell or interpreter, written to a file that the same line runs, or inside `eval`; `exec`/`eval` of `b64decode`/`atob`; PowerShell `-EncodedCommand`; `FromBase64String` with `iex` <!-- scan-allow: base64-exec --> |
| `crypto-miner` | xmrig, stratum mining URLs, cryptonight, `--donate-level`, known pool names <!-- scan-allow: crypto-miner --> |
| `credential-exfil` | On one line: an SSH private key / AWS credentials / netrc / git-credentials / docker / kube / gh config path (or an `.env` file passed with `@` or read with `cat`) together with a network send (curl, wget, nc, `requests.post`, `fetch(`, ...). `ssh -i` / `IdentityFile` and `.pub` keys are not matched. |

WARN (annotation only, the check passes):

| Rule id | Detects |
| --- | --- |
| `email-address` | Email addresses, except `example.com/.org/.net`, `*.example`, `*.test`, `users.noreply.github.com`, `noreply@` / `no-reply@`, and `git@github.com`-style remotes |
| `prompt-injection` | In `.md` files: phrases that tell a model to drop its earlier instructions or system prompt, "developer mode" jailbreak phrases, instructions to hide actions from the user, chat-template tokens |
| `svg-script` | `<script>`, `on*=` event handlers, or `javascript:` in `.svg` files |
| `soft-hyphen` | U+00AD (invisible when rendered) |
| `long-line` | FAIL. A line longer than 4096 characters that contains a shell, network, decode or miner keyword. The full script patterns are too slow on such lines, so padding a command past the limit fails instead of hiding it. Split the line, or ask the maintainer for a path allowlist entry (data files). |
| `binary-file` | Other binary content (NUL bytes). Text rules still run on the NUL-stripped content. BOM-less UTF-16 is detected and decoded as text. |

Valid images get the executable/archive/polyglot checks only. SVG is text and gets all rules.

The scanner never prints a matched value in full: it shows `****` plus the last 2-4 characters. Logs of a public repo are public.

## Overrides

**Line override** (only for `reverse-shell`, `pipe-to-shell`, `base64-exec`, `crypto-miner`, `email-address`, `prompt-injection`, `svg-script`, `soft-hyphen`). Put `scan-allow: <rule-id>` on the same line as the match, or on the line directly above it. Separate more ids with commas: `scan-allow: pipe-to-shell, email-address`. Use the comment syntax of the file, for example `<!-- scan-allow: pipe-to-shell -->` in Markdown or `# scan-allow: pipe-to-shell` in shell. The log shows each used override as a notice, and the job summary lists them all for the maintainer.

PII, secrets, credential-exfil, binaries, archives, images, hidden Unicode, `.gitattributes`, LFS, symlinks and submodules do **not** accept a line override (the annotation says so). Only a path entry can skip them.

**Path allowlist.** `.github/scan-allowlist.txt` has one `<rule-id> <glob>` entry per line. It skips that rule for matching paths. `*` also matches `/`. An unknown rule id is an error. Use this only for files such as test fixtures. The maintainer reviews every change to this file.

## gitleaks hardening

`gitleaks_ci.py` runs gitleaks so that a PR can not weaken it:

- Config: the upstream default `config/gitleaks.toml` of tag v8.30.1 (sha256 pinned in the workflow) with every allowlist `paths` entry removed: the global one (`.svg`, `.pdf`, lock files, `node_modules/`, `vendor/`, ...) and the rule-level ones (for example `@octokit/auth-token/README.md` for `github-pat`). A rule allowlist that has nothing left, or that combined paths with `condition = "AND"`, is removed. Regexes and stopwords stay. Passed with `--config`, so a repo `.gitleaks.toml` has no effect.
- Every `.gitleaksignore` in the working tree is deleted before the scan (gitleaks reads it from the scanned directory even with `--gitleaks-ignore-path`). `gitleaks:allow` comments are ignored. `GITLEAKS_*` environment variables are cleared.
- `.git/info/attributes` is set to `* diff -filter -text -working-tree-encoding`, and the log options include `--text`, so a PR `.gitattributes` (`-diff`, `binary`, `diff=driver`) can not turn diffs into "Binary files differ".
- `--diff-merges=first-parent`, so content that only a merge commit adds is scanned.
- A self-test builds a repo that uses all of these bypasses (tokens in skipped paths, `* -diff` attributes, a token added in a merge commit, ignore files in the root, a sub-directory and as a symlink, a permissive `.gitleaks.toml`). It fails unless `github-pat` is reported for every planted token.

## Run locally

```sh
python3 scripts/ci/scan_content.py --all                        # working tree: tracked and untracked (not ignored) files
python3 scripts/ci/scan_content.py path/to/file.md              # specific files
python3 scripts/ci/scan_content.py --base origin/main --head HEAD   # committed changes on your branch
python3 scripts/ci/scan_content.py --rev HEAD                   # every file of a commit
python3 -m unittest discover scripts/ci/tests                   # scanner tests
```

Exit code: 0 = no failures (warnings allowed), 1 = at least one failure, 2 = usage or configuration error.

With gitleaks installed, also run the real-gitleaks test: build a config with `python3 scripts/ci/gitleaks_ci.py config <default.toml> <sha256> /tmp/gl.toml`, then `GITLEAKS_BIN=$(command -v gitleaks) GITLEAKS_TEST_CONFIG=/tmp/gl.toml python3 -m unittest discover scripts/ci/tests`.

## Limits

- The PR workflow runs the PR's own copy of this workflow and of the scripts in `scripts/ci/`. A PR that edits `.github/` or `scripts/ci/` can weaken the checks for itself. The maintainer must review changes to those paths before approving.
- `pii-and-content` and `malware` check the final content of the PR, not intermediate commits (squash merge keeps those out of `main`). `secrets` covers intermediate commits for credentials.
- Pattern rules are line-local and meant to catch obvious cases with few false positives. They do not replace review.

---
name: commit
description: Commit to this PUBLIC repository. Runs the public-safety scan and the tests as hard gates before anything is staged. Triggers on /commit or when asked to commit changes here.
---

# Commit Skill - public repository

**This repository is PUBLIC and must stay that way.** One credential, one client name, one
person's address in a commit and it has to be taken private - and the history keeps the leak
long after the file is deleted. `git push` is the irreversible step. Everything below happens
before it.

Invoke with `/commit`, or when the user asks to commit changes.

## Scope

Default: only files changed in this session. `all`: every uncommitted change. Named files:
only those. Never `git add .` or `git add -A` - stage by name, so nothing arrives unnoticed.

## STEP 1 - Gather

```
git status
git log --oneline -5
```

Read the recent messages and match their style.

## STEP 2 - THE PUBLIC-SAFETY SCAN (mandatory, no exceptions)

```
python tools/scan_public.py
```

It exits non-zero on any finding, in six categories: **media** (footage or captures - this
repository holds tools, never material), **secrets**, **personal** (emails, phone numbers,
denylisted names), **internal** (home paths, machine names, private hosts, job ids),
**attribution** (an assistant named as an author), **encoding** (any non-ASCII byte).

**Read the output, do not just read the exit code.**

* `NO DENYLIST` in the output means site-specific names were **not checked**. That is a broken
  instrument, not a clean run, and it exits non-zero for that reason alone. Create `.denylist`
  (gitignored) or set `CCVIDEO_DENYLIST` and run it again.
* A finding is fixed by **removing the thing**, never by widening a pattern or adding a term
  exemption to make the message go away. The two exemptions that exist - the scanner's own
  pattern definitions, and the copyright holder named in LICENSE - are printed on every run so
  they stay visible. Adding a third is a decision for the owner, not for a session trying to
  get a commit through.
* If a finding is genuinely a false positive, say so to the user and get an answer. Do not
  decide it alone.

**If anything is found, STOP.** Do not stage, do not commit, do not push.

## STEP 3 - Tests

```
python -m pytest tests -q
```

All must pass. Several of them guard things that fail expensively and silently rather than
loudly:

* the **voice cache key** is frozen. If that test fails, the question is not "what is the new
  digest" - it is whether the voice genuinely changed. A wrong answer re-buys every second of
  narration in every project using this library.
* `clean_edge` **must not** import the boundary rule. A checker that shares an oracle with the
  thing it checks reports success in exactly the case you needed a failure. Sixteen published
  shorts are why that test exists.

If a test fails, STOP and fix it. Never commit past a red test.

## STEP 4 - Present the plan

Show the user:

* the files to be committed, by name
* the scan result, including which exemptions were applied
* the test result
* the proposed commit message

Message format:

```
type: short description

why, if it is not obvious
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.

**Never sign a commit.** No `Co-Authored-By`, no "Generated with", no assistant named
anywhere - not in the message, not in a pull request, not in an issue. This is the owner's
repository and his client deliverable. The scan checks the files; check your own message
yourself before you write it.

## STEP 5 - Wait for approval

Do not proceed until the user says yes. Committing was not authorised by them asking you to
write code; it is authorised each time, explicitly.

## STEP 6 - Commit and push

Stage each file by name. Commit with a heredoc:

```
git commit -m "$(cat <<'EOF'
message here
EOF
)"
```

Then `git push`. If it is rejected for remote changes, `git pull --rebase` and push again.
Never `push --force`, never `--no-verify`, never `reset --hard`.

## STEP 7 - Finish the job, or say plainly that it is not finished

**Pushed is not done.** A pushed side branch is a backup: it is in no release, nothing depends
on it, and it rots as main moves.

If the commit went to `main`, the job is done. Say so.

If it went to a side branch, the job is NOT done:

1. Open the pull request if one does not exist.
2. When it merges, delete the branch on both sides.
3. Remove its worktree, if it had one.
4. Park the checkout back on `main` and pull.

Report IN PROGRESS and name the outstanding step. Never delete a branch carrying a commit that
is not already on `origin/main` - prove it with `git rev-list --count origin/main..<branch>`,
which must be `0`.

## If the scan ever fails on something already pushed

Deleting the file is not enough - the history still carries it. Tell the user immediately and
plainly, and treat it as their decision: making the repository private, rewriting history, and
rotating whatever leaked are all their calls, not a session's.

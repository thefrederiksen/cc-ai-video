"""Refuse to publish anything that does not belong in a PUBLIC repository.

This repository is public and is meant to stay that way. One credential, one client name, one
person's email address in a commit and it has to be taken private - and the history keeps the
leak even after the file is deleted. So the check runs BEFORE the commit, not after.

    python tools/scan_public.py            # everything git tracks, plus what is staged
    python tools/scan_public.py --all      # every file in the tree, tracked or not

Exit 1 on any finding. Nothing here is advisory.

WHAT IT LOOKS FOR

  media        A video, an audio file or a screen capture. This library is TOOLS. Footage is
               somebody's material, it is large, and a frame of a screen recording carries
               whatever happened to be on that screen.
  secrets      API keys, tokens, private keys, connection strings, passwords.
  personal     Email addresses, phone numbers, and any term in the local denylist.
  internal     Machine names, internal hosts, absolute paths into somebody's home directory,
               and identifiers that only mean something inside one company.
  attribution  An assistant's name in the code, a commit or a document. These are the owner's
               repositories and his client deliverables.
  encoding     Any non-ASCII byte. The house rule everywhere, and it is also how a smuggled
               homoglyph would hide.

THE LOCAL DENYLIST

Site-specific terms - a company's name, a client, a person - must not be written into a public
repository even as a list of things to avoid, because that publishes them. Put them one per
line in a file named by CCVIDEO_DENYLIST, or in `.denylist` beside this repository, which is
gitignored. A `#` starts a comment. If no denylist is present the scan says so plainly rather
than reporting a clean run: an absent list is a missing instrument, not an all-clear.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MEDIA_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".wmv",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp",
    ".srt", ".ass", ".vtt",
}
# A test fixture may legitimately need a tiny image. Nothing does yet; when something does,
# it goes here explicitly and by name, so adding one is a decision somebody made.
MEDIA_ALLOWED = set()

SECRET_PATTERNS = [
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("PostHog key", re.compile(r"\bph[cx]_[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer literal", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{24,}")),
    ("assigned secret", re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']")),
    ("connection string", re.compile(r"(?i)(password|pwd)\s*=\s*[^;\s\"']{6,};")),
]

PERSONAL_PATTERNS = [
    ("email address", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone number", re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{9,}\d(?![\w.])")),
]
# Addresses that are documentation, not a person.
EMAIL_ALLOWED = re.compile(r"(?i)@(example\.(com|org|net)|localhost)$")

INTERNAL_PATTERNS = [
    ("home directory path", re.compile(r"(?i)\b[a-z]:[\\/]users[\\/][^\\/\s\"']+")),
    ("unix home path", re.compile(r"/home/[a-z0-9_\-]+/")),
    ("repository path", re.compile(r"(?i)\b[a-z]:[\\/]repos[a-z]*[\\/]")),
    ("UNC share", re.compile(r"(?<![\w:])\\\\[A-Za-z0-9._\-]+\\[A-Za-z0-9._$\-]+")),
    ("private network host", re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("tailscale host", re.compile(r"\b[a-z0-9\-]+\.ts\.net\b")),
    ("session or job id", re.compile(r"\b(?:cj|sess|ses)_[0-9a-f]{6,}\b")),
]

ATTRIBUTION_PATTERNS = [
    ("attribution trailer", re.compile(r"(?i)co-authored-by")),
    ("generated-with line", re.compile(r"(?i)generated with .{0,40}(claude|codex|copilot|cursor)")),
    ("assistant named as author", re.compile(
        r"(?i)\b(authored|written|created|generated)\s+by\s+(claude|anthropic|chatgpt|copilot)")),
]

# Windows font paths are a platform fact, not a private path.
PATH_ALLOWED = re.compile(r"(?i)^[a-z]:[\\/]windows[\\/]", )

TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".json", ".cfg", ".ini", ".yml", ".yaml",
                 ".sh", ".ps1", ".cmd", ".bat", ""}

# This file holds the patterns, so it matches every one of them. It is exempted from the
# CONTENT checks only, and the exemption is announced on every run - a silent self-exemption
# is how a scanner ends up not scanning the one file somebody edited to disable it. Encoding
# and file-type checks still apply to it.
SELF = "tools/scan_public.py"

# Files whose JOB is to state the publication rules have to be able to NAME what they forbid.
# A document that cannot write the words "the forbidden trailer" cannot teach anyone to avoid
# it. Only the ATTRIBUTION category is relaxed for these, and only for these: a real key or a
# real person's address in one of them still fails, which is the part that actually matters.
# Keep this list to files that define the rules. It is printed on every run.
RULE_FILES = {
    ".claude/skills/commit/skill.md",
}

# A licence has to name its copyright holder; that is the point of a licence. This is the ONE
# place the owner's name is allowed, and naming it anywhere else still fails.
TERM_EXEMPTIONS = {
    "LICENSE": {"center consulting"},
}


def tracked_files(scan_all):
    if scan_all:
        return [p for p in ROOT.rglob("*")
                if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts]
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co",
                          "--exclude-standard"], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("git ls-files failed - is this a git repository?\n" + out.stderr)
    return [ROOT / line for line in out.stdout.splitlines() if line.strip()]


def load_denylist():
    """(terms, where it came from). An absent list is reported, never treated as empty."""
    configured = os.environ.get("CCVIDEO_DENYLIST", "").strip()
    path = Path(configured) if configured else ROOT / ".denylist"
    if not path.exists():
        return None, path
    terms = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            terms.append(line)
    return terms, path


def scan_text(path, text, denylist, findings):
    exempt_terms = {t.lower() for t in TERM_EXEMPTIONS.get(path.as_posix(), set())}

    def report(kind, detail, line_no):
        findings.append((path, line_no, kind, detail))

    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                report("secrets", label, number)
        for label, pattern in PERSONAL_PATTERNS:
            for hit in pattern.finditer(line):
                value = hit.group(0)
                if label == "email address" and EMAIL_ALLOWED.search(value):
                    continue
                if label == "phone number" and _looks_like_version_or_time(line, value):
                    continue
                report("personal", "%s: %s" % (label, _mask(value)), number)
        for label, pattern in INTERNAL_PATTERNS:
            for hit in pattern.finditer(line):
                if PATH_ALLOWED.match(hit.group(0)):
                    continue
                report("internal", "%s: %s" % (label, hit.group(0)[:60]), number)
        if path.as_posix() not in RULE_FILES:
            for label, pattern in ATTRIBUTION_PATTERNS:
                if pattern.search(line):
                    report("attribution", label, number)
        for term in (denylist or []):
            if term.lower() in exempt_terms:
                continue
            if re.search(r"(?i)" + re.escape(term), line):
                report("personal", "denylisted term: %s" % _mask(term), number)


def _looks_like_version_or_time(line, value):
    """A timestamp, a duration table or a version string is not a telephone number."""
    if re.search(r"(?i)(version|timecode|--\>|\d\d:\d\d:\d\d)", line):
        return True
    return bool(re.fullmatch(r"[\d\s.\-()]+", value) and value.count(".") >= 2)


def _mask(value):
    if len(value) <= 6:
        return value[0] + "*" * (len(value) - 1)
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true",
                        help="scan every file in the tree, not only what git would carry")
    args = parser.parse_args()

    denylist, denylist_path = load_denylist()
    findings = []
    files = tracked_files(args.all)

    for path in files:
        rel = path.relative_to(ROOT)
        if path.suffix.lower() in MEDIA_SUFFIXES and str(rel) not in MEDIA_ALLOWED:
            findings.append((path, 0, "media",
                             "a %s file - this repository holds tools, never footage"
                             % path.suffix))
            continue
        try:
            raw = path.read_bytes()
        except OSError as error:
            findings.append((path, 0, "unreadable", str(error)))
            continue

        non_ascii = [b for b in raw if b > 127]
        if non_ascii:
            findings.append((path, 0, "encoding",
                             "%d non-ASCII bytes" % len(non_ascii)))
        if path.suffix.lower() not in TEXT_SUFFIXES:
            findings.append((path, 0, "media",
                             "unexpected file type %r - allow it explicitly or remove it"
                             % path.suffix))
            continue
        if rel.as_posix() == SELF:
            continue        # announced below; content checks only, encoding already applied
        scan_text(rel, raw.decode("utf-8", errors="replace"), denylist, findings)

    print("scanned %d files" % len(files))
    print("content checks skipped for %s (it defines the patterns)" % SELF)
    for where, terms in sorted(TERM_EXEMPTIONS.items()):
        print("term exemption: %s may name %s" % (where, ", ".join(sorted(terms))))
    for where in sorted(RULE_FILES):
        print("attribution exemption: %s states the rules, so it may name them" % where)
    if denylist is None:
        print("NO DENYLIST at %s - site-specific names were NOT checked. This is a missing\n"
              "instrument, not a clean result. Create it, or set CCVIDEO_DENYLIST."
              % denylist_path)
    else:
        print("denylist: %d terms from %s" % (len(denylist), denylist_path))

    if not findings:
        print("PASS - nothing found that would force this repository private.")
        return 1 if denylist is None else 0

    print("\nFAIL - %d finding(s). None of this may be committed to a public repository."
          % len(findings))
    for path, line, kind, detail in sorted(findings, key=lambda f: (f[2], str(f[0]), f[1])):
        where = "%s:%d" % (path, line) if line else str(path)
        print("  %-11s %-44s %s" % (kind, where, detail))
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Real photographs for an illustrated video, from Wikimedia Commons, with their credits.

A generated picture of "a 1950s lab" says nothing; a photograph of THE machine says the thing
itself. Commons holds public-domain and freely licensed photographs of most of the people,
places and machines a history video names, and every file carries its license and author.

Two rules this module enforces rather than leaves to the caller:

  FREE LICENSES ONLY. Public domain, CC0, CC BY and CC BY-SA are accepted. Anything else -
      fair use, non-free, no-derivatives, non-commercial, unknown - is refused, because a
      YouTube video is a commercial, derived use.
  THE CREDIT TRAVELS WITH THE FILE. fetch() writes <photo>.json beside the picture with the
      title, author, license and source address. credits() gathers them for the video's
      description. A photograph without its credit file is not ours to use.
"""

import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
AGENT = "ccvideo/0.1 (https://github.com/thefrederiksen/cc-ai-video)"

FREE = re.compile(r"^(public domain|pd\b|cc0|cc[- ]by(-sa)?[- ]\d(\.\d)?( [a-z]{2,})?|cc[- ]by(-sa)?)$", re.I)


PICTURES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")


def _get(params):
    url = API + "?" + urllib.parse.urlencode(dict(params, format="json"))
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    return json.load(urllib.request.urlopen(req, timeout=60))


def _text(value):
    """Plain text from Commons' HTML metadata."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def is_free(license_name):
    return bool(FREE.match((license_name or "").strip()))


def _info(page):
    ii = page["imageinfo"][0]
    m = ii.get("extmetadata", {})
    val = lambda k: _text(m.get(k, {}).get("value"))
    return {
        "title": page["title"],
        "width": ii.get("width"), "height": ii.get("height"),
        "license": val("LicenseShortName"),
        "license_url": val("LicenseUrl"),
        "author": val("Artist") or val("Credit") or "unknown",
        "description": val("ImageDescription")[:200],
        "date": val("DateTimeOriginal")[:40],
        "source": ii.get("descriptionurl"),
        "thumb": ii.get("thumburl"),
        "free": is_free(val("LicenseShortName")),
    }


def search(query, limit=12, width=2400):
    """Candidate photographs for `query`, free ones first. Nothing is downloaded."""
    d = _get({"action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query,
              "gsrlimit": limit, "prop": "imageinfo", "iiprop": "url|size|extmetadata",
              "iiurlwidth": width})
    pages = d.get("query", {}).get("pages", {}).values()
    found = [_info(p) for p in pages
             if p.get("imageinfo") and p["title"].lower().endswith(PICTURES)]
    return sorted(found, key=lambda f: (not f["free"], -(f["width"] or 0)))


def fetch(title, out, width=2400):
    """Download one Commons file (at most `width` wide) and write its credit beside it."""
    if not title.startswith("File:"):
        title = "File:" + title
    d = _get({"action": "query", "titles": title, "prop": "imageinfo",
              "iiprop": "url|size|extmetadata", "iiurlwidth": width})
    page = next(iter(d["query"]["pages"].values()))
    if "imageinfo" not in page:
        raise SystemExit("no such file on Commons: %s" % title)
    info = _info(page)
    if not info["free"]:
        raise SystemExit("REFUSED %s - license %r is not public domain, CC0, CC BY or CC BY-SA"
                         % (title, info["license"]))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(info["thumb"], headers={"User-Agent": AGENT})
    out.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    info["credit"] = credit_line(info)
    out.with_suffix(out.suffix + ".json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def credit_line(info):
    """'Title - Author, License, via Wikimedia Commons' - the attribution a CC license asks for."""
    name = info["title"].replace("File:", "").rsplit(".", 1)[0]
    author = info["author"]
    if len(author) > 60:
        author = author[:57] + "..."
    return "%s - %s, %s, via Wikimedia Commons" % (name, author, info["license"])


def credits(folder):
    """Every credit in a folder of fetched photographs, in file order. A picture without its
    credit file is an error: it cannot go in a published video."""
    folder = Path(folder)
    lines, missing = [], []
    for img in sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")):
        meta = img.with_suffix(img.suffix + ".json")
        if not meta.exists():
            missing.append(img.name)
            continue
        info = json.loads(meta.read_text(encoding="utf-8"))
        lines.append("%s: %s  %s" % (img.name, info["credit"], info["source"]))
    if missing:
        raise SystemExit("no credit file for: %s - fetch them with 'ccvideo photo fetch'"
                         % ", ".join(missing))
    return lines

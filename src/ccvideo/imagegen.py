"""Generated scene images for an illustrated narration.

For moods and places - a street at night, an empty lab, a board game under a lamp. Not for
the faces of real people: a generated face of a real person is a made-up picture of him, and
on a history video that is the one thing a viewer will check. Name a real person with a card
instead, or use a real photograph.

The key is read from the environment only. On this machine cc-secrets supplies it:
    cc-secrets run deepinfra-api-key -- ccvideo image --prompt "..." --out x.png
"""

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://api.deepinfra.com/v1/openai/images/generations"
MODEL = "black-forest-labs/FLUX-1-schnell"
KEY = "DEEPINFRA_API_KEY"


def generate(prompt, out, size="1792x1024", model=MODEL):
    key = os.environ.get(KEY)
    if not key:
        raise SystemExit("%s is not set. Run through cc-secrets: "
                         "cc-secrets run deepinfra-api-key -- ccvideo image ..." % KEY)
    body = json.dumps({"model": model, "prompt": prompt, "size": size, "n": 1,
                       "response_format": "b64_json"}).encode()
    request = urllib.request.Request(ENDPOINT, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        reply = json.load(urllib.request.urlopen(request, timeout=180))
    except urllib.error.HTTPError as err:
        raise SystemExit("image generation refused (HTTP %d): %s"
                         % (err.code, err.read().decode("utf-8", "replace")[:300]))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(reply["data"][0]["b64_json"]))
    return out

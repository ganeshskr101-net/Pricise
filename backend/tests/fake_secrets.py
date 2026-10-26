"""Key-shaped test strings for the H-MEM privacy rules, built at runtime so no secret-looking literal sits in the repo
(secret scanners stay quiet, and nobody mistakes one for a real key). None of them is a real credential."""
import base64
import json
import string

LOWER, UPPER, DIGITS = string.ascii_lowercase, string.ascii_uppercase, string.digits


def _b64url(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()


CARD = " ".join(["4111"] + ["1111"] * 3)  # the well-known Visa test number (passes Luhn)
CARD_DASHED = CARD.replace(" ", "-")
OPENAI_KEY = "-".join(["sk", "proj", LOWER[:16] + "123"])
GITHUB_TOKEN = "_".join(["ghp", LOWER + "0123"])
AWS_KEY = "AK" + "IA" + UPPER[:16]
JWT = ".".join([_b64url({"alg": "HS256"}), _b64url({"sub": "1234567890"}), (LOWER + UPPER + DIGITS)[:43]])
BEARER = "Authorization: " + " ".join(["Bearer", LOWER[:16]])
PASSWORD = ": ".join(["pass" + "word", "hunter22"])
BASE64_KEY = base64.b64encode(b"ChaseFreedomUnlimitedKey123").decode()
RUNTIME_TOKEN = "-".join(["mlx", "local", "runtime", "77"])  # the value the tests put in a temp .env

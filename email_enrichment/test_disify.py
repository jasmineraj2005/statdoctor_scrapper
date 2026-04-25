"""
Dry-run test for Disify email verification API.
Run this before disify_verify.py to confirm the API works and responses parse correctly.
"""
import time
import requests

DISIFY_URL = "https://www.disify.com/api/email/{email}"
DELAY_S = 1.5

TEST_CASES = [
    ("a.real.format@alfred.org.au",          "hospital — expect verified or unverified (real domain)"),
    ("fake999xyz@alfred.org.au",             "fake user, real domain — expect failed or catch_all"),
    ("test@gmail.com",                       "gmail — expect domain valid"),
    ("test@mailinator.com",                  "disposable domain — expect disposable=true"),
    ("firstname.lastname@health.nsw.gov.au", "gov domain — expect domain valid"),
]


def classify(resp: dict) -> str:
    fmt = resp.get("format", False)
    domain_ok = resp.get("domain", False)
    dns_ok = resp.get("dns", False)
    disposable = resp.get("disposable", False)

    if not fmt:
        return "failed"
    if not domain_ok or not dns_ok:
        return "failed"
    if disposable:
        return "failed"
    return "verified"


def main():
    print(f"{'EMAIL':<45} {'CONFIDENCE':<12} RAW RESPONSE")
    print("-" * 100)
    for email, note in TEST_CASES:
        try:
            r = requests.get(DISIFY_URL.format(email=email), timeout=10)
            r.raise_for_status()
            data = r.json()
            confidence = classify(data)
            print(f"{email:<45} {confidence:<12} {data}  # {note}")
        except Exception as e:
            print(f"{email:<45} {'ERROR':<12} {e}")
        time.sleep(DELAY_S)

    print()
    print("Done. If all rows show a confidence label (not ERROR), the API is working.")


if __name__ == "__main__":
    main()

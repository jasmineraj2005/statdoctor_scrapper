"""
AHPRA Medical Practitioner Scraper — generic state version
Usage: python3 scraper_state.py --state QLD --pc-start 4000 --pc-end 4999 --canary-suburb Brisbane --canary-pc 4000
"""

import argparse, csv, os, random, time
import requests
from bs4 import BeautifulSoup

BASE_URL      = "https://www.ahpra.gov.au"
SEARCH_URL    = f"{BASE_URL}/Registration/Registers-of-Practitioners.aspx"
LOCATIONS_API = f"{BASE_URL}/api/Search/GetLocations"
MAX_RESULTS   = 50
ZERO_STREAK_THRESHOLD = 3
BACKOFF_SECONDS       = 90

CSV_FIELDS = [
    "practitioner_id", "name", "profession", "division",
    "registration_type", "speciality", "location", "postcode_searched",
]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")


def delay(lo=0.8, hi=1.5):
    time.sleep(random.uniform(lo, hi))


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en-AU"})
    for _ in range(3):
        try:
            if s.get(SEARCH_URL, timeout=20).status_code == 200:
                return s
        except Exception:
            time.sleep(2)
    return s


def canary_check(sess, state, suburb, postcode):
    try:
        data = {
            "health-profession": "Medical Practitioner",
            "state": state, "suburb": suburb, "postcode": str(postcode),
            "suburb-postcode": f"{suburb}, {state}, {postcode}",
            "phonetic-suggested": "true", "phonetic-direct-match": "",
            "event-category": "Practitioner Search",
            "event-action": "On Search Results page",
            "name-reg": "", "name-reg-detail": "",
            "sex-filters": "", "language-filters": "",
            "load-more-page-num": "", "search-results-count": "",
            "practitioner-row-id": "",
        }
        r = sess.post(SEARCH_URL, data=data, timeout=30)
        soup = BeautifulSoup(r.text, "lxml")
        c = soup.find("input", {"name": "search-results-count"})
        return int(c["value"]) > 0 if c and c.get("value", "").isdigit() else False
    except Exception:
        return False


def wait_for_unblock(state, suburb, postcode):
    wait = BACKOFF_SECONDS
    for attempt in range(10):
        print(f"  [Rate limited — waiting {wait:.0f}s (attempt {attempt+1})]", flush=True)
        time.sleep(wait)
        sess = new_session()
        if canary_check(sess, state, suburb, postcode):
            print("  [Unblocked — resuming]", flush=True)
            return sess
        wait = min(wait * 1.5, 600)
    print("  [WARNING: Still blocked after max retries, continuing anyway]", flush=True)
    return new_session()


def get_suburbs(sess, postcode, state):
    for _ in range(3):
        try:
            r = sess.post(LOCATIONS_API, data={"term": str(postcode), "state": state}, timeout=15)
            if r.status_code == 200:
                return [v for v in r.json().get("Values", []) if v.get("suburb", "N/A") != "N/A"]
        except Exception:
            time.sleep(2)
    return []


def post_search(sess, suburb, postcode, state, gender=""):
    data = {
        "health-profession": "Medical Practitioner",
        "state": state, "suburb": suburb, "postcode": str(postcode),
        "suburb-postcode": f"{suburb}, {state}, {postcode}",
        "phonetic-suggested": "true", "phonetic-direct-match": "",
        "event-category": "Practitioner Search",
        "event-action": "On Search Results page",
        "name-reg": "", "name-reg-detail": "",
        "sex-filters": "", "language-filters": "",
        "load-more-page-num": "", "search-results-count": "",
        "practitioner-row-id": "",
    }
    if gender:
        data["gender-select"] = gender
    for attempt in range(3):
        try:
            r = sess.post(SEARCH_URL, data=data, timeout=30)
            if r.status_code == 200:
                return r.text
            if r.status_code == 403:
                print("    403 — refreshing session")
                sess.cookies.clear()
                sess.get(SEARCH_URL, timeout=20)
                delay()
        except Exception as e:
            if attempt == 2:
                print(f"    Request failed: {e}")
            time.sleep(3)
    return None


def parse(html, postcode, state):
    soup = BeautifulSoup(html, "lxml")
    c = soup.find("input", {"name": "search-results-count"})
    total = int(c["value"]) if c and c.get("value", "").isdigit() else 0
    rows = []
    for div in soup.find_all("div", attrs={"data-practitioner-row-id": True}):
        pid = div["data-practitioner-row-id"]
        a = div.find("a")
        name = a.get_text(" ", strip=True) if a else ""
        cols = div.find_all("div", class_="search-results-table-col")
        profession = ""
        if len(cols) >= 2:
            profession = cols[1].get_text(" ", strip=True)
            if profession.startswith("Profession"):
                profession = profession[10:].strip()
        location = ""
        for col in reversed(cols):
            t = col.get_text(" ", strip=True)
            if state in t:
                location = t.replace("Location", "").strip() if t.startswith("Location") else t
                break
        span_rows = div.find_all("div", class_="col-span-row")
        if span_rows:
            for sr in span_rows:
                rows.append({
                    "practitioner_id": pid, "name": name, "profession": profession,
                    "division": _col(sr, "division"),
                    "registration_type": _col(sr, "reg-type"),
                    "speciality": _col(sr, "speciality"),
                    "location": location, "postcode_searched": postcode,
                })
        else:
            rows.append({
                "practitioner_id": pid, "name": name, "profession": profession,
                "division": "", "registration_type": "", "speciality": "",
                "location": location, "postcode_searched": postcode,
            })
    return rows, total


def _col(parent, cls):
    el = parent.find("div", class_=cls)
    if not el:
        return ""
    t = el.get_text(" ", strip=True)
    for lbl in ["Division", "Registration Type", "Registration type", "Specialty:", "Specialty"]:
        t = t.replace(lbl, "").strip()
    return t


def append_csv(rows, output_file):
    if not rows:
        return
    exists = os.path.exists(output_file)
    with open(output_file, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def load_seen(output_file):
    s = set()
    if os.path.exists(output_file):
        with open(output_file, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                s.add(r["practitioner_id"])
    return s


def load_done(progress_file):
    s = set()
    if os.path.exists(progress_file):
        with open(progress_file) as f:
            for line in f:
                if line.strip().isdigit():
                    s.add(int(line.strip()))
    return s


def mark_done(pc, progress_file):
    with open(progress_file, "a") as f:
        f.write(f"{pc}\n")


def collect(sess, suburb, postcode, state, seen, output_file):
    html = post_search(sess, suburb, postcode, state)
    if not html:
        return 0, 0
    rows, total = parse(html, postcode, state)
    new = [r for r in rows if r["practitioner_id"] not in seen]
    for r in new:
        seen.add(r["practitioner_id"])
    append_csv(new, output_file)
    added = len(set(r["practitioner_id"] for r in new))
    if total > MAX_RESULTS:
        for gender in ["Male", "Female"]:
            delay(1.5, 2.5)
            html2 = post_search(sess, suburb, postcode, state, gender=gender)
            if not html2:
                continue
            rows2, _ = parse(html2, postcode, state)
            new2 = [r for r in rows2 if r["practitioner_id"] not in seen]
            for r in new2:
                seen.add(r["practitioner_id"])
            append_csv(new2, output_file)
            added += len(set(r["practitioner_id"] for r in new2))
        delay(3.0, 5.0)
        print(f"  {suburb}: {added} doctors (dense: {total}, gender-split)", flush=True)
    else:
        print(f"  {suburb}: {added} doctors (of {total})", flush=True)
    return added, total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state",         required=True,  help="State code e.g. QLD")
    parser.add_argument("--pc-start",      required=True,  type=int, help="First postcode e.g. 4000")
    parser.add_argument("--pc-end",        required=True,  type=int, help="Last postcode e.g. 4999")
    parser.add_argument("--canary-suburb", required=True,  help="Suburb for canary check e.g. Brisbane")
    parser.add_argument("--canary-pc",     required=True,  type=int, help="Postcode for canary check e.g. 4000")
    args = parser.parse_args()

    state        = args.state
    script_dir   = os.path.dirname(os.path.abspath(__file__))
    db_dir       = os.path.join(script_dir, "..", "db_ARPHA")
    os.makedirs(db_dir, exist_ok=True)
    output_file   = os.path.join(db_dir, f"{state.lower()}_practitioners.csv")
    progress_file = os.path.join(script_dir, f"{state.lower()}_scrape_progress.txt")

    postcodes = list(range(args.pc_start, args.pc_end + 1))
    done      = load_done(progress_file)
    remaining = [p for p in postcodes if p not in done]
    seen      = load_seen(output_file)

    print(f"\n{'='*50}", flush=True)
    print(f"  {state} — {len(remaining)}/{len(postcodes)} postcodes remaining", flush=True)
    print(f"  Practitioners so far: {len(seen)}", flush=True)
    print(f"  Output: {output_file}", flush=True)
    print(f"{'='*50}\n", flush=True)

    sess = new_session()
    zero_streak = 0

    for i, pc in enumerate(remaining):
        suburbs = get_suburbs(sess, pc, state)
        if not suburbs:
            mark_done(pc, progress_file)
            continue

        names = [s["suburb"] for s in suburbs]
        print(f"[{i+1}/{len(remaining)}] {pc} -> {names}", flush=True)

        postcode_total = 0
        for loc in suburbs:
            delay()
            try:
                added, total = collect(sess, loc["suburb"], pc, state, seen, output_file)
                postcode_total += total
            except Exception as e:
                print(f"  ERROR {loc['suburb']}: {e}", flush=True)

        mark_done(pc, progress_file)

        if postcode_total == 0:
            zero_streak += 1
            if zero_streak >= ZERO_STREAK_THRESHOLD:
                if not canary_check(sess, state, args.canary_suburb, args.canary_pc):
                    print(f"  [{zero_streak} zeros + canary failed — backing off]", flush=True)
                    sess = wait_for_unblock(state, args.canary_suburb, args.canary_pc)
                zero_streak = 0
        else:
            zero_streak = 0

        delay(6.0, 10.0)

    print(f"\n[{state}] Done! {len(seen)} unique practitioners -> {output_file}\n", flush=True)


if __name__ == "__main__":
    main()

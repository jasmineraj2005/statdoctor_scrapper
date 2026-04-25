#!/usr/bin/env python3
"""
Dry-test harness for SMTP RCPT probing.

Runs in two phases:
  --preflight    : just checks if port 25 outbound works from this machine.
                   Does not need FreeDNS.
  --probe HELO   : full RCPT probe (canary + control) against a hardcoded
                   list of hospital domains, using HELO as the sender identity.

Example:
  python smtp_probe_test.py --preflight
  python smtp_probe_test.py --probe arpha-probe.mooo.com
"""
import argparse
import random
import socket
import smtplib
import string
import sys
import time
from datetime import datetime

import dns.resolver


# Hospital domains to test. Mix of public tertiary, teaching, children's, community.
TEST_DOMAINS = [
    "alfred.org.au",
    "monash.edu",
    "svhm.org.au",
    "rch.org.au",
    "mh.org.au",
]

# A generic mailbox likely to exist on most orgs (for the "control" probe).
# Fall back through this list if earlier ones are rejected.
CONTROL_LOCAL_PARTS = ["info", "contact", "enquiries", "reception"]

SMTP_TIMEOUT = 15  # seconds


def fake_local_part(n: int = 10) -> str:
    """Random localpart that is vanishingly unlikely to exist."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=n))
    return f"zzz9probe{suffix}"


def mx_for(domain: str) -> str | None:
    try:
        answers = dns.resolver.resolve(domain, "MX")
        best = sorted(answers, key=lambda r: r.preference)[0]
        host = str(best.exchange).rstrip(".")
        return host
    except Exception as e:
        return None


def preflight() -> bool:
    """Open raw TCP to a known public MX on port 25 to see if ISP blocks it."""
    target_domains = ["gmail.com", "outlook.com"]
    print(f"[preflight] Testing outbound port 25 ...")
    any_ok = False
    for dom in target_domains:
        mx = mx_for(dom)
        if not mx:
            print(f"  {dom:20s}  MX lookup failed")
            continue
        t0 = time.time()
        try:
            with socket.create_connection((mx, 25), timeout=SMTP_TIMEOUT) as s:
                banner = s.recv(1024).decode(errors="replace").strip()
                dt = time.time() - t0
                print(f"  {dom:20s}  OK  ({dt:0.2f}s)  mx={mx}")
                print(f"    banner: {banner[:120]}")
                any_ok = True
        except Exception as e:
            dt = time.time() - t0
            print(f"  {dom:20s}  FAIL ({dt:0.2f}s) mx={mx}  err={type(e).__name__}: {e}")
    if any_ok:
        print("[preflight] Port 25 outbound works. Proceed to --probe.")
    else:
        print("[preflight] Port 25 BLOCKED on this network.")
        print("  Fix options: use a mobile hotspot, a VPN that permits 25,")
        print("  or run this from a cloud VM (Oracle free tier / GCP / AWS).")
    return any_ok


def probe_domain(domain: str, helo_domain: str) -> dict:
    result = {
        "domain": domain,
        "mx": None,
        "tcp_ok": False,
        "helo_code": None,
        "mail_code": None,
        "canary_code": None,
        "canary_msg": "",
        "control_local": None,
        "control_code": None,
        "control_msg": "",
        "latency_s": 0.0,
        "verdict": "",
        "err": None,
    }
    t0 = time.time()
    try:
        mx = mx_for(domain)
        result["mx"] = mx
        if not mx:
            result["err"] = "no MX record"
            result["verdict"] = "no_mx"
            return result

        smtp = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        smtp.connect(mx, 25)
        result["tcp_ok"] = True

        helo_code, _ = smtp.helo(helo_domain)
        result["helo_code"] = helo_code

        mail_code, _ = smtp.mail(f"probe@{helo_domain}")
        result["mail_code"] = mail_code

        canary_addr = f"{fake_local_part()}@{domain}"
        ccode, cmsg = smtp.rcpt(canary_addr)
        result["canary_code"] = ccode
        result["canary_msg"] = cmsg.decode(errors="replace")[:200] if isinstance(cmsg, bytes) else str(cmsg)[:200]

        # Reset between RCPTs to start a fresh transaction
        try:
            smtp.rset()
            smtp.mail(f"probe@{helo_domain}")
        except Exception:
            pass

        for local in CONTROL_LOCAL_PARTS:
            ctrl_addr = f"{local}@{domain}"
            try:
                tcode, tmsg = smtp.rcpt(ctrl_addr)
            except Exception as e:
                tcode, tmsg = 0, str(e)
            if tcode in (250, 251, 550, 551, 553):
                result["control_local"] = local
                result["control_code"] = tcode
                result["control_msg"] = tmsg.decode(errors="replace")[:200] if isinstance(tmsg, bytes) else str(tmsg)[:200]
                break
            try:
                smtp.rset()
                smtp.mail(f"probe@{helo_domain}")
            except Exception:
                pass

        try:
            smtp.quit()
        except Exception:
            pass

    except Exception as e:
        result["err"] = f"{type(e).__name__}: {e}"
    result["latency_s"] = round(time.time() - t0, 2)

    # Verdict
    can = result["canary_code"]
    ctrl = result["control_code"]
    if result["err"] and not result["tcp_ok"]:
        result["verdict"] = "connect_failed"
    elif can in (550, 551, 553) and ctrl in (250, 251):
        result["verdict"] = "CLEAN_PROBE_WORKS"
    elif can in (250, 251) and ctrl in (250, 251):
        result["verdict"] = "catch_all"
    elif can in (550, 551, 553) and ctrl in (550, 551, 553):
        result["verdict"] = "all_rejected_or_no_control"
    elif result["helo_code"] and result["helo_code"] >= 400:
        result["verdict"] = "helo_rejected"
    else:
        result["verdict"] = "inconclusive"
    return result


def probe(helo_domain: str):
    print(f"[probe] HELO identity: {helo_domain}")
    print(f"[probe] Test domains : {', '.join(TEST_DOMAINS)}")
    print()
    rows = []
    for d in TEST_DOMAINS:
        print(f"--- {d} ---")
        r = probe_domain(d, helo_domain)
        rows.append(r)
        print(f"  mx           : {r['mx']}")
        print(f"  tcp_ok       : {r['tcp_ok']}")
        print(f"  helo_code    : {r['helo_code']}")
        print(f"  mail_code    : {r['mail_code']}")
        print(f"  canary_code  : {r['canary_code']}  msg={r['canary_msg']!r}")
        print(f"  control_local: {r['control_local']}")
        print(f"  control_code : {r['control_code']}  msg={r['control_msg']!r}")
        print(f"  latency_s    : {r['latency_s']}")
        print(f"  err          : {r['err']}")
        print(f"  VERDICT      : {r['verdict']}")
        print()
        # Light jitter between domains so we don't hammer
        time.sleep(random.uniform(3, 6))

    # Summary table
    print("=" * 78)
    print(f"SUMMARY   (HELO = {helo_domain}  run_at = {datetime.now().isoformat(timespec='seconds')})")
    print("=" * 78)
    print(f"{'domain':<22}{'tcp':<5}{'helo':<6}{'canary':<8}{'control':<9}{'verdict'}")
    for r in rows:
        print(
            f"{r['domain']:<22}"
            f"{'Y' if r['tcp_ok'] else 'N':<5}"
            f"{str(r['helo_code'] or '-'):<6}"
            f"{str(r['canary_code'] or '-'):<8}"
            f"{str(r['control_code'] or '-'):<9}"
            f"{r['verdict']}"
        )
    clean = sum(1 for r in rows if r["verdict"] == "CLEAN_PROBE_WORKS")
    catch = sum(1 for r in rows if r["verdict"] == "catch_all")
    fail = len(rows) - clean - catch
    print()
    print(f"Clean-probe domains : {clean}/{len(rows)}")
    print(f"Catch-all domains   : {catch}/{len(rows)}")
    print(f"Failed/inconclusive : {fail}/{len(rows)}")
    print()
    if clean >= 3:
        print("✅ FreeDNS HELO looks viable. Keep the free setup.")
    elif clean + catch >= 3:
        print("⚠️  Partial viability. Catch-all still usable; failures may be HELO-related.")
        print("    Consider buying a real domain if you need tighter coverage.")
    else:
        print("❌ Most probes failing. FreeDNS HELO is likely being rejected.")
        print("    Recommend buying a real domain ($10/yr) before continuing.")


def probe_addresses(helo_domain: str, addresses: list[str]):
    """Probe a specific list of email addresses. Groups by domain so each
    domain's MX is only connected to once."""
    from collections import defaultdict
    by_domain: dict[str, list[str]] = defaultdict(list)
    for a in addresses:
        if "@" not in a:
            continue
        by_domain[a.split("@", 1)[1].lower()].append(a)

    print(f"[probe-addresses] HELO identity: {helo_domain}")
    print(f"[probe-addresses] {sum(len(v) for v in by_domain.values())} addresses across {len(by_domain)} domain(s)")
    print()

    all_rows = []
    for domain, addrs in by_domain.items():
        print(f"--- {domain} ---")
        mx = mx_for(domain)
        print(f"  mx: {mx}")
        if not mx:
            for a in addrs:
                all_rows.append({"address": a, "code": None, "msg": "no MX", "verdict": "no_mx"})
            continue
        try:
            smtp = smtplib.SMTP(timeout=SMTP_TIMEOUT)
            smtp.connect(mx, 25)
            hc, _ = smtp.helo(helo_domain)
            print(f"  helo: {hc}")
            mc, _ = smtp.mail(f"probe@{helo_domain}")
            print(f"  mail: {mc}")
            for addr in addrs:
                try:
                    code, msg = smtp.rcpt(addr)
                    msg_s = msg.decode(errors="replace")[:200] if isinstance(msg, bytes) else str(msg)[:200]
                except Exception as e:
                    code, msg_s = 0, f"{type(e).__name__}: {e}"
                verdict = (
                    "exists" if code in (250, 251)
                    else "rejected" if code in (550, 551, 553)
                    else "unknown"
                )
                all_rows.append({"address": addr, "code": code, "msg": msg_s, "verdict": verdict})
                print(f"  {addr:<45}  code={code}  verdict={verdict}")
                print(f"      msg: {msg_s!r}")
                try:
                    smtp.rset()
                    smtp.mail(f"probe@{helo_domain}")
                except Exception:
                    pass
                time.sleep(random.uniform(1, 3))
            try:
                smtp.quit()
            except Exception:
                pass
        except Exception as e:
            print(f"  CONNECT FAILED: {type(e).__name__}: {e}")
            for a in addrs:
                all_rows.append({"address": a, "code": None, "msg": str(e), "verdict": "connect_failed"})
        print()

    # Summary
    print("=" * 78)
    print(f"SUMMARY  HELO={helo_domain}  run_at={datetime.now().isoformat(timespec='seconds')}")
    print("=" * 78)
    print(f"{'address':<45}{'code':<8}{'verdict'}")
    for r in all_rows:
        print(f"{r['address']:<45}{str(r['code'] or '-'):<8}{r['verdict']}")

    exists_count = sum(1 for r in all_rows if r["verdict"] == "exists")
    rejected_count = sum(1 for r in all_rows if r["verdict"] == "rejected")
    print()
    print(f"exists (250)   : {exists_count}")
    print(f"rejected (550) : {rejected_count}")
    print(f"other          : {len(all_rows) - exists_count - rejected_count}")
    return all_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preflight", action="store_true", help="Test if port 25 outbound works at all")
    ap.add_argument("--probe", metavar="HELO_DOMAIN", help="Run full RCPT probe using this HELO domain")
    ap.add_argument("--probe-addresses", metavar="HELO_DOMAIN", help="Probe specific addresses via --addresses")
    ap.add_argument("--addresses", nargs="+", default=[], help="Email addresses to probe (with --probe-addresses)")
    args = ap.parse_args()

    if not args.preflight and not args.probe and not args.probe_addresses:
        ap.print_help()
        sys.exit(1)

    if args.preflight:
        ok = preflight()
        if not args.probe and not args.probe_addresses:
            sys.exit(0 if ok else 2)
        print()

    if args.probe:
        probe(args.probe)

    if args.probe_addresses:
        if not args.addresses:
            print("--probe-addresses requires --addresses a@b.com c@d.com ...", file=sys.stderr)
            sys.exit(1)
        probe_addresses(args.probe_addresses, args.addresses)


if __name__ == "__main__":
    main()

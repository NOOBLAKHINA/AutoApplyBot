"""Auto-apply bot.

Usage:
  python auto_apply.py login linkedin      # opens browser; log in manually, then close the window
  python auto_apply.py login naukri        # also: yc, wellfound
  python auto_apply.py run                 # all portals
  python auto_apply.py run yc wellfound    # just these
  python auto_apply.py run linkedin --submit   # override dry_run from config
"""
import argparse
import csv
import datetime as dt
import random
import re
import shutil
import socket
import subprocess
import time
import os
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
# Dedicated profile for the bot's Chrome. Chrome refuses remote control of your
# default profile, so logins made here are kept separately from your everyday Chrome.
PROFILE_DIR = ROOT / "chrome_profile"
CDP_PORT = 9222
def find_chrome():
    candidates = [
        shutil.which("chrome"), shutil.which("chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES", r"C:\\Program Files")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ]
    return next((c for c in candidates if c and Path(c).exists()), "chrome")

CHROME = find_chrome()
LOG_FILE = ROOT / "applied.csv"
LOG_FIELDS = ["time", "portal", "job_id", "title", "company", "url", "status"]

LOGIN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "naukri": "https://www.naukri.com/nlogin/login",
    "yc": "https://account.ycombinator.com/?continue=https%3A%2F%2Fwww.workatastartup.com%2F",
    "wellfound": "https://wellfound.com/login",
}


def load_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text())


_LEVELS = r"\b(senior|sr|junior|jr|lead|staff|principal|associate|i{1,3}|iv|[1-4]|l[1-6]|[a-e][1-5])\b"
_CO_SUFFIX = r"\b(private|pvt|limited|ltd|llp|llc|inc|corp|corporation|co|technologies|technology|" \
             r"solutions|software|services|india|labs?)\b"


def role_key(title, company):
    """Normalise so 'Senior GenAI Engineer (Freelancer)' @ 'Soul AI Pvt Ltd' == 'GenAI Engineer' @ 'Soul AI'."""
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", title.lower())
    t = re.sub(_LEVELS, " ", re.sub(r"[^a-z0-9+#]+", " ", t))  # text after dashes kept: '– Node.js' is a different role
    raw = re.sub(r"[^a-z0-9]+", " ", company.lower())
    c = re.sub(_CO_SUFFIX, " ", raw).split() or raw.split()  # keep full name if it's all suffix words
    return " ".join(c), " ".join(t.split())


def newest_first(job_ids):
    """Job IDs grow over time on every portal; Naukri's 12-digit IDs start with the post date DDMMYY."""
    def key(j):
        j = str(j)
        if len(j) == 12 and j.isdigit():          # Naukri: DDMMYY + sequence
            return int(j[4:6] + j[2:4] + j[0:2] + j[6:])
        return int(j) if j.isdigit() else 0
    return sorted(job_ids, key=key, reverse=True)


def pause(lo=1.0, hi=2.5):
    """Human-ish delay between actions."""
    time.sleep(random.uniform(lo, hi))


class Bot:
    """Shared state + helpers handed to each portal module."""

    def __init__(self, cfg, page, dry_run, portal):
        self.cfg = cfg
        self.page = page
        self.dry_run = dry_run
        self.portal = portal
        self.profile = cfg["profile"]
        self.count = 0
        self.unconfirmed = 0  # consecutive submissions the portal didn't confirm
        self.portal_limit_hit = False  # set when the portal itself says the limit is reached
        self.current_resume = cfg["profile"]["resume_path"]  # resume for the job being applied to
        self.resume_uploaded = False
        rows = self._read_log()
        self.seen = {(r["portal"], r["job_id"]) for r in rows
                     if r["status"] in ("applied", "skipped_external")}
        # Company+role pairs already applied to on ANY portal – never apply to the same role twice.
        self.applied_roles = {role_key(r["title"], r["company"]) for r in rows
                              if r["status"] in ("applied", "not_confirmed", "already_applied")}
        today = dt.date.today().isoformat()
        self.today_count = sum(1 for r in rows if r["portal"] == portal
                               and r["status"] in ("applied", "not_confirmed")
                               and r["time"].startswith(today))

    # ---- log / dedupe ----
    @staticmethod
    def _read_log():
        if not LOG_FILE.exists():
            return []
        with LOG_FILE.open() as f:
            return list(csv.DictReader(f))

    def already_done(self, portal, job_id):
        return (portal, job_id) in self.seen

    def log(self, portal, job_id, title, company, url, status):
        new = not LOG_FILE.exists()
        with LOG_FILE.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow(dict(time=dt.datetime.now().isoformat(timespec="seconds"), portal=portal,
                            job_id=job_id, title=title, company=company, url=url, status=status))
        self.seen.add((portal, job_id))
        if status in ("applied", "not_confirmed", "already_applied", "dry_run"):
            self.applied_roles.add(role_key(title, company))
        if status == "applied":
            self.count += 1
        if status in ("applied", "not_confirmed"):  # an unconfirmed submit may still count on the portal
            self.today_count += 1
        print(f"  [{status}] {title} @ {company}  (today: {self.today_count}/{self.daily_limit})")

    @property
    def daily_limit(self):
        return self.cfg.get("daily_limit_per_portal", 50)

    def limit_reached(self):
        if self.portal_limit_hit:
            return True
        if self.today_count >= self.daily_limit:
            print(f"  daily limit of {self.daily_limit} reached on {self.portal} – not applying further today")
            return True
        return self.count >= self.cfg["max_applications_per_run"]

    def check_portal_limit(self):
        """Stop if the portal shows its own 'daily limit reached' message."""
        msg = re.compile(r"(daily|application|apply|easy apply)[^.]{0,40}limit|limit[^.]{0,30}(reached|exhausted)"
                         r"|reached[^.]{0,30}(limit|quota)|daily quota", re.I)
        if self.page.get_by_text(msg).count():
            print(f"  {self.portal} says the application limit is reached – stopping {self.portal}")
            self.portal_limit_hit = True
        return self.portal_limit_hit

    # ---- human in the loop ----
    def ask_human(self, reason, title, company):
        """Pause the run until the user finishes (or skips) this application in Chrome.
        Returns 'done' or 'skip'; 'q' ends the whole run."""
        msg = f"{self.portal}: {title} @ {company} needs you – {reason}"
        if os.name != "nt":
            try:
                subprocess.run(["notify-send", "-u", "critical", "Auto-apply bot", msg], timeout=5)
            except Exception:
                pass
        print("\a\n" + "!" * 70)
        print(f"  MANUAL STEP NEEDED: {msg}")
        print("  Finish the application in the Chrome window, then come back here.")
        while True:
            try:
                ans = input("  [Enter] = I submitted it   s = skip this job   q = quit run > ").strip().lower()
            except EOFError:  # no keyboard attached (background run) -> skip, finish it by hand later
                print("  (no terminal to answer – skipping; see 'skipped_manual' in applied.csv)")
                ans = "s"
            if ans in ("", "s", "q"):
                break
        print("!" * 70)
        if ans == "q":
            raise KeyboardInterrupt("stopped by user")
        return "done" if ans == "" else "skip"

    # ---- explicit final-submit approval (Option B) ----
    def approve_submission(self, title, company, details=""):
        print("\\n" + "=" * 72)
        print(f"APPLICATION READY FOR REVIEW: {self.portal}")
        print(f"Role: {title} @ {company}")
        if details:
            print(details[:5000])
        print("\\nReview the visible browser form, uploaded resume and every answer.")
        print("Type APPLY to authorize the final submission click.")
        print("Press Enter to leave it unsubmitted, S to skip, or Q to stop.")
        while True:
            answer = input("Your decision [APPLY/Enter/S/Q]: ").strip().upper()
            if answer in ("APPLY", "", "S", "Q"):
                break
        if answer == "Q":
            raise KeyboardInterrupt("Stopped before final submission by user")
        return answer == "APPLY"

    # ---- resume ----
    def resume_for(self, job_key, title, company, description):
        """Tailored resume for this job if enabled, else the base resume."""
        base = self.profile["resume_path"]
        if not self.cfg.get("tailor_resume", True):
            return base
        from resume_tailor import tailor
        return tailor(base, f"{self.portal}_{job_key}", title, company, description)

    def cover_note(self, title, company, description):
        """Short note to founders/hiring manager: Claude-written if available, else the config template."""
        if self.cfg.get("tailor_resume", True):
            from resume_tailor import write_note
            note = write_note(self.profile["resume_path"], title, company, description)
            if note:
                return note
        return self.cfg["cover_note"].format(title=title, company=company, **self.profile).strip()

    # ---- filtering ----
    def duplicate_role(self, title, company):
        """True (and prints why) if this company+role was already applied to on any portal."""
        if company and role_key(title, company) in self.applied_roles:
            print(f"  [skip] {title} @ {company}: already applied to this role at this company")
            return True
        return False

    def title_ok(self, title):
        t = title.lower()
        inc = any(re.search(rf"\b{re.escape(k)}\b", t) for k in self.cfg["title_must_include"])
        exc = any(re.search(rf"\b{re.escape(k)}\b", t) for k in self.cfg["title_exclude"])
        return inc and not exc

    # ---- answering questions ----
    def answer_for(self, question):
        q = question.lower()
        for rule in self.cfg["answers"]:
            if any(k in q for k in rule["match"]):
                return str(rule["value"]).format(**self.profile)
        return None


def _port_open():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", CDP_PORT)) == 0


def start_chrome(*urls, debug=True):
    """Start system Google Chrome with the bot's persistent profile."""
    if CHROME == "chrome" and shutil.which("chrome") is None:
        raise RuntimeError("Google Chrome was not found. Install Chrome or set CHROME_EXE in your environment.")
    chrome_exe = os.environ.get("CHROME_EXE", CHROME)
    args = [chrome_exe, f"--user-data-dir={PROFILE_DIR}", "--no-first-run", "--no-default-browser-check"]
    if debug:
        args.append(f"--remote-debugging-port={CDP_PORT}")
    return subprocess.Popen([*args, *urls], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_login(portal):
    # Plain Chrome, nothing attached -> sites see a normal browser, so login works.
    if _port_open():
        print("Bot Chrome is already running; close that window first, then retry.")
        return
    print(f"Log in to {portal} in the Chrome window, then close the window.")
    start_chrome(LOGIN_URLS[portal], debug=False).wait()


def connect_chrome(p):
    if not _port_open():
        start_chrome()
        for _ in range(30):
            if _port_open():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"Chrome didn't open debug port {CDP_PORT}")
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    ctx = browser.contexts[0]
    page = ctx.new_page()
    page.set_viewport_size({"width": 1366, "height": 900})
    return browser, page


def cmd_run(portals, submit):
    import importlib
    modules = {name: importlib.import_module(f"portals.{name}") for name in portals}
    cfg = load_config()
    dry_run = cfg.get("dry_run", True) and not submit
    if not Path(cfg["profile"]["resume_path"]).exists():
        print(f"WARNING: resume not found at {cfg['profile']['resume_path']}")
    print(f"DRY RUN: {dry_run}")

    with sync_playwright() as p:
        browser, page = connect_chrome(p)
        for name in portals:
            print(f"\n=== {name} ===")
            bot = Bot(cfg, page, dry_run, name)
            print(f"  already applied today: {bot.today_count}/{bot.daily_limit}")
            if bot.limit_reached():
                continue
            try:
                modules[name].run(bot)
            except KeyboardInterrupt:
                print("  run stopped by user")
                break
            except Exception as e:  # one portal crashing shouldn't kill the others
                print(f"  {name} stopped: {e}")
            print(f"  {name}: {bot.count} applications this run")
        page.close()
        browser.close()  # only disconnects; your Chrome window stays open


def main():
    import sys
    if len(sys.argv) == 1:  # plain `python auto_apply.py` / IDE Run button -> run all portals
        sys.argv.append("run")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login")
    lg.add_argument("portal", choices=list(LOGIN_URLS))
    rn = sub.add_parser("run")
    # No `choices=` here: argparse rejects a list default against choices, which broke plain `run`.
    rn.add_argument("portals", nargs="*", default=list(LOGIN_URLS),
                    help=f"any of: {', '.join(LOGIN_URLS)} (default: all)")
    rn.add_argument("--submit", action="store_true", help="actually submit (overrides dry_run)")
    a = ap.parse_args()
    if a.cmd == "run" and (bad := [x for x in a.portals if x not in LOGIN_URLS]):
        ap.error(f"unknown portal(s): {', '.join(bad)} – choose from {', '.join(LOGIN_URLS)}")
    if a.cmd == "login":
        cmd_login(a.portal)
    else:
        cmd_run(a.portals, a.submit)


if __name__ == "__main__":
    main()

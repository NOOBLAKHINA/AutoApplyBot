"""Wellfound (ex-AngelList Talent). Apply = note to the hiring team; your Wellfound
profile (with its resume) is what they see. Wellfound uses strong bot protection –
if a CAPTCHA appears the run pauses and asks you to solve it."""
import re

from portals.common import apply_job, collect_links

PORTAL = "wellfound"
LINK_RE = re.compile(r"/jobs/(?P<id>\d+)-[a-z0-9-]+")
APPLY_RE = re.compile(r"^apply\b", re.I)
SUBMIT_RE = re.compile(r"^(send application|submit application|send|submit)\b", re.I)
APPLIED_LIST_URL = "https://wellfound.com/jobs/applications"
APPLIED_RE =re.compile(r"^\W*applied\b|application (sent|submitted)", re.I)  # button reads "✓  Applied"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def search_urls(bot):
    for title in bot.cfg["titles"]:
        yield title, f"https://wellfound.com/role/r/{slug(title)}"  # remote
        for loc in bot.cfg["locations"]:
            yield f"{title} / {loc}", f"https://wellfound.com/role/l/{slug(title)}/{slug(loc)}"


def run(bot):
    for label, url in search_urls(bot):
        if bot.limit_reached():
            return
        print(f"- {label}")
        jobs = collect_links(bot, url, LINK_RE)
        print(f"  {len(jobs)} jobs found")
        for job_id, job_url in jobs.items():
            if bot.limit_reached():
                return
            if not bot.already_done(PORTAL, job_id):
                apply_job(bot, job_id, job_url, APPLY_RE, SUBMIT_RE, APPLIED_RE, APPLIED_LIST_URL)

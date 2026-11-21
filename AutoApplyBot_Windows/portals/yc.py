"""YC's Work at a Startup (workatastartup.com). Apply = short message to the founders;
your WaaS profile (with its resume) is what they see."""
import re
from urllib.parse import quote_plus

from portals.common import apply_job, collect_links

PORTAL = "yc"
LINK_RE = re.compile(r"/jobs/(?P<id>\d+)")
APPLY_RE = re.compile(r"^apply\b", re.I)
SUBMIT_RE = re.compile(r"^(send|submit|apply)\b", re.I)
APPLIED_RE = re.compile(r"^\W*applied\b|application sent|you('ve| have) applied", re.I)


def run(bot):
    for title in bot.cfg["titles"]:
        if bot.limit_reached():
            return
        url = f"https://www.workatastartup.com/companies?query={quote_plus(title)}&sortBy=created_desc&layout=list-compact"
        print(f"- {title}")
        jobs = collect_links(bot, url, LINK_RE)
        print(f"  {len(jobs)} jobs found")
        for job_id, job_url in jobs.items():
            if bot.limit_reached():
                return
            if not bot.already_done(PORTAL, job_id):
                apply_job(bot, job_id, job_url, APPLY_RE, SUBMIT_RE, APPLIED_RE)

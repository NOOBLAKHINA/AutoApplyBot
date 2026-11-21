"""Naukri.com. Applies only to jobs with the on-site 'Apply' button (skips 'Apply on company site')."""
import re

from auto_apply import newest_first, pause

PORTAL = "naukri"
PAGES_PER_SEARCH = 2  # 20 jobs per page


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def run(bot):
    page, cfg = bot.page, bot.cfg
    exp = bot.profile["years_experience"]
    age = cfg.get("posted_within_days", 7)
    for title in cfg["titles"]:
        for loc in cfg["locations"]:
            jobs = []
            for pg in range(1, PAGES_PER_SEARCH + 1):
                path = f"{slug(title)}-jobs"
                query = f"k={title}&experience={exp}&jobAge={age}"
                if loc.lower() != "india":
                    path += f"-in-{slug(loc)}"
                    query += f"&l={loc}"
                if pg > 1:
                    path += f"-{pg}"
                print(f"- {title} / {loc} / page {pg}")
                page.goto(f"https://www.naukri.com/{path}?{query}")
                pause(3, 5)
                cards = page.locator(".srp-jobtuple-wrapper")
                if cards.count() == 0:
                    break
                for c in cards.all():
                    a = c.locator("a.title").first
                    jobs.append(dict(id=c.get_attribute("data-job-id") or a.get_attribute("href"),
                                     title=_text(a), company=_text(c.locator("a.comp-name").first),
                                     url=a.get_attribute("href")))
            by_id = {j["id"]: j for j in jobs}
            for job in (by_id[i] for i in newest_first(by_id)):
                if bot.limit_reached():
                    return
                if bot.already_done(PORTAL, job["id"]) or not bot.title_ok(job["title"]):
                    continue
                if bot.duplicate_role(job["title"], job["company"]):
                    continue
                status = apply(bot, job)
                bot.log(PORTAL, job["id"], job["title"], job["company"], job["url"], status)
                if status == "not_confirmed":
                    bot.unconfirmed += 1
                elif status == "applied":
                    bot.unconfirmed = 0
                if bot.unconfirmed >= 2:
                    raise RuntimeError("2 applications in a row weren't confirmed by Naukri – stopping")


def apply(bot, job):
    page, url = bot.page, job["url"]
    page.goto(url)
    pause(2, 4)
    if "login" in page.url:
        raise RuntimeError("not logged in – run: python auto_apply.py login naukri")
    # Never touch jobs that redirect to the company's own site.
    if page.locator("#company-site-button, [class*='company-site']").count() \
            or page.get_by_text(re.compile(r"company site", re.I)).count():
        return "skipped_external"
    if page.locator("#already-applied, span:has-text('Applied')").count():
        return "already_applied"
    btn = page.locator("#apply-button, button.apply-button").first
    if btn.count() == 0:
        return "error_no_button"
    if "company" in _text(btn).lower():
        return "skipped_external"

    # Naukri's Apply sends the resume on your profile, so put the tailored one there first.
    body = _text(page.locator("body"))
    i = body.lower().find("job description")
    resume = bot.resume_for(job["id"], job["title"], job["company"], body[i:i + 10000] if i >= 0 else body[:10000])
    if bot.dry_run:
        return "dry_run"
    if resume != bot.profile["resume_path"]:
        upload_profile_resume(bot, resume)
        page.goto(url)
        pause(2, 4)
        btn = page.locator("#apply-button, button.apply-button").first
        if btn.count() == 0:
            return "error_no_button"

    if not bot.approve_submission(job["title"], job["company"],
                                  "Naukri on-site application flow is about to start. Review the job and profile resume first."):
        return "skipped_manual"
    tabs_before = len(page.context.pages)
    btn.click()
    pause(2, 4)
    if bot.check_portal_limit():
        return "limit_reached"
    # Safety net: if the click still opened a company site, close it and move on.
    for extra in page.context.pages[tabs_before:]:
        extra.close()
        return "skipped_external"
    if "naukri.com" not in page.url:
        return "skipped_external"
    if page.locator(".chatbot_DrawerContentWrapper").count() and not answer_chatbot(bot):
        if bot.ask_human("recruiter questions couldn't be answered automatically",
                         job["title"], job["company"]) == "skip":
            return "skipped_manual"
    if confirm_applied(page, job):
        return "applied"
    # Something unexpected on the page – let the user look before moving on.
    if bot.ask_human("couldn't confirm the application went through – please check/finish it",
                     job["title"], job["company"]) == "skip":
        return "skipped_manual"
    return "applied" if confirm_applied(page, job) else "not_confirmed"


PROFILE_URL = "https://www.naukri.com/mnjuser/profile"


def upload_profile_resume(bot, resume):
    """Replace the resume on the Naukri profile (what recruiters receive on Apply)."""
    page = bot.page
    page.goto(PROFILE_URL)
    pause(3, 4)
    inp = page.locator("input#attachCV, input[type=file]").first
    if inp.count() == 0:
        print("    couldn't find Naukri resume upload – applying with current profile resume")
        return False
    inp.set_input_files(resume)
    for _ in range(10):
        pause(1, 1.5)
        if page.get_by_text(re.compile(r"successfully uploaded|uploaded successfully|resume has been (successfully )?(uploaded|updated)", re.I)).count():
            print("    Naukri profile resume updated")
            return True
    print("    Naukri didn't confirm the resume upload – continuing")
    return False


APPLIED_URL = "https://www.naukri.com/myapply/historypage"


def confirm_applied(page, job):
    """Don't move on until the job shows up in Naukri's Applied jobs (or the job page says Applied)."""
    if _applied_confirmed(page):
        print("    confirmed on job page")
        return True
    for attempt in range(4):
        pause(3, 5)
        page.goto(APPLIED_URL)
        pause(3, 4)
        html = page.content()
        if str(job["id"]) in html or (job["title"] in html and job["company"] in html):
            print("    confirmed in Applied jobs")
            return True
        page.goto(job["url"])
        pause(2, 4)
        if _applied_confirmed(page):
            print("    confirmed on job page")
            return True
        print(f"    not in Applied yet (try {attempt + 1}/4)")
    return False


def answer_chatbot(bot):
    """Naukri asks recruiter questions in a chat drawer, one at a time."""
    page = bot.page
    for _ in range(12):
        drawer = page.locator(".chatbot_DrawerContentWrapper")
        if drawer.count() == 0:
            return True
        q = _text(drawer.locator(".botMsg").last)
        ans = bot.answer_for(q) or str(bot.profile["years_experience"])

        radios = drawer.locator(".ssrc__radio-btn-container")
        chips = drawer.locator(".chatbot_Chip")
        box = drawer.locator("div[contenteditable='true']")
        if radios.count():
            texts = [t.strip() for t in radios.all_inner_texts()]
            pick = next((i for i, t in enumerate(texts) if ans.lower() in t.lower()), None)
            if pick is None:
                pick = next((i for i, t in enumerate(texts) if t.lower() == "yes"), 0)
            radios.nth(pick).click()
        elif chips.count():
            texts = [t.strip() for t in chips.all_inner_texts()]
            pick = next((i for i, t in enumerate(texts) if ans.lower() in t.lower()), 0)
            chips.nth(pick).click()
        elif box.count():
            box.first.fill(ans)
        else:
            return False
        pause(0.5, 1)
        send = drawer.locator(".sendMsg, .sendMsgbtn_container, button:has-text('Save')").first
        if send.count():
            send.click()
        pause(1.5, 2.5)
    return page.locator(".chatbot_DrawerContentWrapper").count() == 0


def _applied_confirmed(page):
    return (page.get_by_text(re.compile(r"successfully applied|applied to", re.I)).count() > 0
            or page.locator("#already-applied").count() > 0)


def _text(loc):
    try:
        return loc.inner_text(timeout=2000).strip()
    except Exception:
        return ""

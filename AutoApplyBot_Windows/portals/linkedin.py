"""LinkedIn Easy Apply. Only jobs with the in-site 'Easy Apply' flow are handled.

LinkedIn's 2026 jobs UI uses obfuscated CSS classes, so this module avoids classes:
it pulls job IDs out of the search page HTML, opens each /jobs/view/<id>/ page,
and finds buttons/fields by accessible name, role and label.
"""
import re
from pathlib import Path
from urllib.parse import quote_plus

from auto_apply import newest_first, pause

PORTAL = "linkedin"
PAGES_PER_SEARCH = 3  # 25 jobs per page
TPR = {1: "r86400", 7: "r604800", 30: "r2592000"}
JOB_ID_RE = re.compile(r"(?:currentJobId=|/jobs/view/|jobPosting:|jobPostingUrn%3A|jobPosting%3A)(\d{8,})")

# Question text for a form field: its <label>, aria-label, or the nearest container with text.
QUESTION_JS = """el => {
  let q = el.getAttribute('aria-label') || '';
  if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) q += ' ' + l.innerText; }
  const fs = el.closest('fieldset'); if (fs) q += ' ' + fs.innerText;
  let box = el.parentElement;
  for (let i = 0; i < 4 && box && box.innerText.trim().length < 4; i++) box = box.parentElement;
  return q + ' ' + (box ? box.innerText : '');
}"""

BTN_SUBMIT = re.compile(r"submit application|^submit$", re.I)
BTN_NEXT = re.compile(r"review your application|continue to next step|^review$|^next$", re.I)


def run(bot):
    page, cfg = bot.page, bot.cfg
    tpr = TPR.get(cfg.get("posted_within_days", 7), "r604800")
    for title in cfg["titles"]:
        for loc in cfg["locations"]:
            for pg in range(PAGES_PER_SEARCH):
                if bot.limit_reached():
                    return
                url = (f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(title)}"
                       f"&location={quote_plus(loc)}&f_AL=true&f_TPR={tpr}&sortBy=DD&start={pg * 25}")
                print(f"- {title} / {loc} / page {pg + 1}")
                ids = collect_ids(page, url)
                print(f"  {len(ids)} jobs found")
                if not ids:
                    break
                for job_id in ids:
                    if bot.limit_reached():
                        return
                    if not bot.already_done(PORTAL, job_id):
                        handle_job(bot, job_id)


def collect_ids(page, url):
    page.goto(url)
    pause(4, 6)
    if "login" in page.url or "authwall" in page.url:
        raise RuntimeError("not logged in – run: python auto_apply.py login linkedin")
    # The result list lazy-loads; scroll it so all ~25 cards render.
    page.mouse.move(350, 500)
    for _ in range(8):
        page.mouse.wheel(0, 1500)
        pause(0.4, 0.8)
    return newest_first(set(JOB_ID_RE.findall(page.content())))


def handle_job(bot, job_id):
    page = bot.page
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    page.goto(url)
    pause(3, 4)
    # Tab title is "Job Title | Company | LinkedIn" – reliable even when the page layout changes.
    parts = [s.strip() for s in page.title().split("|")]
    title = _text(page.locator("h1").first) or (parts[0] if len(parts) >= 3 else "")
    company = (_text(page.locator("a[href*='/company/']").filter(has_text=re.compile(r"\S")).first)
               or (parts[1] if len(parts) >= 3 else ""))
    if not title:
        print(f"  [skip] {job_id}: couldn't read job title (tab title: {page.title()!r})")
        return
    if not bot.title_ok(title):
        print(f"  [skip] {title}: title filtered out")
        return
    if bot.duplicate_role(title, company):
        return
    btn = page.locator("button[aria-label*='Easy Apply'], a[aria-label*='Easy Apply']").first
    if btn.count() == 0:
        btn = page.get_by_role("button", name=re.compile(r"easy apply", re.I)).first
    if btn.count() == 0:
        btn = page.get_by_role("link", name=re.compile(r"easy apply", re.I)).first
    if btn.count() == 0:
        print(f"  [skip] {title} @ {company}: no Easy Apply button (already applied / external)")
        return
    print(f"  applying: {title} @ {company}")
    body = _text(page.locator("body"))
    i = body.find("About the job")
    description = body[i:i + 10000] if i >= 0 else body[:10000]
    bot.current_resume = bot.resume_for(job_id, title, company, description)
    bot.current_title, bot.current_company = title, company
    bot.resume_uploaded = False
    btn.click()
    pause(2, 3)
    if bot.check_portal_limit():
        discard(page)
        return
    status = fill_modal(bot)
    if status == "applied" and bot.check_portal_limit():
        status = "not_confirmed"
    if status.startswith("manual:"):
        if bot.ask_human(status[7:], title, company) == "done":
            status = "applied"
        else:
            discard(page)
            status = "skipped_manual"
    if status == "applied":
        status = "applied" if confirm_applied(page, job_id) else "not_confirmed"
    bot.log(PORTAL, job_id, title, company, url, status)
    # Don't keep going if submissions aren't actually landing.
    if status == "not_confirmed":
        bot.unconfirmed += 1
    elif status == "applied":
        bot.unconfirmed = 0
    if bot.unconfirmed >= 2:
        raise RuntimeError("2 applications in a row didn't appear in LinkedIn's Applied jobs – stopping")


APPLIED_URL = "https://www.linkedin.com/my-items/saved-jobs/?cardType=APPLIED"


def confirm_applied(page, job_id):
    """Wait until the job shows up in My Jobs > Applied (or the job page says Applied)."""
    for attempt in range(4):
        pause(3, 5)
        page.goto(APPLIED_URL)
        pause(3, 4)
        if job_id in page.content():
            print(f"    confirmed in Applied jobs")
            return True
        page.goto(f"https://www.linkedin.com/jobs/view/{job_id}/")
        pause(2, 3)
        if page.get_by_text(re.compile(r"^Applied\b|application (was )?(sent|submitted)", re.I)).count():
            print(f"    confirmed on job page")
            return True
        print(f"    not in Applied yet (try {attempt + 1}/4)")
    return False


def fill_modal(bot):
    page = bot.page
    last_q = None
    for _ in range(15):  # max steps in the wizard
        dlg = page.get_by_role("dialog").first
        if dlg.count() == 0:
            return "error_no_modal"
        fill_fields(bot, dlg)

        submit = dlg.get_by_role("button", name=BTN_SUBMIT)
        if submit.count():
            if bot.dry_run:
                discard(page)
                return "dry_run"
            if not bot.approve_submission(bot.current_title if hasattr(bot, "current_title") else "LinkedIn job",
                                          getattr(bot, "current_company", ""),
                                          _text(dlg)):
                discard(page)
                return "not_sent"
            submit.first.click()
            pause(3, 4)
            page.keyboard.press("Escape")  # close the "application sent" popup
            pause()
            return "applied"

        nxt = dlg.get_by_role("button", name=BTN_NEXT)
        if nxt.count() == 0:
            return _manual(bot, "unrecognised form step (no Next/Submit button)")
        before = _text(dlg)
        nxt.first.click()
        pause(1.5, 2.5)
        # Stuck on the same step (validation error / unanswered required question)?
        if _text(dlg) == before or dlg.locator("[aria-invalid=true], [role=alert]:visible").count():
            if before == last_q:
                return _manual(bot, "a required question couldn't be answered automatically")
            last_q = before  # one more try after re-filling
    return _manual(bot, "form has too many steps")


def _manual(bot, reason):
    """Leave the form open for the user (real run) or drop it (dry run)."""
    if bot.dry_run:
        discard(bot.page)
        return "needs_manual"
    return "manual:" + reason


def fill_fields(bot, dlg):
    prof = bot.profile

    # Resume: upload this job's (tailored) resume once, into the resume input (not a cover-letter one).
    if not bot.resume_uploaded:
        for f in dlg.locator("input[type=file]").all():
            label = f.evaluate(QUESTION_JS).lower()
            if "cover" in label:
                continue
            if Path(bot.current_resume).exists():
                f.set_input_files(bot.current_resume)
                bot.resume_uploaded = True
                pause(2, 3)
            break

    # Text / number / textarea
    for el in dlg.locator("input[type=text]:visible, input:not([type]):visible, "
                          "input[type=number]:visible, input[type=tel]:visible, textarea:visible").all():
        if el.input_value().strip():
            continue
        q = el.evaluate(QUESTION_JS)
        ans = bot.answer_for(q)
        if ans is None and (el.get_attribute("type") == "number" or "numeric" in (el.get_attribute("id") or "")
                            or "how many" in q.lower()):
            ans = str(prof["years_experience"])
        if ans is None:
            continue
        el.fill(ans)
        if el.get_attribute("role") == "combobox":  # typeahead like city
            pause(1, 1.5)
            el.press("ArrowDown")
            el.press("Enter")

    # Dropdowns
    for sel in dlg.locator("select:visible").all():
        current = sel.evaluate("s => s.options[s.selectedIndex]?.text || ''")
        if current and "select" not in current.lower():
            continue
        opts = [o.strip() for o in sel.locator("option").all_inner_texts()]
        sel.select_option(label=_pick(opts, bot.answer_for(sel.evaluate(QUESTION_JS))))

    # Radio groups (inputs are often hidden; click their labels)
    for fs in dlg.locator("fieldset").all():
        if fs.locator("input[type=radio]").count() == 0 or fs.locator("input[type=radio]:checked").count():
            continue
        labels = fs.locator("label")
        texts = [t.strip() for t in labels.all_inner_texts()]
        if not texts:
            continue
        choice = _pick(texts, bot.answer_for(_text(fs)))
        labels.nth(texts.index(choice)).click()

    # Required checkboxes (e.g. "I agree to terms")
    for cb in dlg.locator("input[type=checkbox][required]:not(:checked), input[type=checkbox][aria-required=true]:not(:checked)").all():
        cb.check(force=True)


def _pick(options, answer):
    """Best option for an answer: exact/contains match, else 'Yes', else first real option."""
    real = [o for o in options if o and "select" not in o.lower()]
    if not real:
        return options[0]
    for want in filter(None, [answer, "Yes"]):
        for o in real:
            if o.lower() == want.lower() or want.lower() in o.lower():
                return o
    return real[0]


def discard(page):
    page.keyboard.press("Escape")
    pause(1, 1.5)
    btn = page.get_by_role("button", name=re.compile(r"^discard", re.I))
    if btn.count():
        btn.first.click()
        pause()


def _text(loc):
    try:
        return loc.inner_text(timeout=2000).strip()
    except Exception:
        return ""

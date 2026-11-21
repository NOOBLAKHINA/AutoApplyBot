"""Shared flow for 'Apply + short note' portals (YC Work at a Startup, Wellfound).

These sites send the resume already on your profile, and ask for a short note.
Everything is found by role/visible text (their CSS classes are generated), and
anything unexpected is handed to you via bot.ask_human instead of being guessed.
"""
import re

from playwright.sync_api import Error as PlaywrightError

from auto_apply import newest_first, pause

NOT_A_MATCH_RE = re.compile(r"may not be a match|qualifications might not match|"
                            r"does not support the locations?\b", re.I)
NOTE_QUESTION_RE = re.compile(r"what interests you|why (do you want|are you interested|this company|us)|"
                              r"cover letter|message to|note to|tell us about yourself", re.I)
QUESTION_TEXT_JS = "e => (e.closest('div')?.parentElement?.innerText || '').slice(0, 300)"
EXPERIENCE_RE =re.compile(r"Experience\s*\n?\s*(\d+)\+?\s*(?:-\s*\d+\s*)?years", re.I)
BLOCKED_RE =re.compile(r"verify you are human|are you a robot|captcha|press (&|and) hold|"
                        r"access (is )?(temporarily )?(denied|blocked)", re.I)


def collect_links(bot, url, link_re, scrolls=6):
    """Open a search page, scroll to load results, return {job_id: absolute_url} in page order."""
    page = bot.page
    page.goto(url)
    pause(4, 6)
    ensure_not_blocked(bot, "search page")
    page.mouse.move(600, 500)
    for _ in range(scrolls):
        page.mouse.wheel(0, 2000)
        pause(0.6, 1.2)
    base = re.match(r"https?://[^/]+", url).group(0)
    found = {}
    for m in link_re.finditer(page.content()):
        found.setdefault(m.group("id"), base + m.group(0))
    return {i: found[i] for i in newest_first(found)}


def ensure_not_blocked(bot, where):
    """CAPTCHA / bot wall / logged out -> let the user fix it in Chrome, then continue."""
    page = bot.page
    if BLOCKED_RE.search(_text(page.locator("body"))[:3000]) or re.search(r"/log_?in|/signin|account\.ycombinator", page.url):
        if bot.ask_human(f"{bot.portal} {where} shows a login/CAPTCHA wall – log in or solve it in Chrome",
                         "(search)", bot.portal) == "skip":
            raise RuntimeError("blocked by login/CAPTCHA")
        page.reload()
        pause(3, 4)


def title_company(page):
    """'AI Engineer at Acme | Site' style tab titles, with <h1> as the title fallback."""
    for _ in range(10):  # SPAs set the real tab title a moment after load
        head = re.split(r"\s[|•·]\s", page.title())[0].strip()  # not '–': titles like 'Backend Engineer – GTM'
        if " at " in head:
            break
        pause(0.5, 0.6)
    title, company = (head.rsplit(" at ", 1) + [""])[:2] if " at " in head else (head, "")
    if not company:  # YC shows 'Readily · S23' under the breadcrumb
        m = re.search(r"^(.+?) · [WSFX]\d{2}\s*$", _text(page.locator("body"))[:2000], re.M)
        company = m.group(1) if m else ""
    h1 = _text(page.locator("h1").first)
    title = re.sub(r"^[^\w(]+", "", title.strip() or h1)  # drop leading emoji like '🚀 '
    return title, company.strip()


def apply_job(bot, job_id, url, apply_re, submit_re, applied_re, applied_list_url=None):
    """Apply to one job; a page glitch on this job skips it instead of stopping the whole portal."""
    try:
        _apply_job(bot, job_id, url, apply_re, submit_re, applied_re, applied_list_url)
    except PlaywrightError as e:
        print(f"  [error] job {job_id}: {str(e).splitlines()[0][:150]} – skipping this job")
        bot.log(bot.portal, job_id, "", "", url, "error")
        try:
            _close_dialog(bot.page)
        except PlaywrightError:
            pass


def _apply_job(bot, job_id, url, apply_re, submit_re, applied_re, applied_list_url=None):
    """Open a job, apply with a note, confirm, and log. Returns nothing; logs the outcome."""
    page, portal = bot.page, bot.portal
    page.goto(url)
    pause(3, 5)
    ensure_not_blocked(bot, "job page")
    title, company = title_company(page)
    if not title:
        print(f"  [skip] {job_id}: couldn't read job title")
        return
    if not bot.title_ok(title):
        print(f"  [skip] {title}: title filtered out")
        return
    if bot.duplicate_role(title, company):
        return
    if _find_button(page, applied_re).count():
        bot.log(portal, job_id, title, company, url, "already_applied")
        return
    btn = _find_button(page, apply_re)
    if btn.count() == 0:
        print(f"  [skip] {title} @ {company}: no Apply button")
        return

    print(f"  applying: {title} @ {company}")
    note = bot.cover_note(title, company, _text(page.locator("main, body").first)[:10000])
    if bot.dry_run:
        print(f"    note: {note[:100]}...")
        bot.log(portal, job_id, title, company, url, "dry_run")
        return

    tabs_before = len(page.context.pages)
    btn.first.click()
    pause(2, 3)
    if bot.check_portal_limit():
        return
    for extra in page.context.pages[tabs_before:]:  # apply link went to an external ATS
        extra.close()
        bot.log(portal, job_id, title, company, url, "skipped_external")
        return
    # The portal itself says you don't fit (YC: "This may not be a match") -> don't message the founder.
    if bot.cfg.get("skip_portal_mismatch", True) and page.get_by_text(NOT_A_MATCH_RE).count():
        _close_dialog(page)
        bot.log(portal, job_id, title, company, url, "skipped_not_a_match")
        return
    # Job asks for far more experience than you have (e.g. "Experience 8+ years" vs your 2).
    m = EXPERIENCE_RE.search(_text(page.get_by_role("dialog").first) if page.get_by_role("dialog").count() else "")
    limit = bot.profile["years_experience"] + bot.cfg.get("experience_gap", 2)
    if m and int(m.group(1)) > limit:
        _close_dialog(page)
        print(f"    needs {m.group(1)}+ years (you: {bot.profile['years_experience']})")
        bot.log(portal, job_id, title, company, url, "skipped_experience")
        return

    status = _fill_and_submit(bot, note, submit_re, title, company)
    if status == "applied":
        ok = _confirm(bot, url, applied_re, title, applied_list_url)
        status = "applied" if ok else "not_confirmed"
        if status == "not_confirmed" and bot.ask_human(
                "couldn't confirm the application – please check/finish it", title, company) == "done":
            status = "applied" if _confirm(bot, url, applied_re, title, applied_list_url) else "not_confirmed"
    bot.log(portal, job_id, title, company, url, status)
    # Only a confirmed application resets the streak; skipped jobs in between don't.
    if status == "not_confirmed":
        bot.unconfirmed += 1
    elif status == "applied":
        bot.unconfirmed = 0
    if bot.unconfirmed >= 2:
        raise RuntimeError(f"2 applications in a row weren't confirmed on {portal} – stopping")


def _fill_and_submit(bot, note, submit_re, title, company):
    page = bot.page
    dlg = page.get_by_role("dialog")
    scope = dlg.first if dlg.count() else page
    fields = scope.locator("textarea:visible, input[required]:visible, select[required]:visible").all()
    enabled = [f for f in fields if f.is_enabled()]
    if fields and not enabled:  # portal locked the form (e.g. location mismatch) – nothing we can do
        _close_dialog(page)
        print("    application form is locked by the portal – skipping")
        return "skipped_not_a_match"
    # The note goes in the message box (or a question that's really asking for one, like Wellfound's
    # "What interests you about working for this company?"); other employer questions go to the human.
    note_boxes = [f for f in enabled if f.evaluate("e => e.tagName") == "TEXTAREA"
                  and ("customquestion" not in (f.get_attribute("name") or "").lower()
                       or NOTE_QUESTION_RE.search(f.evaluate(QUESTION_TEXT_JS)))]
    if note_boxes:
        note_boxes[0].fill(note, timeout=5000)
        pause()
    if any(not (f.input_value() or "").strip() for f in enabled):
        return _human(bot, "the application has extra required questions", title, company)
    # Yes/No screening questions (hidden radio inputs behind styled labels) – never guess these,
    # e.g. "Are you authorized to work in the US?" must be answered truthfully by you.
    unanswered_radio = scope.evaluate("""el => {
      const groups = {};
      el.querySelectorAll('input[type=radio]').forEach(r => { (groups[r.name] ||= []).push(r); });
      return Object.values(groups).filter(g => !g.some(r => r.checked)).length;
    }""") if scope != page else 0
    if unanswered_radio:
        return _human(bot, f"{unanswered_radio} yes/no screening question(s) need your answers", title, company)
    submit = scope.get_by_role("button", name=submit_re)
    if submit.count() == 0:
        return _human(bot, "couldn't find the Send/Submit button", title, company)
    if not submit.first.is_enabled():
        return _human(bot, "the Send button stays disabled", title, company)
    if not bot.approve_submission(title, company, _text(scope)):
        _close_dialog(page)
        return "skipped_manual"
    submit.first.click()
    pause(3, 4)
    if scope.locator("[aria-invalid=true], [role=alert]:visible").count():
        return _human(bot, "the form reported an error", title, company)
    return "applied"


def _close_dialog(page):
    close = page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^(close|cancel)$", re.I))
    if close.count():
        close.first.click()
    else:
        page.keyboard.press("Escape")
    pause()


def _human(bot, reason, title, company):
    return "applied" if bot.ask_human(reason, title, company) == "done" else "skipped_manual"


def _confirm(bot, url, applied_re, title="", applied_list_url=None):
    page = bot.page
    for attempt in range(3):
        if _find_button(page, applied_re).count() or _text_outside_nav(page, applied_re):
            print("    confirmed applied")
            return True
        # Wellfound holds new applications as "Scheduled" (with Undo) for a few minutes; during that
        # time the job page shows no Apply/Applied button, but the Applied list already has it.
        if applied_list_url and title:
            page.goto(applied_list_url)
            pause(3, 4)
            if title in _text(page.locator("main, body").first):
                print("    confirmed in Applied list")
                return True
        pause(2, 3)
        page.goto(url)
        pause(3, 4)
        print(f"    not shown as applied yet (try {attempt + 1}/3)")
    return False


# Clickable elements on the page body – NOT the site menu (Wellfound's menu has an "Applied" link).
_CLICKABLE = ":is(a, button, [role=button]):visible:not(nav *):not(header *)"


def _find_button(page, name_re):
    """Any clickable element by its text (YC's Apply is an <a> without href), ignoring nav/header."""
    return page.locator(_CLICKABLE).filter(has_text=name_re)


def _text_outside_nav(page, regex):
    """True if visible text matching regex appears anywhere except the site's nav/header."""
    return page.evaluate("""([src, flags]) => {
      const re = new RegExp(src, flags);
      for (const el of document.querySelectorAll('body *')) {
        if (el.closest('nav, header') || !el.offsetParent) continue;
        const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
        if (own && re.test(own)) return true;
      }
      return false;
    }""", [regex.pattern, "i"])


def _text(loc):
    try:
        return loc.inner_text(timeout=2000).strip()
    except Exception:
        return ""

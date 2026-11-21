# Auto-Apply Bot

Applies to jobs on **LinkedIn (Easy Apply), Naukri, YC Work at a Startup and Wellfound** in your own Google Chrome, using the titles and answers in `config.yaml`.

---

## 1. One-time setup

You need Linux, Python 3.12 and Google Chrome (`/usr/bin/google-chrome`).

```bash
cd ~/auto_apply_bot

# If "python3 -m venv" fails with "ensurepip is not available":
sudo apt install python3.12-venv

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**In VS Code** the project's `.vscode/settings.json` already selects `.venv` as the Python environment. If the editor still uses another Python (you'll see `No module named 'playwright'`), choose *Python: Select Interpreter → .venv*.

You don't need `playwright install`. The bot drives the Chrome already installed on your system, not a bundled browser.

---

## 2. Edit `config.yaml`

Check these before your first run:

| Setting | What it does |
|---|---|
| `titles`, `locations` | What to search for |
| `title_must_include` / `title_exclude` | Which job titles are allowed. Add words like `construction`, `gtm` or `ios` to `title_exclude` to stop off-target applications |
| `profile` | `resume_path` (absolute path to your PDF), phone, experience, CTC, notice period, city |
| `answers` | Answers to common form questions (notice period, CTC, relocation…) |
| `cover_note` | Note sent on YC and Wellfound. Keep `{title}` and `{company}` in it |
| `dry_run` | `true` = fill everything but **don't submit**. Use it for a first test |
| `daily_limit_per_portal` | Maximum applications per site per day (default 50) |
| `max_applications_per_run` | Maximum per site per run |
| `skip_portal_mismatch` | Skip jobs the site itself flags as a poor fit (location / "may not be a match") |
| `experience_gap` | Skip jobs needing more than your years of experience plus this many |
| `tailor_resume` | `true` = rewrite your resume for each job with Claude (see section 6) |

---

## 3. Log in to each site (once)

```bash
.venv/bin/python auto_apply.py login linkedin
.venv/bin/python auto_apply.py login naukri
.venv/bin/python auto_apply.py login yc
.venv/bin/python auto_apply.py login wellfound
```

Each command opens a normal Chrome window. **Log in, then close the window.** Your logins are saved in `chrome_profile/` and reused on every run. Repeat a site's login if the bot ever says you're logged out.

---

## 4. Run it

```bash
.venv/bin/python auto_apply.py                         # same as `run` – also what VS Code's ▶ Run does
.venv/bin/python auto_apply.py run                     # all four sites
.venv/bin/python auto_apply.py run yc                  # just one
.venv/bin/python auto_apply.py run yc wellfound        # several
.venv/bin/python auto_apply.py run linkedin --submit   # submit even if dry_run: true
```

**Run it in your own terminal** and leave it open. When a job needs you (a question the bot can't answer, a CAPTCHA, or an application it can't confirm), it beeps, shows a desktop notification and waits:

- **Enter**: you finished the application in Chrome. The bot checks it went through, then continues.
- **s**: skip this job.
- **q**: stop the run.

Press **Ctrl+C** to stop at any time.

---

## 5. Check the results

Every job the bot handles is recorded in **`applied.csv`**. The `status` column says what happened:

| Status | Meaning |
|---|---|
| `applied` | Sent **and** confirmed in the site's Applied list |
| `not_confirmed` | Sent, but the site didn't confirm it. Check the job yourself |
| `not_sent` | Not submitted. A later run can retry it |
| `skipped_manual` | Needs your answers. **Finish these by hand** |
| `skipped_not_a_match` / `skipped_experience` | The site flagged a mismatch (location, experience) |
| `skipped_external` | Applies on the company's own website, so the bot doesn't touch it |
| `already_applied` / `dry_run` / `error` | As named |

The bot never applies twice to the same role at the same company, on any site. When a company posts the same role several times, it applies to the newest one.

---

## 6. Optional: tailored resume per job (costs money)

1. Uncomment `anthropic>=1.0` in `requirements.txt`, then run `.venv/bin/pip install -r requirements.txt`.
2. Create an API key at **console.anthropic.com** (Settings → API Keys) and add credit.
3. `echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc && source ~/.bashrc`
4. Set `tailor_resume: true` in `config.yaml`.

Tailored PDFs are saved in `tailored_resumes/`. On Naukri, each one **replaces the resume on your Naukri profile**, so re-upload your original afterwards. Expect roughly $0.10–0.15 per job. Without a key, the bot simply uses your original resume.

---

## Things to know

- **The sites' rules:** LinkedIn's and Naukri's terms forbid automated applying. Keep the limits modest to protect your accounts.
- **Yes/No questions on LinkedIn:** an unmatched Yes/No question is answered **"Yes"** by default. Add rules under `answers` for anything that must be answered truthfully, such as work authorization or sponsorship.
- **Relocation on YC:** applying to a job outside your location leaves YC's "I'm open to relocating" box ticked, which updates your YC profile.
- **When a site changes its page layout,** the bot may report `0 jobs found` or `no Apply button`. The site's code in `portals/` then needs updating.

## Files

| File | Purpose |
|---|---|
| `auto_apply.py` | The commands, shared logic, daily limits, duplicate check |
| `portals/linkedin.py`, `portals/naukri.py` | Site-specific flows |
| `portals/yc.py`, `portals/wellfound.py`, `portals/common.py` | YC and Wellfound (apply with a note) |
| `resume_tailor.py` | Optional Claude resume tailoring and cover notes |
| `config.yaml` | All your settings |
| `applied.csv` | Log of every job handled |

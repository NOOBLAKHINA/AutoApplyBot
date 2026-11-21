# Auto-Apply Bot — Windows port of your original project

This port preserves the original architecture and portal modules: LinkedIn Easy Apply, Naukri, Wellfound, YC Work at a Startup, `config.yaml` question rules, resume tailoring, deduplication, daily limits and `applied.csv`.

## Windows requirements
- Windows 10/11, Python 3.10+ (3.12 recommended)
- Google Chrome installed
- VS Code optional

## Install
Open PowerShell in this folder:
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```
If activation is blocked:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Git Bash alternative:
```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

This project attaches Playwright to a normal Google Chrome instance using CDP. It does not use `playwright install`.

## Configure
Edit `config.yaml`:
- Set `profile.resume_path` to your actual Windows resume path, e.g. `C:/Users/Gaurav/Documents/Resume.pdf`.
- Verify phone, experience, CTC, notice period, city, profile URLs and every entry in `answers`.
- Review the `answers` rules carefully. Incorrect eligibility, sponsorship, relocation or legal answers can cause false applications. Change or remove any rule that does not reflect your real circumstances.
- Start with `dry_run: true`.

If Chrome is installed in a non-standard location, set `CHROME_EXE` to its full path in PowerShell:
```powershell
$env:CHROME_EXE = "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

## Log in once per site
```powershell
python auto_apply_bot.py login linkedin
python auto_apply_bot.py login naukri
python auto_apply_bot.py login yc
python auto_apply_bot.py login wellfound
```
Log in manually in the Chrome window, then close it. Cookies are stored in `chrome_profile`.

## Run
All platforms:
```powershell
python auto_apply_bot.py run
```
Selected platforms:
```powershell
python auto_apply_bot.py run linkedin naukri wellfound yc
```
Original command name also works:
```powershell
python auto_apply.py run linkedin naukri wellfound yc
```

## Option B — explicit approval before final submission
For LinkedIn and Wellfound/YC, the bot fills the form and pauses at the final submit step. Review the form in Chrome and type `APPLY` in the terminal to authorize the final click. Press Enter or `S` to leave it unsubmitted/skip; `Q` stops the run.

For Naukri, its application flow begins with the on-site Apply action and then presents its chat-style questions. The port pauses for approval before starting that flow; any unanswered or unexpected questions still pause for manual handling. This is intentionally conservative and is not a fully post-fill confirmation gate on Naukri yet.

Use `dry_run: true` for a non-submitting test. The explicit `--submit` flag can override dry_run, but it does not bypass the Option B approval prompt.

## Results and safety
Every processed job is recorded in `applied.csv`. Check status values before relying on the count. A browser/page layout change can break selectors; run small batches and inspect results.

CAPTCHA/OTP and access controls are not bypassed. The tool does not apply to external ATS links. Keep application volume modest and follow platform terms. Review answers for accuracy, especially work authorization, sponsorship, relocation and other legal/employment questions.

## Important
This is a Windows compatibility port of the provided source, not a guarantee that the live websites' current layouts will work unchanged. Test one platform at a time with `dry_run: true`, then verify actual page behavior before authorizing submissions.

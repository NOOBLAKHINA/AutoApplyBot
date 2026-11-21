"""Tailor the base resume to a job description with Claude, render it to PDF with system Chrome.

Needs Anthropic credentials (ANTHROPIC_API_KEY). Without them, or on any error,
tailor() returns the original resume so applying still works.
"""
import base64
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "tailored_resumes"
MODEL = "claude-opus-5-5"

SYSTEM = """You tailor resumes to job descriptions.

Rules — these are strict:
- Use ONLY facts present in the candidate's resume. Never invent employers, titles, dates,
  degrees, projects, metrics, certifications or skills the candidate doesn't show.
- You may reorder sections and bullets, rewrite the summary, rephrase bullets to use the job
  description's vocabulary where it truthfully describes the same work, and drop or shorten
  less relevant items.
- Keep contact details, employers, titles and dates exactly as in the original.
- Keep it to the same length as the original or shorter (1–2 A4 pages).

Output a single complete HTML document (inline CSS only, no scripts, no external assets),
clean professional single-column layout, ATS-friendly (real text, standard headings, no tables
for layout, no images). Output only the HTML, starting with <!DOCTYPE html>."""

_client = None
_base_pdf_b64 = None
_disabled = False  # set after an auth failure so we don't retry every job


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def tailor(base_resume, job_key, title, company, description):
    """Return a path to a tailored PDF for this job (cached), or base_resume on failure."""
    base = Path(base_resume)
    out = OUT_DIR / re.sub(r"[^\w.-]", "_", str(job_key)) / base.name  # keep the original filename for recruiters
    global _disabled
    if out.exists():
        return str(out)
    if _disabled or not description.strip():
        return str(base)
    try:
        html = _generate_html(base, title, company, description)
        out.parent.mkdir(parents=True, exist_ok=True)
        _html_to_pdf(html, out)
        print(f"    tailored resume -> {out.relative_to(ROOT)}")
        return str(out)
    except Exception as e:
        if "authentication" in str(e).lower() or type(e).__name__ == "AuthenticationError":
            _disabled = True
            print("    resume tailoring OFF: no valid ANTHROPIC_API_KEY – using original resume for this run")
        else:
            print(f"    resume tailoring failed ({type(e).__name__}: {e}) – using original resume")
        return str(base)


NOTE_SYSTEM = """You write short application notes to startup founders / hiring managers.
80-120 words, plain text, first person, no greeting fluff, no sign-off placeholders.
Say why this specific role fits, citing 2-3 concrete things from the candidate's resume.
Use ONLY facts in the resume — never invent experience, skills, metrics or interest claims
about the company beyond what the job description says. Output only the note."""


def write_note(base_resume, title, company, description):
    """Claude-written note for this job, or None (no key / error) so the caller uses the template."""
    global _disabled
    if _disabled or not description.strip():
        return None
    try:
        text = _ask(Path(base_resume), NOTE_SYSTEM, title, company, description,
                    "Write the application note for this job.", max_tokens=4000).strip()
        return text if 200 < len(text) < 1500 else None
    except Exception as e:
        if "authentication" in str(e).lower() or type(e).__name__ == "AuthenticationError":
            _disabled = True
            print("    Claude OFF: no valid ANTHROPIC_API_KEY – using template note / original resume")
        else:
            print(f"    note writing failed ({type(e).__name__}) – using template note")
        return None


def _ask(base, system, title, company, description, instruction, max_tokens=16000):
    global _base_pdf_b64
    if _base_pdf_b64 is None:
        _base_pdf_b64 = base64.standard_b64encode(base.read_bytes()).decode()
    response = _get_client().beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": [
            # Resume first and cached: it's identical for every job, only the JD changes.
            {"type": "document", "cache_control": {"type": "ephemeral"},
             "source": {"type": "base64", "media_type": "application/pdf", "data": _base_pdf_b64}},
            {"type": "text", "text": f"Job title: {title}\nCompany: {company}\n\n"
                                     f"Job description:\n{description[:12000]}\n\n{instruction}"},
        ]}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("output truncated")
    return "".join(b.text for b in response.content if b.type == "text")


def _generate_html(base, title, company, description):
    text = _ask(base, SYSTEM, title, company, description, "Tailor the attached resume to this job.")
    start = text.find("<!DOCTYPE")
    if start < 0 or "</html>" not in text:
        raise RuntimeError("no HTML in response")
    return text[start:text.rfind("</html>") + len("</html>")]


def _html_to_pdf(html, out):
    from auto_apply import CHROME
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "resume.html"
        src.write_text(html, encoding="utf-8")
        # Separate throwaway profile so it doesn't clash with the bot's running Chrome.
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={tmp}/profile", f"--print-to-pdf={out}", src.as_uri()],
                       check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError("PDF render failed")

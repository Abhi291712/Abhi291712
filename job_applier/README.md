# Job Application Bot

Automates job applications on **CVS Health** and **UHC (UnitedHealth Group)** career sites using 3 profiles: Healthcare, Finance, General.

## Setup

### 1. Install Python dependencies
```bash
cd job_applier
pip install -r requirements.txt
playwright install chromium
```

### 2. Add your 3 resumes
Place your PDF resumes in the `resumes/` folder:
```
resumes/healthcare_resume.pdf
resumes/finance_resume.pdf
resumes/general_resume.pdf
```

### 3. Check your credentials
Credentials are in `.env` (never committed to git):
```
EMAIL=abhisheknkp7292@gmail.com
PASSWORD=********
```

## Usage

```bash
python main.py
```

You'll be prompted to:
1. Pick a profile (Healthcare / Finance / General)
2. Pick a site (CVS / UHC / Both)
3. Set how many jobs to apply to
4. Run in background or watch it

## How It Works

1. Reads your resume PDF
2. Extracts name + phone from it
3. Logs into career site
4. Searches jobs matching your profile keywords
5. Fills and submits each application with your resume + cover letter
6. Logs results to `logs/apply.log`

## Files

- `main.py` — run this
- `cvs_apply.py` — CVS automation
- `uhc_apply.py` — UHC automation
- `resume_parser.py` — extracts text from PDF/DOCX
- `profiles/*.json` — edit job titles, keywords, cover letter per profile
- `.env` — your credentials (local only, not in git)

## Safety Notes

- Always watch the first run (uncheck headless) to make sure it behaves
- Career sites change layouts often — selectors may need tweaks
- Don't run too many applications at once (risk of rate-limiting)

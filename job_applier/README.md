# Job Application Bot (Multi-Site)

Automated job applications across **7 sites** targeting Data Science / ML / AI / Automation Engineer roles.

## Supported Sites

| Site | Feature |
|------|---------|
| **LinkedIn** | Easy Apply (multi-step) |
| **Indeed** | Indeed Apply |
| **Dice** | Easy Apply (great for tech) |
| **Glassdoor** | Easy Apply |
| **ZipRecruiter** | 1-Click Apply |
| **CVS Health** | Career portal |
| **UHC** | Career portal |

## Profiles

Each profile maps to a resume and targets different roles:

| Profile | Resume | Target Roles |
|---------|--------|--------------|
| **Healthcare** | `Abhishekcvs.pdf` | Healthcare Data Scientist, Clinical ML/AI Engineer |
| **Finance** | `ABHISHEK@NW.pdf` | Credit Risk DS, Quant ML, Finance AI/Automation |
| **General** | `Generative AI Engineer.pdf` | Data Scientist, ML/AI/Automation Engineer, LLM/GenAI |

## Setup

```bash
cd job_applier
pip install -r requirements.txt
playwright install chromium
```

Credentials already in `.env` (local only, git-ignored).

## Usage

```bash
python main.py
```

Prompts:
1. Pick profile (1=Healthcare, 2=Finance, 3=General)
2. Pick sites (e.g. `1,2,3` or `A` for all)
3. Max jobs per search
4. Headless Y/N (pick **N** for first run)

## Project Structure

```
job_applier/
├── main.py                  # Entry point
├── config.py                # Loads .env + profiles
├── sites/
│   ├── base.py              # Shared JobSite base class
│   ├── linkedin.py
│   ├── indeed.py
│   ├── dice.py
│   ├── glassdoor.py
│   ├── ziprecruiter.py
│   ├── cvs.py
│   └── uhc.py
├── profiles/
│   ├── healthcare.json
│   ├── finance.json
│   └── general.json
├── logs/apply.log           # Run history
└── .env                     # Credentials (local)
```

## Tips

- **Watch the first run.** Sites have CAPTCHAs + bot detection.
- **Start small** — 3 jobs per site is safe.
- **LinkedIn/Indeed** are best for bulk applications.
- **Dice** is most relevant for your tech stack.
- If selectors break, edit the respective `sites/*.py` file.

## Adding a New Site

Create `sites/newsite.py`:
```python
from sites.base import JobSite

class NewSite(JobSite):
    name = "newsite"
    login_url = "https://..."
    async def login(self, page): ...
    async def search_and_apply(self, page, job_title, max_jobs): ...
```

Register in `sites/__init__.py`.

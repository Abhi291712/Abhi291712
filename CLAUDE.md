# CLAUDE.md - AI Assistant Guide

This document provides guidance for AI assistants (like Claude) working with this repository. It outlines the codebase structure, development workflows, and key conventions to follow.

## Repository Overview

**Repository:** Abhi291712/Abhi291712
**Status:** Active - voice-agent-gateway FastAPI service
**Last Updated:** 2026-09-28

### Purpose

voice-agent-gateway: a FastAPI backend connecting voice AI agents (e.g. Retell AI) to business systems. It manages call records, processes signed call webhooks, and exposes tool endpoints voice agents call mid-conversation. See README.md and docs/LEARN.md.

### Technology Stack

[To be filled as the project develops]

**Primary Languages:**
- Python 3.11+

**Frameworks & Libraries:**
- FastAPI, Pydantic v2, pydantic-settings
- SQLAlchemy 2.0 (SQLite default, PostgreSQL via DATABASE_URL), httpx

**Build Tools:**
- pip (requirements.txt / requirements-dev.txt), ruff, pytest, Docker, GitHub Actions

## Project Structure

```
.
├── CLAUDE.md                 # This file - AI assistant guide
├── README.md                 # Project documentation (to be created)
├── .gitignore               # Git ignore patterns (to be created)
└── [Additional directories as project grows]
```

### Key Directories

[To be filled as project structure develops]

- `/src` - Source code
- `/tests` - Test files
- `/docs` - Documentation
- `/config` - Configuration files
- `/scripts` - Build and utility scripts

## Development Workflow

### Branch Strategy

**Main Development Branch:** `main` (or `master`)

**Feature Branches:**
- Prefix: `claude/`
- Naming: `claude/<descriptive-name>-<session-id>`
- Example: `claude/claude-md-mis68ecac91rmcdp-01ASfpBjCSxg7W5eHbgri5M4`

### Git Conventions

**Commits:**
- Write clear, descriptive commit messages
- Use present tense ("Add feature" not "Added feature")
- Reference issues/PRs when applicable
- Format: `<type>: <description>`
  - Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

**Examples:**
```
feat: Add user authentication module
fix: Resolve null pointer exception in data parser
docs: Update API documentation for v2.0
refactor: Simplify database connection logic
test: Add unit tests for utility functions
```

**Pushing Changes:**
- Always use: `git push -u origin <branch-name>`
- Branch names must start with `claude/` and include session ID
- Retry on network failures with exponential backoff (2s, 4s, 8s, 16s)

### Pull Requests

**Creating PRs:**
1. Ensure all tests pass
2. Update relevant documentation
3. Provide clear description of changes
4. Include test plan or verification steps

**PR Format:**
```markdown
## Summary
- Brief description of changes
- Why these changes were made

## Changes Made
- Change 1
- Change 2
- Change 3

## Test Plan
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing completed
- [ ] Documentation updated
```

## Coding Conventions

### General Principles

1. **Simplicity First**
   - Avoid over-engineering
   - Don't add features beyond what's requested
   - Keep solutions focused and simple

2. **Code Quality**
   - Write self-documenting code
   - Add comments only where logic isn't self-evident
   - Follow established patterns in the codebase

3. **Security**
   - Prevent common vulnerabilities (XSS, SQL injection, command injection)
   - Validate input at system boundaries
   - Don't expose sensitive data

4. **Testing**
   - Write tests for new features
   - Maintain or improve code coverage
   - Test edge cases and error conditions

### Style Guidelines

[To be filled based on project language and team preferences]

**Naming Conventions:**
- Variables: [camelCase/snake_case/etc]
- Functions: [camelCase/snake_case/etc]
- Classes: [PascalCase/etc]
- Constants: [UPPER_CASE/etc]

**File Organization:**
- [Guidelines for file structure]
- [Import/export patterns]
- [Module organization]

**Documentation:**
- [JSDoc/docstrings/etc requirements]
- [README standards]
- [API documentation approach]

## AI Assistant Specific Guidelines

### Before Making Changes

1. **Always Read First**
   - Read files before modifying them
   - Understand existing code patterns
   - Check for similar implementations

2. **Research the Codebase**
   - Use `Grep` to search for patterns
   - Use `Glob` to find relevant files
   - Use `Task` tool with `Explore` agent for broad exploration

3. **Plan Complex Tasks**
   - Use `TodoWrite` tool for multi-step tasks
   - Break down large features into smaller steps
   - Track progress and mark items complete

### Making Changes

1. **Prefer Edit Over Write**
   - Always use `Edit` for existing files
   - Only use `Write` for new files when necessary
   - Preserve existing code style and patterns

2. **Avoid Unnecessary Changes**
   - Don't refactor code unless requested
   - Don't add error handling for impossible scenarios
   - Don't create abstractions for one-time operations
   - Don't add comments to unchanged code

3. **Security Considerations**
   - Check for OWASP top 10 vulnerabilities
   - Validate user input appropriately
   - Use parameterized queries for databases
   - Sanitize output to prevent XSS

4. **Testing Changes**
   - Run existing tests after changes
   - Add new tests for new functionality
   - Verify builds succeed

### Communication

1. **Be Concise**
   - Keep responses short and clear
   - Use markdown formatting for readability
   - Avoid emojis unless requested

2. **Reference Code Properly**
   - Use `file_path:line_number` format
   - Example: `src/utils/parser.ts:42`

3. **Ask When Uncertain**
   - Clarify ambiguous requirements
   - Confirm architectural decisions
   - Verify assumptions before implementing

## Common Tasks

### Starting New Development

```bash
# Fetch latest changes
git fetch origin

# Create feature branch
git checkout -b claude/<feature-name>-<session-id>

# Make changes...

# Commit changes
git add .
git commit -m "feat: <description>"

# Push to remote
git push -u origin claude/<feature-name>-<session-id>
```

### Running Tests

[To be filled with project-specific test commands]

```bash
# Run all tests
pytest

# Run specific test suite
pytest tests/test_webhooks.py

# Lint and format check (same as CI)
ruff check . && ruff format --check .
```

### Building the Project

[To be filled with project-specific build commands]

```bash
# Development server
uvicorn app.main:app --reload

# Production image + Postgres
docker compose up --build
```

### Deployment

[To be filled with deployment procedures]

## Dependencies

### Installing Dependencies

[To be filled based on package manager]

```bash
python -m venv .venv && source .venv/bin/activate
# Runtime only
pip install -r requirements.txt
# Runtime + test/lint tools
pip install -r requirements-dev.txt
```

### Managing Dependencies

- Document why dependencies are added
- Keep dependencies up to date
- Remove unused dependencies
- Check for security vulnerabilities

## Troubleshooting

### Common Issues

[To be filled as common issues are identified]

**Issue:** [Description]
**Solution:** [How to resolve]

**Issue:** [Description]
**Solution:** [How to resolve]

## Resources

### Documentation

- [Link to main documentation]
- [Link to API documentation]
- [Link to architecture docs]

### External Resources

- [Relevant framework documentation]
- [Style guides]
- [Best practices]

## Maintenance

### Updating This Document

This document should be updated when:
- Project structure changes significantly
- New conventions are established
- Development workflows are modified
- Common issues and solutions are identified
- Technology stack changes

**Process:**
1. Make changes to CLAUDE.md
2. Commit with message: `docs: Update CLAUDE.md - <brief description>`
3. Push to current branch or create PR for review

### Document History

- **2025-12-05**: Initial creation - Repository setup and template structure
- **2026-09-28**: Added voice-agent-gateway; filled in stack, test and build commands. Layering rule: routers = HTTP only, services = business logic, repositories = DB access; every file starts with an explanatory docstring.

---

## Quick Reference

### Essential Commands

```bash
# Git operations
git status                           # Check current status
git fetch origin                     # Fetch latest changes
git checkout -b <branch>             # Create new branch
git add .                           # Stage all changes
git commit -m "type: message"       # Commit changes
git push -u origin <branch>         # Push to remote

# [Project-specific commands to be added]
```

### File Patterns

```bash
# Find files
**/*.js                             # All JavaScript files
**/*.test.js                        # All test files
src/**/*.ts                         # All TypeScript in src

# [Project-specific patterns]
```

### Contact & Support

- **Repository Owner:** Abhi291712
- **Issues:** [Link to issues]
- **Discussions:** [Link to discussions]

---

**Note to AI Assistants:** This is a living document. As you work with this repository and discover new patterns, conventions, or important information, please update this document to help future AI assistants work more effectively.

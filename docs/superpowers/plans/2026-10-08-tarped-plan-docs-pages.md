# Bilingual Documentation and GitHub Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` after owner approval. Publishing repository metadata or Pages needs a separate publish approval.

**Goal:** Подготовить точную двуязычную документацию и доступную публичную страницу проекта с проверяемой локальной сборкой.

**Architecture:** Сначала automated RU/EN/link/diagram parity checks, затем статическая landing page и PR build validation. Live Pages deployment и GitHub metadata меняются только после подтверждения canonical URL.

**Tech Stack:** Markdown, Python validation scripts, pinned `@mermaid-js/mermaid-cli` 12.0.0 (Node.js >=22.13.0), HTML/CSS, GitHub Actions Pages workflow.

**Spec:** `docs/superpowers/specs/2026-10-08-tarped-product-redesign-design.md`, разделы 13–14.

## Global Constraints

- README and landing page stay semantically equal in RU/EN.
- Do not claim unperformed Mac/Windows/Telegram/VPS acceptance.
- Fix Mermaid syntax and validate actual fenced diagrams before publication.
- Honor keyboard accessibility, mobile layout, and `prefers-reduced-motion`.
- No analytics, trackers, secrets, or unapproved GitHub metadata/domain change.
- “GitHub profile” here means the `invinby/XIDER` project repository metadata, not the owner’s personal account profile; keep the short repository description bilingual within the field limit.

## Review Focus

1. RU and EN copy disagree on permissions/status — parity checker fails.
2. Mermaid parser fails because of labels/line breaks — PR validation fails.
3. Reduced-motion is enabled — optional animation is removed.
4. Narrow viewport or keyboard-only navigation — page remains readable/usable.
5. Canonical URL is not approved — deployment and homepage update remain disabled.

---

**Дата:** 2026-10-08  
**Статус:** Tasks 1–4 реализованы и проверены. GitHub Pages опубликован через Actions на подтверждённом canonical URL; live acceptance пройдена 2026-10-12.
**Спецификация:** раздел 13 и документальные критерии раздела 14 [TARPED redesign](../specs/2026-10-08-tarped-product-redesign-design.md)

## Цель

Сделать GitHub-страницу XIDER понятной на русском и английском, с одинаковыми фактами, валидной архитектурной диаграммой и отдельной аккуратной продуктовой страницей. Не менять X-LEX и не публиковать URL, который владелец не подтвердил.

## Этапы TDD/контента

### Implementation tasks

#### Task 1: RU/EN README parity checker and Mermaid validation

**Files:** Preserve the already-requested edits to `README.md`, `README.ru.md`, and `tools/test_readme_mermaid.py` when moving from the current branch to the approved `origin/main` work branch; extend the existing Mermaid regression coverage rather than duplicate it. Create `tools/test_readme_parity.py`, `tools/validate_mermaid.py`, `tools/test_validate_mermaid.py`, and pinned `tools/package.json`/`tools/package-lock.json`; modify `README.md`, `README.ru.md`, `CHANGELOG.md`, `SECURITY.md`, `.github/workflows/ci.yml`.

**Interface:** `check_readme_pair(english: str, russian: str) -> list[str]` returns missing required sections, broken local links, unmatched fences or language-only status claims. `validate_mermaid.py` extracts every fenced diagram and sends each to the pinned Mermaid CLI parser, returning nonzero with file/block/line diagnostics for invalid syntax.

- [x] Add tests for a missing translation section, broken relative link, unclosed code fence, missing Mermaid block and malformed Mermaid syntax.
- [x] Run `python -m pytest tools/test_readme_parity.py -q` from repo root; fixture coverage is included in the completed README validation suite.
- [x] Implement required section mapping for purpose, architecture, components, OS support, install/update/rollback, permissions/security, diagnostics, limitations, license and status; use the pinned Mermaid CLI (not a hand-written regex-only check) to parse every fenced diagram.
- [x] Compare the full Russian and English content semantically, not just headings; validate relative/HTTPS links, code fences and status claims without asserting unperformed device/live checks.
- [x] Cross-check root `CHANGELOG.md` and `SECURITY.md` against the same release/security facts and verify README links to them plus the GitHub release notes; keep the canonical release URL distinct from the repository homepage/Pages URL.
- [x] Align both READMEs and correct every Mermaid diagram; do not claim unperformed live/device acceptance.
- [x] Rerun; both documents pass parity/format checks and Mermaid CLI validation.
- [x] Commit checker and bilingual README.

#### Task 2: Landing-page source and accessibility checks

**Files:** Create `site/index.html`, `site/styles.css`, assets only if needed, `tools/check_site.py`, and `tools/test_site.py`.

**Interface:** Static page works without JavaScript; RU/EN switch uses ordinary links; optional animation obeys `prefers-reduced-motion`.

- [x] Add checker tests for missing title/lang, broken internal link, reduced-motion rule and keyboard-visible focus.
- [x] Run `python -m pytest tools/test_site.py -q`; expected: failures for missing site/checker.
- [x] Build responsive semantic HTML/CSS with no remote fonts, analytics, trackers or secrets.
- [x] Run `python tools/check_site.py`; expected: exit 0 with no accessibility/link violations.
- [x] Inspect local desktop and narrow-viewport render and note actual results.
- [x] Commit page and checks.

#### Task 3: Pages workflow and guarded publication

**Files:** Create `.github/workflows/pages.yml`; update `.github/workflows/ci.yml` for the Python static syntax check; add workflow assertions using a focused `tools/test_pages_workflow.py`.

**Interface:** PR jobs build/check only; deployment is limited to an approved branch/environment and a confirmed canonical URL.

- [x] Test that pull requests do not deploy and unapproved refs cannot publish.
- [x] Run `python -m pytest tools/test_pages_workflow.py -q`; expected: tests fail until guarded workflow exists.
- [x] Add least-privilege, pinned-action workflow consistent with existing CI policy.
- [x] Add `python -m compileall -q TG-BOT-SERVER XGENT-WDS XGENT-MCS ops tools deploy` to CI, alongside existing bash and Windows PowerShell syntax checks; exclude generated virtual environments and build outputs.
- [x] Rerun workflow and site checks; expected: PR validation passes without live publication.
- [x] Commit workflow. Do not deploy until separate owner confirmation.

#### Task 4: GitHub metadata and live page acceptance

**Files:** `.github/workflows/pages.yml`, `tools/test_pages_workflow.py`, and `invinby/XIDER` repository settings. Scope is the project profile, not the personal GitHub account profile.

- [x] Confirm exact canonical Pages URL with the owner: `https://invinby.github.io/XIDER/`.
- [x] Receive separate owner approval to switch Pages to GitHub Actions and publish at the confirmed URL (2026-10-12).
- [x] Merge main-only guarded deployment workflow (commit `7a58dee`), switch Pages source from legacy `main:/docs` to Actions, and publish only the validated `site/` artifact (run `38179646685`, both jobs passed).
- [x] Verify live `200` responses for `/`, `/ru.html`, and `/styles.css`; the English and Russian documents declare `lang="en"` and `lang="ru"`. Static local-link checks passed. Set repository homepage to `https://invinby.github.io/XIDER/`; preserve the existing concise bilingual description and leave the personal account profile unchanged.

## Коммиты

1. Parity checker and docs baseline.
2. Bilingual README content and diagram validation.
3. Site source/design and accessibility checks.
4. Pages CI workflow.
5. Publish metadata only after explicit confirmation of canonical URL.

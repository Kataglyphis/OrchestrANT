# GitHub Copilot Instructions

> Datei: `.github/copilot-instructions.md`

<!--
Diese Datei hieß bis 2026-09 `.github/github_copilot_instructions.md`, hat sich
im Text aber schon immer als `.github/copilot-instructions.md` bezeichnet — und
nur DEN Namen liest GitHub Copilot. Copilot hat die Datei also nie geladen; die
Umbenennung (`git mv`) macht sie erstmals wirksam.

Im selben Schritt wurden die C/C++‑, Rust‑ und Dart/Flutter‑Abschnitte entfernt
(inkl. clang-format/clang-tidy, gtest, clippy/rustfmt, cbindgen, `dart format`
und die beiden FFI/Interop‑Kapitel). OrchestrANT ist ein reines Python‑Paket:
`git ls-files` findet keine einzige .cpp/.hpp/.rs/.dart/.cmake Datei. Die Regeln
waren damit nicht nur unbenutzt, sondern aktiv schädlich — Copilot hätte
Vorschläge an Werkzeugen ausgerichtet, die das Repo nicht hat, und die
`dart format`‑Zeile widersprach zusätzlich der Schwesterdatei in OmniAccelerANT.
Der strenge uv/ruff/ty‑Teil ist unverändert erhalten.
-->

## Zweck
Diese Datei gibt GitHub Copilot (inkl. Copilot Chat) repository‑weite Hinweise, wie Vorschläge, Code‑Snippets und Antworten formuliert werden sollen. OrchestrANT ist ein **reines Python‑Paket**; maßgeblich sind daher **`uv`**, **`ruff`** und **`ty`**. Ziel ist konsistente Code‑Qualität, starke Typisierung und sichere Vorschläge.

Hinweis: In diesem Dokument meint „**uv**“ in zwei Kontexten unterschiedliche Dinge:
- **Python `uv`** = Package Manager/Runner.
- **libuv/uvloop** = native I/O‑Library bzw. deren Python‑Anbindung (siehe Abschnitt zu I/O).

---

## Kurze Zusammenfassung / About
Dieses Repository enthält ein Python‑Paket (`orchestrant/`) für Pipeline‑, Streaming‑ und Monitoring‑Workloads. Der Fokus liegt auf Performance, deterministischem Verhalten und starker Typisierung. Cython wird ausschließlich als optionaler Build‑Schritt (`CYTHONIZE=True`) für Wheels genutzt — handgeschriebenen C/C++‑, Rust‑ oder Dart‑Code gibt es im Repo nicht.

---

## Repository‑Scope
- **Behandeln mit Vorrang:** `orchestrant/`, `tests/`, `bench/`, `docs/source/`, `scripts/`.
- **Ignorieren / nur mit Vorsicht ändern:** `third_party/`, `archive/`, `build/`, `dist/`, `output/`, `logs/`, `docs/test_results/`.
- **CI/Infra:** Vorschläge für CI (z. B. GitHub Actions) nur wenn klarer Nutzen erkennbar; keine ungeprüften Änderungen an Workflows ohne Review. Die Lanes selbst liegen als reusable Workflows in ANTfrastructure — hier stehen nur deren Aufrufe.

---

## Personality / Ton
- Sprache: Deutsch (bei technischen Kommentaren kurze englische Code‑Begriffe ok).
- Ton: Präzise, technisch, knapp. 1–3 Sätze Erklärung plus minimaler Beispielcode, wenn nötig.
- Umfang: Bei trivialen Änderungen kurz; bei Architektur ausführlicher (aber nicht ausschweifend).

---

## Starke Typisierung (allgemein)
- Priorisiere klar typisierte Lösungen: Signaturen und Fehlerpfade explizit, `X | None` statt impliziter Nulls.
- Copilot soll Typannotation vorschlagen, falls weggelassen.

### Type‑Safety‑Policy (maximal)
- Öffentliche APIs müssen vollständig typisiert sein; keine „untyped“ Escape‑Hatches (`Any`, unparametrisierte Generics) ohne Begründung.
- Fehlerpfade müssen typisiert sein (Result‑/Optional‑Patterns, explizite Exception‑Typen).
- Narrowing/implizite Konvertierungen vermeiden; bevorzugt explizite Casts und Wrapper‑Typen.
- Wenn Tooling das hergibt: Warnings werden in CI als Errors behandelt.

---

## Code Style & Tooling

**Python (uv + ruff + ty)**
- Package Manager/Runner: **`uv`** (Dependencies gehören in `pyproject.toml`; Lockfile/Sync über `uv`).
- Tools sollen über `uv run …` ausgeführt werden (kein „global pip“, kein ungepinnter Tool‑Mix).
- **Lint/Style: `ruff` ist MANDATORY.** Alle Änderungen müssen `uv run ruff check` ohne Fehler bestehen. Copilot soll:
  - Keine Linting-Fehler einführen (N, F, E501, ANN, S Rules beachten).
  - Auto-fixable Fehler mit `uv run ruff check --fix` vor Submission beheben.
  - Code präventiv für ruff schreiben (z. B. keine unused imports, proper line lengths, Type hints).
- **Formatierung: `ruff format .` häufig ausführen.** Python-Code soll regelmäßig mit `ruff format .` formatiert werden, um konsistenten Code-Stil zu gewährleisten. Copilot soll:
  - Code so schreiben, dass er `ruff format`-konform ist.
  - Bei längeren Änderungen: `ruff format .` nach Abschluss empfehlen.
- **Typisierung: `ty` (Pyre Type Checker) ist MANDATORY.** Alle Änderungen müssen `uv run ty check` ohne Fehler bestehen. Copilot soll:
  - Vollständige Typannotationen für **alle öffentlichen Funktionen und Klassen**.
  - `from __future__ import annotations` am Datei-Anfang verwenden.
  - Moderne Python 3.11+ Syntax für Types: `list[T]`, `dict[K,V]`, `tuple[T,...]`, `X | None` (statt `List`, `Dict`, `Tuple`, `Optional`).
  - **Niemals** `Any` verwenden ohne Begründung. Wenn `Any` nötig: `# type: ignore[assignment]` mit Kommentar.
  - Collections/Generics immer parametrisieren (`list[str]` statt `list`).
  - Return types explizit für alle Functions (auch `-> None` wenn leer).
  - Type-Stubbereiche dokumentieren (z. B. `dict[str, int]` nicht `dict[Unknown, Unknown]`).
- **Type‑Safety Regeln (streng):**
  - Öffentliche APIs vollständig annotieren; keine Escapes wie `cast()` ohne Begründung.
  - `Result`/Optional-Patterns für Fehlerpfade; explizite Exception-Handling.
  - Keine `# type: ignore`-Comments ohne Nachbar-Kommentar ("why this is needed").
  - Runtime type mismatch (z. B. `dict` assigned to `list`-typed variable) sind **Fehler**.
- **Versions‑Pins:** `ruff` ist in `pyproject.toml` exakt gepinnt und folgt ANTfrastructures `linux/scripts/01-core/versions.env` (`RUFF_VERSION`); derselbe Wert steht in `.pre-commit-config.yaml`. Copilot soll keine der drei Stellen einzeln anheben.

---

## CI / Checks (nur Kommandos, keine Workflow‑Änderungen)
Copilot soll bei „How to validate“ bevorzugt konkrete, reproduzierbare Kommandos vorschlagen:
- **Python (uv) — MANDATORY Checks vor JEDEM Commit:**
  - `uv sync` (um Dependencies zu aktualisieren).
  - `uv run ruff format .` — **Code formatieren (häufig ausführen, nicht nur vor Commit).**
  - `uv run ruff check .` — **MUSS NULL Fehler haben; keine Warnungen ignorieren.**
  - `uv run ruff check --fix .` — auto-fixable Fehler automatisch beheben.
  - `uv run ty check .` — **MUSS NULL Fehler/unresolved-Types haben.**
  - Wenn lokale Checks bestehen: erst dann committen/pushen.
  - **In CI:** Diese Checks sind echte Merge-Blockers — kein PR-Merge ohne grüne
    ruff- und ty-Checks. Das war bis 2026-09 **nicht** wahr: beide Lanes riefen
    die Werkzeuge nicht-blockierend auf (Linux über ANTfrastructures Treiber mit
    `|| true` hinter jeder Zeile, Windows über `Invoke-BuildOptional`, das den
    Fehler nur als `AllowedFailure` protokolliert). Gemessen an diesem Baum:
    ruff meldete 63 Findings, während beide Lanes grün waren. Der Satz gilt jetzt,
    weil beide Lanes den Exit-Code aus den Ergebnissen von codespell, bandit,
    vulture, `ruff check`, `ruff format` und `ty` bilden.
  - **Die Reparatur liegt seit 2026-09-08 in ANTfrastructure, nicht mehr hier.**
    Der Hub-Treiber gatet selbst (die sechs `|| true` und vier `2>/dev/null`
    sind weg, die Werkzeuge laufen über `01-core/gates.sh`), deshalb ist
    `scripts/linux/ci_static_analysis.sh` wieder ein dünner Wrapper. Auf der
    Windows-Seite nutzt der Static-Analysis-Schritt in `Build-Windows.ps1` die
    Hub-Zwillinge `Invoke-BuildGate` / `Assert-BuildGates` statt einer lokalen
    `$script:GateFailures`-Liste. Beide Aggregatoren scheitern auch dann, wenn
    GAR KEIN Gate lief — ein leerer Durchlauf darf nicht grün melden. Änderungen
    an der Werkzeugliste gehören ab jetzt upstream.
  - **CI prüft `--no-fix` / `--check`.** Lokal darf und soll `ruff check --fix`
    bzw. `ruff format` laufen; die Lanes rufen `ruff check --no-fix` und
    `ruff format --check --diff` auf, denn `--fix` meldet nur, was es *nicht*
    reparieren konnte, und der CI-Checkout wird danach verworfen.
  - **Findings werden behoben, nicht stummgeschaltet.** Kein pauschales `noqa`,
    keine `per-file-ignores` „auf Verdacht“. Ist eine Regel für dieses Projekt
    wirklich unpassend, wird GENAU DIESE Regel in `pyproject.toml` mit
    Begründung deaktiviert (siehe `CPY001` und `unused-ignore-comment` dort).
- Tests: `uv run pytest tests/unit -v`.
- Windows‑Pipeline lokal: `pwsh scripts/windows/Build-Windows.ps1` — der
  Static-Analysis-Schritt lässt den Lauf jetzt mit `exit 1` enden.

**I/O / asynchrone Pfade**
- Für uvloop/uvicorn‑artige APIs: keine blockierenden Aufrufe in Event‑Loop‑Callbacks vorschlagen.
- Prefer async patterns and explicit threading/futures when interacting with native I/O.

---

## Testing & Qualität
- Neue Features sollten Unit‑Tests enthalten. Copilot soll Test‑Skeletons vorschlagen, die vorhandene Fixtures nutzen.
- Vermeide flakige Tests; prefer deterministic seeds and small inputs.
- Coverage: Mindestens smoke tests für kritische Pfade.
- Die Test‑Matrix läuft gegen 3.13 und 3.14. Nur der free‑threaded Build **3.14t** darf fehlschlagen, ohne CI zu blockieren — Copilot soll diese Toleranz nicht auf weitere Versionen ausdehnen.

---

## Sicherheit & Geheimnisse
- Niemals API‑Keys, Passwörter, private Zertifikate oder sonstige Secrets einchecken oder vorschlagen.
- Wenn Copilot möglichen Secret‑Leak erkennt, soll es auf Vault/Secret‑Manager oder `.gitignore` hinweisen.

---

## Lizenz & rechtliche Hinweise
- Repository‑Lizenz: **MIT** (siehe `LICENSE`). Copilot soll Lizenz‑Header nur vorschlagen, wenn klar passend.

---

## Commit‑ und PR‑Konventionen
- Commit‑Format: **Conventional Commits** (`feat:`, `fix:`, `chore:`, `docs:` etc.).
- PRs: kurze Zusammenfassung + „Changes“ + „Testing/How to validate“ und CI‑Status.
- Keine squash‑commits für sicherheitsrelevante Änderungen ohne Review.

---

## Do / Don't (kurz)
**Do**
- Kleine, überprüfbare Änderungen vorschlagen.
- Typannotationen an öffentliche APIs ergänzen.
- Tests + CI‑Checks vorschlagen.
- **Python-spezifisch:**
  - Immer `from __future__ import annotations` am Anfang jeder `.py`-Datei.
  - Vollständige Typannotationen für alle Functions: `def foo(x: int, y: str) -> bool:`.
  - Modern Python syntax: `list[str]` statt `List[str]`, `X | None` statt `Optional[X]`.
  - Local `# type: ignore` mit Begründung-Kommentar.
  - Nach jeder Änderung: `uv run ruff check --fix` + `uv run ty check` lokal laufen.

**Don't**
- Große, invasive Refactorings ohne PR‑Diskussion vorschlagen.
- Secrets oder unsichere Defaults einfügen.
- **Python-spezifisch:**
  - Keine `Any`-Types ohne Begründung. `# type: ignore` ist **kein** Ersatz für Type-Annotations.
  - Keine unused imports, unused variables, oder style violations (ruff wird nicht übersehen).
  - Keine `# noqa` oder `# type: ignore` Comments ohne Kontextuellen Kommentar.
  - Code mit `ruff check` Fehlern nicht vorschlagen; Code muss durch `ruff check --fix` gehen.

---

## Beispiele / Snippets
> *Hinweis: Die maßgeblichen Style‑Configs stehen in `pyproject.toml` (`[tool.ruff]`, `[tool.ty]`). Konkrete Codebeispiele bitte dort ausrichten, statt neue Konfigurationsdateien einzuführen.*

---

## Verhalten bei Unsicherheit
- Wenn Anforderungen unklar sind, soll Copilot Rückfragen vorschlagen (z. B. „Gegen welche Python‑Version soll das laufen?“) anstatt zu raten.
- Für kritische Bereiche (Concurrency, Ressourcen‑Lifetimes, Streaming‑Backpressure) präferiere conservative, safe Vorschläge und verweise auf Tests.

---

## Anpassung & Pflege
- Passe die Datei an, wenn sich die unterstützten Python‑Versionen ändern oder neue CI‑Checks/Tools (z. B. OSS security scanners) hinzukommen.
- Sollte je nicht‑Python‑Code hinzukommen, gehören die zugehörigen Regeln hierher — aber erst dann.

---

*Ende der projekt‑spezifischen Copilot‑Anleitung.*

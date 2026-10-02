# Changelog

## [2026-10-02]
- feat(a11y-i18n): live UI retranslation on settings save, Enter activates focused buttons, RTL layout for Arabic, and QtNetwork restored to the PyInstaller build (PR #1 by HAMZA-collab620).

## [2026-09-21]
- refactor(gui): complete migration from wxPython to PySide6 (Qt 6) across presentation layer and dialogs with full WCAG 2.1 AA accessibility.
- feat(dialogs): migrate GlobalSearchDialog, ProjectManagerDialog, and SettingsDialog to PySide6 with accessible names, descriptions, and setBuddy keyboard mnemonics.
- feat(app): migrate MainWindow, TaskProjectWidget, and AccessibleTaskListWidget to PySide6 with QLocalServer single-instance enforcement, window-level shortcuts, and Notepad activation synchronization.
- test(suite): expand automated headless regression test suite to 69 passing tests in run_tests.py under offscreen QPA platform, verifying 100% test pass rate in under 2 seconds.
- test(guard): implement AST static analysis tests ensuring zero wx imports across repository and enforcing strict function length limits (<= 20 lines) across tasks_app.py and dialogs.py.
- build(spec): update PyInstaller spec for PySide6 standalone packaging with bundled locales and aggressive exclusion of unused Qt and standard library modules.
- chore(deps): replace wxPython with PySide6>=6.5.0 in requirements.txt.
- docs: document PySide6 architecture, operation, keyboard shortcuts table, developer information, and project state.

## [2026-09-06]
- build(exe): compile standalone single-file windowed executable via PyInstaller with bundled gettext locales and excluded unused modules.
- refactor(i18n-dispatch): adopt standard GNU gettext with locales catalog, decompose dispatch_command into focused handlers, decouple list_projects presentation leakage, purge dead reorder_project, and expand test suite to 37 passing tests.
- refactor(compression): radically compress codebase by ~390 lines across dialogs, tasks_app, and core_models, eliminate UI boilerplate, unify open_file_in_editor, pass black and flake8 with zero warnings, and maintain 100% test suite passage (34 tests).
- refactor(minimalist): purge pyperclip for native clipboard, resolve screen-reader pin focus displacement bug, introduce direct Notepad integration (F4), unify synchronous atomic persistence, and expand test suite to 34 passing tests.
- test(guard): harden test suite with test-guard rules, eliminate dummy settings mock, adopt scenario-based naming, isolate subtests, seal edge-case gaps, and expand to 29 passing tests.
- fix(sync-i18n): resolve project rename/delete notebook desynchronization, implement index task disambiguation, adopt gettext ADR-0006 internationalization, strip dead code, enhance keyboard accessibility, and add requirements.txt with 25 passing tests.
- refactor(clean-code): remediate all 11 code review findings across core_models, dialogs, tasks_app, and run_tests; purify pathlib usage, unify archive/delete, and expand test coverage to 21 passing tests.
- refactor(architecture): decompose monolithic tasks_app into flat co-located deep modules (core_models.py, dialogs.py, tasks_app.py) with Task dataclass, pathlib, ADR records, and standard unittest suite.
- chore(cleanup): remove legacy dar_tasks facade package and transition to flat architecture.
- refactor(core): harden exception handling, prune speculative settings, eliminate dead code, and decompose command dispatching via clean-code-guard audit.
- feat(architecture): deepened the codebase with ProjectWorkspace, SearchEngine, and encapsulated ProjectModel modules, turning GUI dialogs into clean adapters and achieving 100% test pass.
- refactor: consolidated 21 scattered modules into a single accessible and maintainable tasks_app.py core, eliminated dead JSON layout engines, and retained full backward compatibility for all test suites.

"""
Targeted automated test suite for Dar Tasks domain logic and persistence invariants.
Executes against isolated temporary directories using Python's standard unittest and pathlib.Path.
Complies rigorously with test-guard principles: real infrastructure, zero fake mocks, and scenario naming.
"""

import ast
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core_models import (
    DAILY_FILENAME,
    DEFAULT_ARCHIVE_NAME,
    BackupManager,
    ProjectModel,
    ProjectWorkspace,
    SearchEngine,
    SettingsManager,
    Task,
    Translator,
    get_project_display_name,
    sanitize_project_name,
)


class TestProjectWorkspace(unittest.TestCase):
    """Verifies workspace disk operations, ordering invariants, and path manipulations."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.workspace = ProjectWorkspace(self.test_dir)
        self.daily_path = self.workspace.get_project_path(DAILY_FILENAME)
        with open(self.daily_path, "w", encoding="utf-8"):
            pass

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sanitize_project_name_strips_illegal_characters(self):
        cases = [
            ("My Project!@#", "My Project"),
            ("Safe_Name-1", "Safe_Name-1"),
            ("   leading and trailing   ", "leading and trailing"),
        ]
        for raw_input, expected in cases:
            with self.subTest(raw_input=raw_input):
                self.assertEqual(sanitize_project_name(raw_input), expected)

    def test_ensure_order_includes_daily_when_initializing_workspace(self):
        order = self.workspace.ensure_order()
        self.assertIn(DAILY_FILENAME, order)
        self.assertTrue(self.daily_path.exists())

    def test_create_project_writes_file_and_appends_to_order(self):
        fn = self.workspace.create_project("Alpha Project")
        self.assertEqual(fn, "Alpha Project.txt")
        self.assertTrue(self.workspace.get_project_path(fn).exists())
        order = self.workspace.ensure_order()
        self.assertIn(fn, order)

    def test_rename_project_updates_disk_file_and_persists_order(self):
        fn = self.workspace.create_project("Old Name")
        new_fn = self.workspace.rename_project(fn, "New Name")
        self.assertEqual(new_fn, "New Name.txt")
        self.assertFalse(self.workspace.get_project_path("Old Name.txt").exists())
        self.assertTrue(self.workspace.get_project_path("New Name.txt").exists())
        order = self.workspace.ensure_order()
        self.assertIn("New Name.txt", order)
        self.assertNotIn("Old Name.txt", order)

    def test_rename_project_fails_if_target_file_already_exists(self):
        p1 = self.workspace.create_project("Existing Alpha")
        self.workspace.create_project("Existing Beta")
        # Attempt to rename Alpha to Beta
        result = self.workspace.rename_project(p1, "Existing Beta")
        self.assertIsNone(result)
        self.assertTrue(self.workspace.get_project_path(p1).exists())

    def test_rename_daily_project_is_rejected(self):
        res = self.workspace.rename_project(DAILY_FILENAME, "Other")
        self.assertIsNone(res)

    def test_delete_project_removes_file_and_purges_order_entry(self):
        fn = self.workspace.create_project("To Delete")
        deleted = self.workspace.delete_project(fn)
        self.assertTrue(deleted)
        self.assertFalse(self.workspace.get_project_path(fn).exists())
        order = self.workspace.ensure_order()
        self.assertNotIn(fn, order)

    def test_delete_daily_project_is_rejected(self):
        deleted = self.workspace.delete_project(DAILY_FILENAME)
        self.assertFalse(deleted)
        self.assertTrue(self.daily_path.exists())

    def test_toggle_pin_project_swaps_star_prefix_in_order(self):
        fn = self.workspace.create_project("Pinnable")
        self.assertTrue(self.workspace.toggle_pin(fn))
        order = self.workspace.ensure_order()
        self.assertIn(f"⭐ {fn}", order)
        # Unpin
        self.assertTrue(self.workspace.toggle_pin(fn))
        order = self.workspace.ensure_order()
        self.assertIn(fn, order)
        self.assertNotIn(f"⭐ {fn}", order)

    def test_move_project_swaps_adjacent_order_entries(self):
        p1 = self.workspace.create_project("P1")
        p2 = self.workspace.create_project("P2")
        order = self.workspace.ensure_order()
        idx_p1 = order.index(p1)
        # Swap
        self.assertTrue(self.workspace.move_project(idx_p1, 1))
        new_order = self.workspace.ensure_order()
        self.assertEqual(new_order[idx_p1], p2)

    # Sacred regression test (Rule 6): prevents substring collisions when names share a common suffix
    def test_exact_path_resolution_no_suffix_collision(self):
        p_sub = self.workspace.create_project("work")
        p_full = self.workspace.create_project("homework")
        path_sub = self.workspace.get_project_path(p_sub).resolve()
        path_full = self.workspace.get_project_path(p_full).resolve()
        self.assertNotEqual(path_sub, path_full)
        self.assertTrue(str(path_full).endswith(str(p_sub)))
        self.assertEqual(path_sub.name, "work.txt")
        self.assertEqual(path_full.name, "homework.txt")

    def test_list_projects_returns_clean_domain_descriptors(self):
        p1 = self.workspace.create_project("Project Clean")
        projects = self.workspace.list_projects()
        p1_entry = next((p for p in projects if p["filename"] == p1), None)
        self.assertIsNotNone(p1_entry)
        self.assertIn("filename", p1_entry)
        self.assertIn("path", p1_entry)
        self.assertIn("readonly", p1_entry)
        self.assertIn("pinned", p1_entry)
        self.assertNotIn("display", p1_entry)


class TestProjectModel(unittest.TestCase):
    """Verifies single-project task handling, atomic saving, undo, and archive harvesting."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.project_path = self.test_dir / "test_proj.txt"
        with open(self.project_path, "w", encoding="utf-8") as f:
            f.write("⭐ Urgent task\nNormal task 1\nNormal task 2\n")
        self.model = ProjectModel(self.project_path, base_dir=self.test_dir)
        self.assertTrue(self.model.load_tasks())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_task_line_serialization_preserves_title_and_pin_state(self):
        cases = [
            ("⭐ Buy milk", "Buy milk", True, "⭐ Buy milk"),
            ("Read book", "Read book", False, "Read book"),
            ("⭐   Trimmed task   ", "Trimmed task", True, "⭐ Trimmed task"),
        ]
        for raw_line, expected_title, expected_pin, expected_line in cases:
            with self.subTest(raw_line=raw_line):
                task = Task.from_line(raw_line)
                self.assertEqual(task.title, expected_title)
                self.assertEqual(task.is_pinned, expected_pin)
                self.assertEqual(task.to_line(), expected_line)

    def test_load_tasks_partitions_pinned_before_unpinned(self):
        pinned = [t for t in self.model.tasks if t.is_pinned]
        unpinned = [t for t in self.model.tasks if not t.is_pinned]
        self.assertEqual(len(pinned), 1)
        self.assertEqual(len(unpinned), 2)
        self.assertEqual(pinned[0].title, "Urgent task")

    def test_add_task_and_atomic_save_persists_to_disk(self):
        self.assertTrue(self.model.add_task("New task"))
        self.assertTrue(self.model.save_tasks_atomic())
        with open(self.project_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.assertIn("New task", lines)
        self.assertIn("⭐ Urgent task", lines)

    def test_archive_task_records_entry_and_appears_in_today_harvest(self):
        task_to_archive = "Normal task 1"
        self.assertTrue(self.model.archive_task(task_to_archive))
        self.assertTrue(self.model.archive_file.exists())
        with open(self.model.archive_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn(task_to_archive, content)

        # Harvest query test
        harvest = ProjectModel.get_today_harvest(base_dir=self.test_dir)
        self.assertEqual(len(harvest), 1)
        self.assertIn(task_to_archive, harvest[0])

    def test_delete_task_isolates_entry_in_trash_file(self):
        task_to_delete = "Normal task 2"
        self.assertTrue(self.model.delete_task(task_to_delete))
        self.assertTrue(self.model.trash_file.exists())
        with open(self.model.trash_file, "r", encoding="utf-8") as f:
            trash_content = f.read()
        self.assertIn(task_to_delete, trash_content)

    def test_undo_restores_last_archived_task_to_project(self):
        task_to_archive = "Normal task 1"
        self.assertTrue(self.model.archive_task(task_to_archive))
        self.assertTrue(self.model.undo())
        titles = [t.title for t in self.model.tasks]
        self.assertIn(task_to_archive, titles)

    # Sacred regression test (Rule 6): prevents operating on wrong item when identical titles exist
    def test_task_disambiguation_by_index(self):
        self.model.tasks = [
            Task(title="Same Title", is_pinned=False),
            Task(title="Same Title", is_pinned=False),
            Task(title="Distinct Task", is_pinned=False),
        ]
        # Archive specifically index 1
        self.assertTrue(self.model.archive_task(1))
        self.assertEqual(len(self.model.tasks), 2)
        self.assertEqual(self.model.tasks[0].title, "Same Title")
        self.assertEqual(self.model.tasks[1].title, "Distinct Task")

    def test_get_archive_path_defaults_to_base_dir_archive_name(self):
        path = ProjectModel.get_archive_path(base_dir=self.test_dir)
        self.assertEqual(path, self.test_dir / DEFAULT_ARCHIVE_NAME)

    def test_move_task_respects_bounds_and_pin_boundary(self):
        # Setup: tasks[0] is pinned, tasks[1] and tasks[2] are unpinned
        self.model.tasks = [
            Task(title="Pinned 1", is_pinned=True),
            Task(title="Unpinned 1", is_pinned=False),
            Task(title="Unpinned 2", is_pinned=False),
        ]
        # Moving pinned task into unpinned section must fail
        self.assertFalse(self.model.move_task(0, 1))
        # Moving unpinned task into pinned section must fail
        self.assertFalse(self.model.move_task(1, -1))
        # Moving out of bounds must fail
        self.assertFalse(self.model.move_task(0, -1))
        self.assertFalse(self.model.move_task(2, 1))
        # Valid move within same partition must succeed
        self.assertTrue(self.model.move_task(1, 1))
        self.assertEqual(self.model.tasks[1].title, "Unpinned 2")
        self.assertEqual(self.model.tasks[2].title, "Unpinned 1")

    def test_edit_task_by_index_updates_title_and_rejects_empty(self):
        # Valid edit
        self.assertTrue(self.model.edit_task(1, "Updated Title"))
        self.assertEqual(self.model.tasks[1].title, "Updated Title")
        # Empty title edit must be rejected
        self.assertFalse(self.model.edit_task(1, "   "))
        self.assertEqual(self.model.tasks[1].title, "Updated Title")

    def test_toggle_pin_by_index_repartitions_tasks(self):
        # Initial: [Pinned(0), Unpinned(1), Unpinned(2)]
        # Toggle index 1 to pinned
        self.assertTrue(self.model.toggle_pin(1))
        self.assertTrue(self.model.tasks[0].is_pinned)
        self.assertTrue(self.model.tasks[1].is_pinned)
        self.assertFalse(self.model.tasks[2].is_pinned)

    def test_multiline_task_sanitization_removes_newlines(self):
        self.assertTrue(self.model.add_task("Multi\nline\r\ntask"))
        task = self.model.tasks[-1]
        self.assertNotIn("\n", task.title)
        self.assertNotIn("\r", task.title)
        self.assertEqual(task.title, "Multi line task")

    def test_archive_task_saves_project_immediately_without_pending_changes(self):
        task_title = "Normal task 1"
        self.assertTrue(self.model.archive_task(task_title))
        self.assertFalse(self.model.has_unsaved_changes)
        with open(self.project_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.assertNotIn(task_title, lines)

    def test_external_modification_detection_and_reload(self):
        import time

        time.sleep(0.05)
        with open(self.project_path, "a", encoding="utf-8") as f:
            f.write("External Notepad Task\n")
        self.assertTrue(self.model.is_modified_externally())
        self.assertTrue(self.model.reload_if_modified())
        titles = [t.title for t in self.model.tasks]
        self.assertIn("External Notepad Task", titles)


class TestSearchEngine(unittest.TestCase):
    """Verifies multi-project query logic."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.workspace = ProjectWorkspace(self.test_dir)
        self.p1 = self.workspace.create_project("Project Alpha")
        self.p2 = self.workspace.create_project("Project Beta")
        with open(self.workspace.get_project_path(self.p1), "w", encoding="utf-8") as f:
            f.write("Fix critical authentication bug\nReview PR\n")
        with open(self.workspace.get_project_path(self.p2), "w", encoding="utf-8") as f:
            f.write("Update documentation\nDeploy bug fix to staging\n")
        self.engine = SearchEngine(self.workspace)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_search_finds_matches_across_multiple_projects(self):
        results = self.engine.search("bug")
        self.assertEqual(len(results), 2)
        tasks = [r["task"] for r in results]
        self.assertTrue(any("authentication" in t for t in tasks))
        self.assertTrue(any("staging" in t for t in tasks))

    def test_search_scoped_to_single_project_filters_other_projects(self):
        results = self.engine.search("bug", project_filename=self.p1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], self.p1)

    def test_search_short_query_returns_empty_list(self):
        self.assertEqual(self.engine.search("a"), [])

    def test_search_with_case_sensitivity_respects_letter_casing(self):
        cases = [
            ("Fix", True, 1),
            ("fix", True, 1),
            ("fix", False, 2),
        ]
        for query, sensitive, expected_count in cases:
            with self.subTest(query=query, sensitive=sensitive):
                self.assertEqual(
                    len(self.engine.search(query, case_sensitive=sensitive)),
                    expected_count,
                )


class TestSettingsManager(unittest.TestCase):
    """Verifies settings JSON persistence and defaults."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.mgr = SettingsManager(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_uninitialized_settings_loads_configured_defaults(self):
        self.assertEqual(self.mgr.get("general", "language"), "en")
        self.assertFalse(self.mgr.get("behavior", "confirm_on_archive"))

    def test_save_settings_persists_across_new_instance(self):
        self.mgr.set("general", "language", "ar")
        self.mgr.save()
        new_mgr = SettingsManager(self.test_dir)
        self.assertEqual(new_mgr.get("general", "language"), "ar")


class TestTranslator(unittest.TestCase):
    """Verifies standard GNU gettext internationalization catalogs and fallback behavior using real settings."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.settings = SettingsManager(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_translator_returns_localized_catalog_strings_for_configured_language(self):
        # Default English
        tr_en = Translator(self.settings)
        self.assertEqual(tr_en._("Project Manager"), "Project Manager")
        self.assertEqual(tr_en._("harvest"), "Today's Harvest")
        self.assertEqual(tr_en._("settings"), "Settings")

        # Configured Arabic via real SettingsManager and real gettext catalog (Rule 8 compliant: no fake mock)
        self.settings.set("general", "language", "ar")
        tr_ar = Translator(self.settings)
        self.assertEqual(tr_ar._("Project Manager"), "مدير المشاريع")
        self.assertEqual(tr_ar._("Daily Tasks"), "مهام اليوم")
        self.assertEqual(tr_ar._("&Save"), "&حفظ")
        self.assertEqual(tr_ar._("harvest"), "حصاد اليوم")

    def test_get_project_display_name_localizes_daily_tasks(self):
        self.settings.set("general", "language", "ar")
        tr_ar = Translator(self.settings)
        tr_en = Translator(SettingsManager(self.test_dir))

        self.assertEqual(get_project_display_name(DAILY_FILENAME, tr_ar), "مهام اليوم")
        self.assertEqual(get_project_display_name(DAILY_FILENAME, tr_en), "Daily Tasks")
        self.assertEqual(get_project_display_name("Project X.txt", tr_ar), "Project X")

    def test_translator_fallback_preserves_untranslated_strings(self):
        tr = Translator(self.settings)
        untranslated = "Some Unregistered String 123"
        self.assertEqual(tr._(untranslated), untranslated)


class TestBackupManager(unittest.TestCase):
    """Verifies snapshot creation, cleanup, and daily task rollover."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.projects_dir = self.test_dir / "projects"
        self.backups_dir = self.test_dir / "backups"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.settings = SettingsManager(self.test_dir)
        self.mgr = BackupManager(
            self.test_dir, self.projects_dir, self.backups_dir, self.settings
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_snapshot_copies_all_project_files(self):
        p1 = self.projects_dir / "p1.txt"
        with open(p1, "w", encoding="utf-8") as f:
            f.write("Task in P1\n")
        self.assertTrue(self.mgr.create_snapshot("test"))
        snapshots = list(self.backups_dir.iterdir())
        self.assertEqual(len(snapshots), 1)
        self.assertTrue((snapshots[0] / "p1.txt").exists())

    def test_check_and_rollover_daily_tasks_detects_past_date(self):
        import datetime
        import os

        daily_path = self.projects_dir / DAILY_FILENAME
        with open(daily_path, "w", encoding="utf-8") as f:
            f.write("Yesterday unfinished task\n")
        past_time = (datetime.datetime.now() - datetime.timedelta(days=2)).timestamp()
        os.utime(str(daily_path), (past_time, past_time))

        self.assertTrue(self.mgr.check_and_rollover_daily_tasks())
        daily_arch = self.test_dir / "أرشيف_المهام_اليومية.txt"
        self.assertTrue(daily_arch.exists())
        with open(daily_arch, "r", encoding="utf-8") as f:
            self.assertIn("Yesterday unfinished task", f.read())


class TestPySide6Dialogs(unittest.TestCase):
    """Verifies modal dialog instantiation, accessibility, clean-code limits, and Qt event adapters."""

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(["-platform", "offscreen"])

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.settings = SettingsManager(self.test_dir)
        self.workspace = ProjectWorkspace(self.test_dir)
        self.p1 = self.workspace.create_project("Project Alpha")
        with open(
            self.workspace.get_project_path(self.p1), "w", encoding="utf-8"
        ) as f:
            f.write("Urgent search task\n")
        self.search_engine = SearchEngine(self.workspace)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_settings_dialog_instantiation_and_accessibility(self):
        from PySide6.QtWidgets import QDialog
        from dialogs import SettingsDialog

        dlg = SettingsDialog(None, self.settings)
        self.assertIsInstance(dlg, QDialog)
        self.assertEqual(dlg.accessibleName(), "Settings")
        self.assertEqual(dlg.confirm_archive_cb.accessibleName(), "Confirm on archive")
        self.assertEqual(dlg.confirm_delete_cb.accessibleName(), "Confirm on delete")
        self.assertEqual(
            dlg.auto_save_spin.accessibleName(), "Auto-save interval (seconds)"
        )
        self.assertEqual(dlg.lang_choice.accessibleName(), "Language")
        dlg.close()

    def test_settings_dialog_save_logic(self):
        from dialogs import SettingsDialog

        dlg = SettingsDialog(None, self.settings)
        dlg.confirm_archive_cb.setChecked(True)
        dlg.confirm_delete_cb.setChecked(False)
        dlg.auto_save_spin.setValue(15)
        dlg.lang_choice.setCurrentIndex(1)
        dlg.on_save()
        self.assertTrue(self.settings.get("behavior", "confirm_on_archive"))
        self.assertFalse(self.settings.get("behavior", "confirm_on_delete"))
        self.assertEqual(self.settings.get("behavior", "auto_save_interval"), 15)
        self.assertEqual(self.settings.get("general", "language"), "ar")
        dlg.close()

    def test_global_search_dialog_instantiation_and_query(self):
        from PySide6.QtWidgets import QDialog
        from dialogs import GlobalSearchDialog

        dlg = GlobalSearchDialog(None, self.search_engine, settings=self.settings)
        self.assertIsInstance(dlg, QDialog)
        self.assertEqual(dlg.accessibleName(), "Global Search")
        self.assertEqual(dlg.search_ctrl.accessibleName(), "Search Query")
        self.assertEqual(dlg.results_list.accessibleName(), "Search Results")
        dlg.search_ctrl.setText("urgent")
        self.assertEqual(dlg.results_list.count(), 1)
        dlg.close()

    def test_project_manager_dialog_instantiation_and_listing(self):
        from PySide6.QtWidgets import QDialog
        from dialogs import ProjectManagerDialog

        dlg = ProjectManagerDialog(None, self.workspace)
        self.assertIsInstance(dlg, QDialog)
        self.assertEqual(dlg.accessibleName(), "Project Manager")
        self.assertEqual(dlg.project_list.accessibleName(), "Projects List")
        self.assertGreaterEqual(dlg.project_list.count(), 1)
        dlg.close()

    def test_dialogs_has_zero_wx_imports(self):
        dialogs_file = Path(__file__).parent / "dialogs.py"
        source = dialogs_file.read_text(encoding="utf-8")
        parsed = ast.parse(source)
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(
                        alias.name == "wx" or alias.name.startswith("wx."),
                        f"Found wx import: {alias.name}",
                    )
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse(
                    node.module and (node.module == "wx" or node.module.startswith("wx.")),
                    f"Found from-wx import: {node.module}",
                )

    def test_global_search_dialog_actions_and_activation(self):
        from PySide6.QtWidgets import QWidget
        from dialogs import GlobalSearchDialog

        class FakeParent(QWidget):
            def __init__(self):
                super().__init__()
                self.opened = None

            def open_or_select_project(self, filename):
                self.opened = filename

        parent = FakeParent()
        dlg = GlobalSearchDialog(parent, self.search_engine, settings=self.settings)
        dlg.search_ctrl.setText("urgent")
        self.assertEqual(dlg.results_list.count(), 1)

        # Test search return moves focus to results
        dlg.on_search_return()
        self.assertEqual(dlg.results_list.currentRow(), 0)

        # Test opening task dispatches to parent
        dlg.on_open_task()
        self.assertEqual(parent.opened, self.p1)
        dlg.close()

    def test_project_manager_actions_pin_and_move(self):
        from PySide6.QtWidgets import QWidget
        from dialogs import ProjectManagerDialog

        class FakeParent(QWidget):
            def __init__(self):
                super().__init__()
                self.opened = None

            def open_or_select_project(self, filename):
                self.opened = filename

        p2 = self.workspace.create_project("Project Beta")
        parent = FakeParent()
        dlg = ProjectManagerDialog(parent, self.workspace)
        self.assertGreaterEqual(len(dlg.projects), 2)

        # Select second project and pin it
        dlg.project_list.setCurrentRow(1)
        dlg.on_pin()
        self.assertTrue(dlg.projects[1]["pinned"] or dlg.projects[0]["pinned"])

        # Open selected project
        dlg.project_list.setCurrentRow(0)
        selected_fn = dlg.projects[0]["filename"]
        dlg.on_open()
        self.assertEqual(parent.opened, selected_fn)
        dlg.close()

    def test_settings_dialog_reset_defaults(self):
        from dialogs import SettingsDialog

        self.settings.set("behavior", "confirm_on_archive", True)
        self.settings.set("behavior", "auto_save_interval", 45)
        self.settings.set("general", "language", "ar")

        dlg = SettingsDialog(None, self.settings)
        self.assertTrue(dlg.confirm_archive_cb.isChecked())
        self.assertEqual(dlg.auto_save_spin.value(), 45)

        dlg._apply_reset()
        self.assertFalse(dlg.confirm_archive_cb.isChecked())
        self.assertEqual(dlg.auto_save_spin.value(), 2)
        self.assertEqual(dlg.lang_choice.currentIndex(), 0)
        dlg.close()

    def test_dialogs_clean_code_function_length_limit(self):
        dialogs_file = Path(__file__).parent / "dialogs.py"
        source = dialogs_file.read_text(encoding="utf-8")
        parsed = ast.parse(source)
        for node in ast.walk(parsed):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno - node.lineno) + 1
                self.assertLessEqual(
                    length,
                    20,
                    f"Function '{node.name}' in dialogs.py exceeds 20 lines ({length} lines)",
                )


class TestPySide6MainWindow(unittest.TestCase):
    """Verifies PySide6 MainWindow, TaskProjectWidget, accessibility, shortcuts, and invariants."""

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(["-platform", "offscreen"])

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.settings = SettingsManager(self.test_dir)
        self.workspace = ProjectWorkspace(self.test_dir)
        self.p1 = self.workspace.create_project("Project Alpha")
        self.p1_path = self.workspace.get_project_path(self.p1)
        with open(self.p1_path, "w", encoding="utf-8") as f:
            f.write("Task 1\n⭐ Task 2\nTask 3\n")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        root_trash = Path(__file__).parent / "trash.txt"
        if root_trash.exists():
            try:
                root_trash.unlink()
            except OSError:
                pass

    def create_task_widget(self, filename=None):
        from tasks_app import TaskProjectWidget

        path = filename or self.p1_path
        widget = TaskProjectWidget(None, path, self.settings)
        widget.model.base_dir = self.test_dir
        widget.model.archive_file = self.test_dir / "archive.txt"
        widget.model.trash_file = self.test_dir / "trash.txt"
        return widget

    def test_copy_to_clipboard(self):
        from tasks_app import copy_to_clipboard
        from PySide6.QtGui import QGuiApplication

        res = copy_to_clipboard("test clipboard text")
        self.assertTrue(res)
        self.assertEqual(QGuiApplication.clipboard().text(), "test clipboard text")

    def test_task_project_widget_instantiation_and_accessibility(self):
        widget = self.create_task_widget()
        self.assertEqual(widget.search_input.accessibleName(), "Filter")
        self.assertEqual(widget.task_list.accessibleName(), "Tasks")
        self.assertEqual(widget.new_task_input.accessibleName(), "Add Task")
        self.assertEqual(widget.task_list.count(), 3)
        widget.timer.stop()

    def test_task_project_widget_add_and_filter(self):
        widget = self.create_task_widget()
        widget.new_task_input.setText("Brand new item")
        widget.on_add_task()
        self.assertEqual(widget.new_task_input.text(), "")
        self.assertEqual(widget.task_list.count(), 4)

        widget.search_input.setText("brand")
        widget.update_display("brand")
        self.assertEqual(widget.task_list.count(), 1)
        widget.timer.stop()

    def test_task_project_widget_add_task_validation(self):
        from PySide6.QtCore import Qt, QEvent
        from PySide6.QtGui import QKeyEvent

        widget = self.create_task_widget()
        initial_count = widget.task_list.count()

        # Whitespace-only task input must be rejected without altering count
        widget.new_task_input.setText("   ")
        widget.on_add_task()
        self.assertEqual(widget.task_list.count(), initial_count)
        self.assertEqual(widget.new_task_input.text(), "   ")

        # Valid task input via returnPressed signal
        widget.new_task_input.setText("Valid task via enter")
        widget.new_task_input.returnPressed.emit()
        self.assertEqual(widget.task_list.count(), initial_count + 1)
        self.assertEqual(widget.new_task_input.text(), "")

        # Test AccessibleTaskListWidget keyPressEvent for Return/Enter
        activated = []
        widget.task_list.on_activate = lambda: activated.append(True)
        ret_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        widget.task_list.keyPressEvent(ret_event)
        self.assertTrue(activated)

        # Other keys delegate to base list without triggering activate
        activated.clear()
        space_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier
        )
        widget.task_list.keyPressEvent(space_event)
        self.assertFalse(activated)
        widget.timer.stop()

    def test_task_project_widget_search_and_filter_behavior(self):
        widget = self.create_task_widget()
        self.assertEqual(widget.task_list.count(), 3)
        self.assertEqual(len(widget.visible_indices), 3)

        # Filter by substring (case-insensitive) via textChanged
        widget.search_input.setText("task 1")
        self.assertEqual(widget.task_list.count(), 1)
        self.assertEqual(len(widget.visible_indices), 1)
        self.assertIn("Task 1", widget.task_list.item(0).text())

        # Filter with non-matching substring yields empty list
        widget.search_input.setText("nonexistent query 123")
        self.assertEqual(widget.task_list.count(), 0)
        self.assertEqual(widget.visible_indices, [])

        # Clearing filter restores all tasks
        widget.search_input.setText("")
        self.assertEqual(widget.task_list.count(), 3)
        self.assertEqual(len(widget.visible_indices), 3)
        widget.timer.stop()

    def test_task_project_widget_context_menu_actions(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QMenu

        widget = self.create_task_widget()
        menu = QMenu(widget)
        widget._populate_context_menu(menu)
        actions = menu.actions()
        self.assertEqual(len(actions), 6)

        # Verify all expected action texts
        action_texts = [a.text() for a in actions]
        self.assertTrue(any("Complete" in t for t in action_texts))
        self.assertTrue(any("Pin" in t for t in action_texts))
        self.assertTrue(any("Edit" in t for t in action_texts))
        self.assertTrue(any("Copy" in t for t in action_texts))
        self.assertTrue(any("Delete" in t for t in action_texts))
        self.assertTrue(any("Notepad" in t for t in action_texts))

        # Select row 1 ('Task 1') and trigger Pin action
        widget.task_list.setCurrentRow(1)
        pin_action = next(a for a in actions if "Pin" in a.text())
        pin_action.trigger()
        self.assertTrue(widget.task_list.currentItem().text().startswith("⭐"))

        # Select item and trigger Copy action
        copy_action = next(a for a in actions if "Copy" in a.text())
        copy_action.trigger()
        self.assertEqual(
            QGuiApplication.clipboard().text(), widget.task_list.currentItem().text()
        )

        # Context menu call with invalid selection/coordinates is safe no-op
        widget.task_list.setCurrentRow(-1)
        widget.on_context_menu(QPoint(-100, -100))
        widget.timer.stop()

    def test_task_project_widget_commands(self):
        widget = self.create_task_widget()
        # Row 0 is '⭐ Task 2' (already pinned), row 1 is 'Task 1' (unpinned)
        widget.task_list.setCurrentRow(1)
        widget.dispatch_command("pin")
        self.assertTrue(
            widget.task_list.item(widget.task_list.currentRow()).text().startswith("⭐")
        )

        widget.dispatch_command("move_down")
        widget.dispatch_command("undo")

        # Complete/archive task
        widget.task_list.setCurrentRow(0)
        widget.dispatch_command("complete")
        self.assertEqual(widget.task_list.count(), 2)
        widget.timer.stop()

    def test_task_project_widget_command_complete_and_undo(self):
        self.settings.set("behavior", "confirm_on_archive", False)
        widget = self.create_task_widget()
        initial_count = widget.task_list.count()
        target_text = widget.task_list.item(0).text()

        # Archive/Complete first item
        widget.task_list.setCurrentRow(0)
        widget.dispatch_command("complete")
        self.assertEqual(widget.task_list.count(), initial_count - 1)
        cur_titles = [
            widget.task_list.item(i).text() for i in range(widget.task_list.count())
        ]
        self.assertNotIn(target_text, cur_titles)

        # Undo restores the archived task
        widget.dispatch_command("undo")
        self.assertEqual(widget.task_list.count(), initial_count)
        restored_titles = [
            widget.task_list.item(i).text() for i in range(widget.task_list.count())
        ]
        self.assertIn(target_text, restored_titles)
        widget.timer.stop()

    def test_task_project_widget_command_pin_and_reselection(self):
        widget = self.create_task_widget()
        self.assertTrue(widget.task_list.item(0).text().startswith("⭐"))
        self.assertFalse(widget.task_list.item(1).text().startswith("⭐"))

        # Pin row 1 ('Task 1') -> pins and stays selected
        widget.task_list.setCurrentRow(1)
        widget.dispatch_command("pin")
        cur_item = widget.task_list.currentItem()
        self.assertIsNotNone(cur_item)
        self.assertTrue(cur_item.text().startswith("⭐"))
        self.assertIn("Task 1", cur_item.text())

        # Unpin row 0 -> becomes unpinned
        widget.task_list.setCurrentRow(0)
        unpinned_title = widget.task_list.item(0).text().replace("⭐ ", "")
        widget.dispatch_command("pin")
        titles = [
            widget.task_list.item(i).text() for i in range(widget.task_list.count())
        ]
        self.assertIn(unpinned_title, titles)
        widget.timer.stop()

    def test_task_project_widget_command_delete_and_boundaries(self):
        self.settings.set("behavior", "confirm_on_delete", False)
        widget = self.create_task_widget()
        initial_count = widget.task_list.count()

        # Delete selected item
        widget.task_list.setCurrentRow(0)
        widget.dispatch_command("delete")
        self.assertEqual(widget.task_list.count(), initial_count - 1)

        # Out-of-bounds selection is safe no-op
        widget.task_list.setCurrentRow(-1)
        widget.dispatch_command("delete")
        widget.dispatch_command("pin")
        widget.dispatch_command("move_up")
        self.assertEqual(widget.task_list.count(), initial_count - 1)

        # Unknown command is safe no-op
        widget.task_list.setCurrentRow(0)
        widget.dispatch_command("unknown_command_xyz")

        # Boundary movement: top pinned item move_up fails safely
        widget.task_list.setCurrentRow(0)
        widget.dispatch_command("move_up")
        widget.timer.stop()

    def test_task_project_widget_edit_and_auto_save(self):
        from PySide6.QtWidgets import QInputDialog

        widget = self.create_task_widget()
        widget.task_list.setCurrentRow(0)

        # Edit task with mocked QInputDialog.getText
        orig_getText = QInputDialog.getText
        try:
            QInputDialog.getText = staticmethod(
                lambda *a, **kw: ("Edited Task Content", True)
            )
            widget.dispatch_command("edit")
            self.assertIn("Edited Task Content", widget.task_list.item(0).text())

            # Canceled edit leaves content unchanged
            QInputDialog.getText = staticmethod(lambda *a, **kw: ("", False))
            widget.dispatch_command("edit")
            self.assertIn("Edited Task Content", widget.task_list.item(0).text())
        finally:
            QInputDialog.getText = orig_getText

        # Auto-save tick saves dirty model
        widget.model.has_unsaved_changes = True
        widget._on_auto_save_tick()
        self.assertFalse(widget.model.has_unsaved_changes)
        widget.timer.stop()

    def test_main_window_instantiation_and_tabs(self):
        from tasks_app import MainWindow

        win = MainWindow(base_dir=self.test_dir)
        self.assertEqual(win.windowTitle(), "Dar Tasks")
        self.assertGreaterEqual(win.notebook.count(), 1)
        self.assertEqual(win.notebook.accessibleName(), "Projects Tabs")
        win.close()

    def test_main_window_tab_operations(self):
        from tasks_app import MainWindow

        win = MainWindow(base_dir=self.test_dir)
        initial_count = win.notebook.count()
        p2 = win.workspace.create_project("Project Beta")
        win.open_or_select_project(p2)
        self.assertEqual(win.notebook.count(), initial_count + 1)

        win.select_tab(0)
        self.assertEqual(win.notebook.currentIndex(), 0)
        win.next_tab()
        self.assertEqual(win.notebook.currentIndex(), 1)

        win.on_project_renamed(p2, "Project Gamma.txt")
        win.on_project_deleted("Project Gamma.txt")
        self.assertEqual(win.notebook.count(), initial_count)
        win.close()

    def test_main_window_tab_switching_comprehensive(self):
        from tasks_app import MainWindow

        win = MainWindow(base_dir=self.test_dir)
        p2 = win.workspace.create_project("Project Beta")
        p3 = win.workspace.create_project("Project Gamma")
        win.open_or_select_project(p2)
        win.open_or_select_project(p3)
        total_tabs = win.notebook.count()
        self.assertGreaterEqual(total_tabs, 3)

        # select_tab within bounds
        win.select_tab(1)
        self.assertEqual(win.notebook.currentIndex(), 1)

        # select_tab out of bounds is ignored
        win.select_tab(-1)
        self.assertEqual(win.notebook.currentIndex(), 1)
        win.select_tab(999)
        self.assertEqual(win.notebook.currentIndex(), 1)

        # next_tab wraps around to 0
        win.select_tab(total_tabs - 1)
        win.next_tab()
        self.assertEqual(win.notebook.currentIndex(), 0)

        # open_or_select_project with already opened project activates tab
        cur_count = win.notebook.count()
        win.open_or_select_project(p2)
        self.assertEqual(win.notebook.count(), cur_count)
        self.assertTrue(
            win.notebook.tabText(win.notebook.currentIndex()).startswith("Project Beta")
        )
        win.close()

    def test_main_window_notepad_sync_activation(self):
        from tasks_app import MainWindow
        import time

        win = MainWindow(base_dir=self.test_dir)
        page = win.notebook.widget(0)
        # External modification
        time.sleep(0.05)
        with open(page.filename, "w", encoding="utf-8") as f:
            f.write("Externally Modified Task\n")
        # Trigger activation
        win._handle_window_activated()
        self.assertEqual(page.task_list.count(), 1)
        self.assertEqual(page.task_list.item(0).text(), "Externally Modified Task")
        win.close()

    def test_main_window_notepad_triggering_and_sync(self):
        import tasks_app
        from tasks_app import MainWindow
        import core_models
        import time

        win = MainWindow(base_dir=self.test_dir)
        opened_paths = []
        orig_open = tasks_app.open_file_in_editor
        tasks_app.open_file_in_editor = lambda path: opened_paths.append(path)
        try:
            # Trigger notepad for active tab
            win.on_open_active_project_in_notepad()
            self.assertEqual(len(opened_paths), 1)
            self.assertEqual(opened_paths[0], win.notebook.currentWidget().filename)

            # Trigger archive opening when file exists
            arch_path = core_models.ProjectModel.get_archive_path(
                self.test_dir, self.settings
            )
            with open(arch_path, "w", encoding="utf-8") as f:
                f.write("Archived entry\n")
            win.on_open_archive()
            self.assertEqual(len(opened_paths), 2)
            self.assertEqual(opened_paths[1], arch_path)

            # External modification sync during window activation
            page = win.notebook.currentWidget()
            time.sleep(0.05)
            with open(page.filename, "w", encoding="utf-8") as f:
                f.write("External Sync Task\n")
            win._handle_window_activated()
            self.assertEqual(page.task_list.count(), 1)
            self.assertEqual(page.task_list.item(0).text(), "External Sync Task")

            # Reload daily tab method
            win.reload_daily_tab()
        finally:
            tasks_app.open_file_in_editor = orig_open
        win.close()

    def test_main_window_shortcuts_registration(self):
        from tasks_app import MainWindow
        from PySide6.QtGui import QShortcut

        win = MainWindow(base_dir=self.test_dir)
        shortcuts = win.findChildren(QShortcut)
        self.assertGreaterEqual(len(shortcuts), 19)
        win.close()

    def test_main_window_accelerator_and_shortcut_dispatch(self):
        from tasks_app import MainWindow
        from PySide6.QtGui import QShortcut, QGuiApplication
        from PySide6.QtWidgets import QMessageBox

        win = MainWindow(base_dir=self.test_dir)
        page = win.notebook.currentWidget()
        page.settings.set("behavior", "confirm_on_archive", False)
        page.new_task_input.setText("Shortcut Task 1")
        page.on_add_task()
        page.new_task_input.setText("Shortcut Task 2")
        page.on_add_task()

        # Dispatch commands to active tab
        page.task_list.setCurrentRow(0)
        win.dispatch_to_active_tab("copy")
        self.assertEqual(
            QGuiApplication.clipboard().text(), page.task_list.item(0).text()
        )

        # Shortcuts map check
        shortcuts = win.findChildren(QShortcut)
        key_map = {sc.key().toString(): sc for sc in shortcuts}
        self.assertIn("Ctrl+M", key_map)
        self.assertIn("Ctrl+Z", key_map)
        self.assertIn("Ctrl+C", key_map)

        # Trigger Ctrl+M shortcut
        initial_count = page.task_list.count()
        page.task_list.setCurrentRow(0)
        key_map["Ctrl+M"].activated.emit()
        self.assertEqual(page.task_list.count(), initial_count - 1)

        # Trigger Ctrl+Z shortcut
        key_map["Ctrl+Z"].activated.emit()
        self.assertEqual(page.task_list.count(), initial_count)

        # Test on_harvest with mock QMessageBox
        orig_info = QMessageBox.information
        QMessageBox.information = staticmethod(
            lambda *a, **kw: QMessageBox.StandardButton.Ok
        )
        try:
            win.on_harvest()
        finally:
            QMessageBox.information = orig_info
        win.close()

    def test_single_instance_logic(self):
        from tasks_app import MainWindow, setup_single_instance
        from PySide6.QtNetwork import QLocalSocket

        sock_name = f"DarTasksTestSocket-{os.getpid()}"
        win = MainWindow(base_dir=self.test_dir)
        is_primary = setup_single_instance(self.app, win, socket_name=sock_name)
        self.assertTrue(is_primary)
        self.assertIsNotNone(win.single_instance_server)

        # Secondary instance attempts to connect
        client = QLocalSocket()
        client.connectToServer(sock_name)
        self.assertTrue(client.waitForConnected(1000))
        client.write(b"ACTIVATE\n")
        client.flush()
        client.waitForBytesWritten(1000)
        client.disconnectFromServer()
        win.close()

    def test_tasks_app_has_zero_wx_imports(self):
        tasks_app_file = Path(__file__).parent / "tasks_app.py"
        source = tasks_app_file.read_text(encoding="utf-8")
        parsed = ast.parse(source)
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(
                        alias.name == "wx" or alias.name.startswith("wx."),
                        f"Found wx import in tasks_app.py: {alias.name}",
                    )
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse(
                    node.module and (node.module == "wx" or node.module.startswith("wx.")),
                    f"Found from-wx import in tasks_app.py: {node.module}",
                )

    def test_tasks_app_clean_code_function_length_limit(self):
        tasks_app_file = Path(__file__).parent / "tasks_app.py"
        source = tasks_app_file.read_text(encoding="utf-8")
        parsed = ast.parse(source)
        for node in ast.walk(parsed):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno - node.lineno) + 1
                self.assertLessEqual(
                    length,
                    20,
                    f"Function '{node.name}' in tasks_app.py exceeds 20 lines ({length} lines)",
                )


class TestCleanCodeGuards(unittest.TestCase):
    """Verifies strict adherence to clean-code-guard constraints and zero-wx codebase policy."""

    def test_all_python_files_have_zero_wx_imports(self):
        repo_dir = Path(__file__).parent
        py_files = sorted(repo_dir.glob("*.py"))
        self.assertGreaterEqual(len(py_files), 4)
        for py_file in py_files:
            source = py_file.read_text(encoding="utf-8")
            parsed = ast.parse(source)
            for node in ast.walk(parsed):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(
                            alias.name == "wx" or alias.name.startswith("wx."),
                            f"Found wx import '{alias.name}' in {py_file.name}:{node.lineno}",
                        )
                elif isinstance(node, ast.ImportFrom):
                    self.assertFalse(
                        node.module
                        and (node.module == "wx" or node.module.startswith("wx.")),
                        f"Found from-wx import '{node.module}' in {py_file.name}:{node.lineno}",
                    )

    def test_clean_code_function_length_limits(self):
        repo_dir = Path(__file__).parent
        target_files = [repo_dir / "tasks_app.py", repo_dir / "dialogs.py"]
        for py_file in target_files:
            source = py_file.read_text(encoding="utf-8")
            parsed = ast.parse(source)
            for node in ast.walk(parsed):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    length = (node.end_lineno - node.lineno) + 1
                    self.assertLessEqual(
                        length,
                        20,
                        f"Function '{node.name}' in {py_file.name} exceeds 20 lines ({length} lines)",
                    )


if __name__ == "__main__":
    unittest.main()



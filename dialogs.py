"""
Dar Tasks - Presentation Dialog Adapters.
Contains modal dialogs for Project Management, Global Search, and Settings.
Connects accessible PySide6 user controls directly to deep domain module seams.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core_models import (
    ProjectWorkspace,
    SearchEngine,
    SettingsManager,
    Translator,
    get_project_display_name,
    open_file_in_editor,
)


class AccessibleActionListWidget(QListWidget):
    """List widget dispatching Enter/Return key to action callback."""

    def __init__(self, activate_action, parent=None):
        super().__init__(parent)
        self.activate_action = activate_action
        self.itemDoubleClicked.connect(lambda _: self.activate_action())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate_action()
            event.accept()
            return
        super().keyPressEvent(event)


class GlobalSearchDialog(QDialog):
    """Accessible UI adapter over the SearchEngine deep module."""

    def __init__(self, parent, search_engine: SearchEngine, settings=None):
        qt_parent = parent if isinstance(parent, QWidget) else None
        super().__init__(qt_parent)
        self.parent_window = parent
        self.search_engine = search_engine
        self.settings = settings
        self.translator = Translator(self.settings)
        self.last_results = []
        self._init_window()
        self._create_controls()
        self._setup_layout()
        self._setup_tab_order()

    def _init_window(self):
        title = self.translator._("Global Search")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.resize(600, 420)

    def _create_controls(self):
        self.search_label = QLabel(self.translator._("&Search:"), self)
        self.search_ctrl = QLineEdit(self)
        self.search_ctrl.setAccessibleName(self.translator._("Search Query"))
        self.search_label.setBuddy(self.search_ctrl)
        self.search_ctrl.textChanged.connect(self.on_search)
        self.search_ctrl.returnPressed.connect(self.on_search_return)

        self.results_label = QLabel(self.translator._("&Results:"), self)
        self.results_list = AccessibleActionListWidget(self.on_open_task, self)
        self.results_list.setAccessibleName(self.translator._("Search Results"))
        self.results_label.setBuddy(self.results_list)

        self.notepad_btn = QPushButton(self.translator._("&Notepad"), self)
        self.notepad_btn.setAccessibleName(self.translator._("Notepad"))
        self.notepad_btn.clicked.connect(self.on_notepad)

        self.close_btn = QPushButton(self.translator._("&Close"), self)
        self.close_btn.setAccessibleName(self.translator._("Close"))
        self.close_btn.clicked.connect(self.reject)

    def _setup_layout(self):
        layout = QVBoxLayout(self)
        search_layout = QHBoxLayout()
        search_layout.addWidget(self.search_label)
        search_layout.addWidget(self.search_ctrl)
        layout.addLayout(search_layout)
        layout.addWidget(self.results_label)
        layout.addWidget(self.results_list)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.notepad_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

    def _setup_tab_order(self):
        self.setTabOrder(self.search_ctrl, self.results_list)
        self.setTabOrder(self.results_list, self.notepad_btn)
        self.setTabOrder(self.notepad_btn, self.close_btn)

    def on_search_return(self):
        if self.last_results:
            self.results_list.setCurrentRow(0)
            self.results_list.setFocus()

    def on_search(self):
        query = self.search_ctrl.text().strip()
        self.results_list.clear()
        if len(query) < 2:
            self.last_results = []
            return
        sens = (
            self.settings.get("search", "case_sensitive", False)
            if self.settings
            else False
        )
        self.last_results = self.search_engine.search(query, case_sensitive=sens)
        for result in self.last_results:
            self.results_list.addItem(result["formatted"])

    def on_open_task(self):
        row = self.results_list.currentRow()
        if 0 <= row < len(self.last_results):
            filename = self.last_results[row]["filename"]
            if self.parent_window and hasattr(
                self.parent_window, "open_or_select_project"
            ):
                self.parent_window.open_or_select_project(filename)
            self.accept()

    def on_notepad(self):
        row = self.results_list.currentRow()
        if 0 <= row < len(self.last_results):
            filename = self.last_results[row]["filename"]
            project_path = self.search_engine.workspace.get_project_path(filename)
            open_file_in_editor(project_path)


class ProjectManagerDialog(QDialog):
    """Accessible UI adapter over the ProjectWorkspace deep module."""

    def __init__(self, parent, workspace: ProjectWorkspace):
        qt_parent = parent if isinstance(parent, QWidget) else None
        super().__init__(qt_parent)
        self.parent_window = parent
        self.workspace = workspace
        self.projects = []
        self.translator = getattr(parent, "translator", Translator())
        self._init_window()
        self._create_controls()
        self._setup_layout()
        self._setup_tab_order()
        self.refresh_list()

    def _init_window(self):
        title = self.translator._("Project Manager")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.resize(680, 440)

    def _create_controls(self):
        self.projects_label = QLabel(self.translator._("&Projects:"), self)
        self.project_list = AccessibleActionListWidget(self.on_open, self)
        self.project_list.setAccessibleName(self.translator._("Projects List"))
        self.projects_label.setBuddy(self.project_list)
        self._create_action_buttons()

    def _create_action_buttons(self):
        actions = [
            ("new_btn", self.translator._("&New"), self.on_new),
            ("open_btn", self.translator._("&Open"), self.on_open),
            ("pin_btn", self.translator._("&Pin ⭐"), self.on_pin),
            ("up_btn", self.translator._("Move &Up"), lambda: self.on_move(-1)),
            ("down_btn", self.translator._("Move &Down"), lambda: self.on_move(1)),
            ("rename_btn", self.translator._("&Rename"), self.on_rename),
            ("notepad_btn", self.translator._("Note&pad"), self.on_notepad),
            ("delete_btn", self.translator._("&Delete"), self.on_delete),
            ("close_btn", self.translator._("&Close"), self.reject),
        ]
        self.buttons = []
        for attr_name, label, slot in actions:
            btn = QPushButton(label, self)
            # Drop the star so screen readers do not announce the emoji.
            btn.setAccessibleName(label.replace("&", "").replace("⭐", "").strip())
            btn.clicked.connect(slot)
            setattr(self, attr_name, btn)
            self.buttons.append(btn)

    def _setup_layout(self):
        layout = QVBoxLayout(self)
        layout.addWidget(self.projects_label)
        layout.addWidget(self.project_list)
        btn_layout = QHBoxLayout()
        for btn in self.buttons:
            btn_layout.addWidget(btn)
        layout.addLayout(btn_layout)

    def _setup_tab_order(self):
        previous_widget = self.project_list
        for btn in self.buttons:
            self.setTabOrder(previous_widget, btn)
            previous_widget = btn

    def refresh_list(self):
        self.projects = self.workspace.list_projects()
        suffix = self.translator._(" (daily)")
        self.project_list.clear()
        for proj in self.projects:
            star = "⭐ " if proj["pinned"] else ""
            display = get_project_display_name(proj["filename"], self.translator)
            daily = suffix if proj["readonly"] else ""
            self.project_list.addItem(f"{star}{display}{daily}")

    def on_new(self):
        name, accepted = QInputDialog.getText(
            self,
            self.translator._("New Project"),
            self.translator._("New project name:"),
        )
        if accepted and name.strip():
            if self.workspace.create_project(name.strip()):
                self.refresh_list()

    def on_open(self):
        row = self.project_list.currentRow()
        if 0 <= row < len(self.projects):
            filename = self.projects[row]["filename"]
            if self.parent_window and hasattr(
                self.parent_window, "open_or_select_project"
            ):
                self.parent_window.open_or_select_project(filename)
            self.accept()

    def on_notepad(self):
        row = self.project_list.currentRow()
        if 0 <= row < len(self.projects):
            open_file_in_editor(Path(self.projects[row]["path"]))

    def on_pin(self):
        row = self.project_list.currentRow()
        if 0 <= row < len(self.projects) and not self.projects[row]["readonly"]:
            self.workspace.toggle_pin(self.projects[row]["filename"])
            self.refresh_list()
            self.project_list.setCurrentRow(row)

    def on_move(self, direction: int):
        row = self.project_list.currentRow()
        if 0 <= row < len(self.projects):
            if self.workspace.move_project(row, direction):
                self.refresh_list()
                self.project_list.setCurrentRow(row + direction)

    def on_rename(self):
        row = self.project_list.currentRow()
        if row < 0 or row >= len(self.projects) or self.projects[row]["readonly"]:
            return
        proj = self.projects[row]
        display = get_project_display_name(proj["filename"], self.translator)
        new_name, accepted = QInputDialog.getText(
            self,
            self.translator._("Rename Project"),
            self.translator._("New project name:"),
            text=display,
        )
        if accepted and new_name.strip():
            self._apply_rename(proj["filename"], new_name.strip())

    def _apply_rename(self, old_filename: str, new_name: str):
        renamed = self.workspace.rename_project(old_filename, new_name)
        if renamed:
            if self.parent_window and hasattr(self.parent_window, "on_project_renamed"):
                self.parent_window.on_project_renamed(old_filename, renamed)
            self.refresh_list()

    def on_delete(self):
        row = self.project_list.currentRow()
        if row < 0 or row >= len(self.projects) or self.projects[row]["readonly"]:
            return
        proj = self.projects[row]
        display = get_project_display_name(proj["filename"], self.translator)
        prompt = self.translator._("Delete project '{display}'?").format(
            display=display
        )
        reply = QMessageBox.question(
            self,
            self.translator._("Confirm Delete"),
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._apply_delete(proj["filename"])

    def _apply_delete(self, filename: str):
        if self.workspace.delete_project(filename):
            if self.parent_window and hasattr(self.parent_window, "on_project_deleted"):
                self.parent_window.on_project_deleted(filename)
            self.refresh_list()


class SettingsDialog(QDialog):
    """Accessible dialog for managing application settings."""

    def __init__(self, parent, settings_manager: SettingsManager):
        qt_parent = parent if isinstance(parent, QWidget) else None
        super().__init__(qt_parent)
        self.parent_window = parent
        self.settings = settings_manager
        self.translator = Translator(self.settings)
        self._init_window()
        self._create_controls()
        self._setup_layout()
        self._setup_tab_order()

    def _init_window(self):
        title = self.translator._("Settings")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.resize(480, 360)

    def _create_controls(self):
        self._create_checkboxes()
        self._create_spin_box()
        self._create_lang_combo()
        self._create_buttons()

    def _create_checkboxes(self):
        self.confirm_archive_cb = QCheckBox(
            self.translator._("Confirm on &archive"), self
        )
        self.confirm_archive_cb.setAccessibleName(
            self.translator._("Confirm on archive")
        )
        archive_val = self.settings.get("behavior", "confirm_on_archive", False)
        self.confirm_archive_cb.setChecked(archive_val)

        self.confirm_delete_cb = QCheckBox(
            self.translator._("Confirm on &delete"), self
        )
        self.confirm_delete_cb.setAccessibleName(self.translator._("Confirm on delete"))
        delete_val = self.settings.get("behavior", "confirm_on_delete", True)
        self.confirm_delete_cb.setChecked(delete_val)

    def _create_spin_box(self):
        self.spin_label = QLabel(
            self.translator._("Auto-save interval (&seconds):"), self
        )
        self.auto_save_spin = QSpinBox(self)
        self.auto_save_spin.setRange(1, 60)
        auto_save_val = self.settings.get("behavior", "auto_save_interval", 2)
        self.auto_save_spin.setValue(auto_save_val)
        self.auto_save_spin.setAccessibleName(
            self.translator._("Auto-save interval (seconds)")
        )
        self.spin_label.setBuddy(self.auto_save_spin)

    def _create_lang_combo(self):
        self.lang_label = QLabel(self.translator._("&Language:"), self)
        self.lang_choice = QComboBox(self)
        self.lang_choice.addItems(["English", "العربية"])
        selected_lang = (
            1 if self.settings.get("general", "language", "en") == "ar" else 0
        )
        self.lang_choice.setCurrentIndex(selected_lang)
        self.lang_choice.setAccessibleName(self.translator._("Language"))
        self.lang_label.setBuddy(self.lang_choice)

    def _create_buttons(self):
        self.reset_btn = QPushButton(self.translator._("&Reset Defaults"), self)
        self.reset_btn.setAccessibleName(self.translator._("Reset Defaults"))
        self.reset_btn.clicked.connect(self.on_reset)

        self.save_btn = QPushButton(self.translator._("&Save"), self)
        self.save_btn.setAccessibleName(self.translator._("Save"))
        self.save_btn.clicked.connect(self.on_save)

        self.cancel_btn = QPushButton(self.translator._("&Cancel"), self)
        self.cancel_btn.setAccessibleName(self.translator._("Cancel"))
        self.cancel_btn.clicked.connect(self.reject)

    def _setup_layout(self):
        layout = QVBoxLayout(self)
        layout.addWidget(self.confirm_archive_cb)
        layout.addWidget(self.confirm_delete_cb)
        self._add_field_rows(layout)
        layout.addStretch()
        self._add_button_row(layout)

    def _add_field_rows(self, layout):
        spin_layout = QHBoxLayout()
        spin_layout.addWidget(self.spin_label)
        spin_layout.addWidget(self.auto_save_spin)
        spin_layout.addStretch()
        layout.addLayout(spin_layout)
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(self.lang_label)
        lang_layout.addWidget(self.lang_choice)
        layout.addLayout(lang_layout)

    def _add_button_row(self, layout):
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.reset_btn)
        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

    def _setup_tab_order(self):
        self.setTabOrder(self.confirm_archive_cb, self.confirm_delete_cb)
        self.setTabOrder(self.confirm_delete_cb, self.auto_save_spin)
        self.setTabOrder(self.auto_save_spin, self.lang_choice)
        self.setTabOrder(self.lang_choice, self.reset_btn)
        self.setTabOrder(self.reset_btn, self.save_btn)
        self.setTabOrder(self.save_btn, self.cancel_btn)

    def on_save(self):
        self.settings.set(
            "behavior", "confirm_on_archive", self.confirm_archive_cb.isChecked()
        )
        self.settings.set(
            "behavior", "confirm_on_delete", self.confirm_delete_cb.isChecked()
        )
        self.settings.set("behavior", "auto_save_interval", self.auto_save_spin.value())
        lang = "ar" if self.lang_choice.currentIndex() == 1 else "en"
        self.settings.set("general", "language", lang)
        self.settings.save()
        self.accept()

    def on_reset(self):
        prompt = self.translator._("Reset settings to default?")
        reply = QMessageBox.question(
            self,
            self.translator._("Confirm Reset"),
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._apply_reset()

    def _apply_reset(self):
        self.settings.reset_to_defaults()
        archive_val = self.settings.get("behavior", "confirm_on_archive", False)
        self.confirm_archive_cb.setChecked(archive_val)
        delete_val = self.settings.get("behavior", "confirm_on_delete", True)
        self.confirm_delete_cb.setChecked(delete_val)
        self.auto_save_spin.setValue(
            self.settings.get("behavior", "auto_save_interval", 2)
        )
        selected_lang = (
            1 if self.settings.get("general", "language", "en") == "ar" else 0
        )
        self.lang_choice.setCurrentIndex(selected_lang)

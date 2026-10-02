"""
Dar Tasks - Accessible Task and Project Management Application.
Streamlined presentation adapter layer utilizing PySide6 native controls.
Delegates all domain persistence and invariants to core_models and dialogs.
"""

import getpass
from pathlib import Path
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core_models import (
    DAILY_FILENAME,
    DAILY_TEMPLATE_FILENAME,
    BackupManager,
    ProjectModel,
    ProjectWorkspace,
    SearchEngine,
    SettingsManager,
    Translator,
    get_base_dir,
    get_project_display_name,
    open_file_in_editor,
)
from dialogs import GlobalSearchDialog, ProjectManagerDialog, SettingsDialog


def copy_to_clipboard(text: str) -> bool:
    """Copy text to clipboard using PySide6 clipboard."""
    clipboard = QGuiApplication.clipboard()
    if clipboard:
        clipboard.setText(text)
        return True
    return False


class ButtonEnterKeyFilter(QObject):
    """Event filter allowing Return/Enter key to trigger focused QPushButton."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            if isinstance(obj, QPushButton):
                # Swallow auto-repeat so holding Enter fires the button once.
                if not event.isAutoRepeat():
                    obj.click()
                return True
        return super().eventFilter(obj, event)


class AccessibleTaskListWidget(QListWidget):
    """Accessible list widget handling Return/Enter keys for task actions."""

    def __init__(self, on_activate, parent=None):
        super().__init__(parent)
        self.on_activate = on_activate
        self.itemDoubleClicked.connect(lambda _: self.on_activate())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.on_activate()
            event.accept()
            return
        super().keyPressEvent(event)


class TaskProjectWidget(QWidget):
    """Widget representing a single project tab with native accessible controls."""

    def __init__(
        self,
        parent=None,
        filename: str | Path = "",
        settings=None,
        base_dir: Path | None = None,
    ):
        super().__init__(parent)
        self.filename = Path(filename)
        self.settings = settings
        self.base_dir = Path(base_dir) if base_dir else get_base_dir()
        self.translator = Translator(self.settings)
        self.model = ProjectModel(self.filename, self.settings, self.base_dir)
        self.visible_indices: list[int] = []
        self._init_ui()
        if self.model.load_tasks():
            self.update_display()
        self._setup_auto_save()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        self._setup_filter_row(layout)
        self._setup_task_list(layout)
        self._setup_add_row(layout)
        QWidget.setTabOrder(self.search_input, self.task_list)
        QWidget.setTabOrder(self.task_list, self.new_task_input)
        self.retranslate_ui()

    def _setup_filter_row(self, parent_layout: QVBoxLayout):
        row = QHBoxLayout()
        self.filter_lbl = QLabel(self)
        self.search_input = QLineEdit(self)
        self.filter_lbl.setBuddy(self.search_input)
        self.search_input.textChanged.connect(self.update_display)
        row.addWidget(self.filter_lbl)
        row.addWidget(self.search_input)
        parent_layout.addLayout(row)

    def _setup_task_list(self, parent_layout: QVBoxLayout):
        self.tasks_lbl = QLabel(self)
        self.task_list = AccessibleTaskListWidget(
            lambda: self.dispatch_command("archive"), self
        )
        self.tasks_lbl.setBuddy(self.task_list)
        self.task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.task_list.customContextMenuRequested.connect(self.on_context_menu)
        parent_layout.addWidget(self.tasks_lbl)
        parent_layout.addWidget(self.task_list, 1)

    def _setup_add_row(self, parent_layout: QVBoxLayout):
        row = QHBoxLayout()
        self.add_lbl = QLabel(self)
        self.new_task_input = QLineEdit(self)
        self.add_lbl.setBuddy(self.new_task_input)
        self.new_task_input.returnPressed.connect(self.on_add_task)
        row.addWidget(self.add_lbl)
        row.addWidget(self.new_task_input)
        parent_layout.addLayout(row)

    def retranslate_ui(self):
        self.translator.setup_translations()
        _ = self.translator._
        self.filter_lbl.setText(f"&{_('filter')}")
        self.search_input.setAccessibleName(_("Filter"))
        self.search_input.setAccessibleDescription(_("Filter tasks in current project"))
        self.tasks_lbl.setText(f"&{_('tasks')}")
        self.task_list.setAccessibleName(_("Tasks"))
        self.task_list.setAccessibleDescription(_("Project tasks list"))
        self.add_lbl.setText(f"&{_('add')}")
        self.new_task_input.setAccessibleName(_("Add Task"))
        self.new_task_input.setAccessibleDescription(_("Enter new task title"))

    def _setup_auto_save(self):
        sec = (
            self.settings.get("behavior", "auto_save_interval", 2)
            if self.settings
            else 2
        )
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_auto_save_tick)
        self.timer.start(max(1, sec) * 1000)

    def _on_auto_save_tick(self):
        if self.model.has_unsaved_changes:
            self.model.save_tasks_atomic()

    def on_add_task(self):
        title = self.new_task_input.text().strip()
        if self.model.add_task(title):
            self.new_task_input.clear()
            self.update_display(self.search_input.text())

    def update_display(self, filter_text: str = ""):
        self.task_list.clear()
        entries = self.model.get_filtered_task_entries(filter_text)
        self.visible_indices = [idx for idx, _ in entries]
        for _, text in entries:
            self.task_list.addItem(text)

    def _confirm(self, msg_key: str, setting_key: str, default_val: bool) -> bool:
        if self.settings and not self.settings.get(
            "behavior", setting_key, default_val
        ):
            return True
        btn = QMessageBox.question(
            self,
            self.translator._("settings"),
            self.translator._(msg_key),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return btn == QMessageBox.StandardButton.Yes

    def _adjust_selection_after_removal(self, sel: int):
        new_sel = min(sel, self.task_list.count() - 1)
        if new_sel >= 0:
            self.task_list.setCurrentRow(new_sel)

    def _handle_archive_command(self, m_idx: int, sel: int):
        if self._confirm("confirm_archive", "confirm_on_archive", False):
            if self.model.archive_task(m_idx):
                self.update_display(self.search_input.text())
                self._adjust_selection_after_removal(sel)

    def _handle_delete_command(self, m_idx: int, sel: int):
        if self._confirm("confirm_delete", "confirm_on_delete", True):
            if self.model.delete_task(m_idx):
                self.update_display(self.search_input.text())
                self._adjust_selection_after_removal(sel)

    def _handle_edit_command(self, m_idx: int, sel: int, task_text: str):
        prompt = self.translator._("edit_task")
        text, ok = QInputDialog.getText(self, prompt, prompt, text=task_text)
        if ok and text.strip() and self.model.edit_task(m_idx, text.strip()):
            self.update_display(self.search_input.text())
            self.task_list.setCurrentRow(sel)

    def _handle_pin_command(self, m_idx: int, sel: int):
        task = self.model.tasks[m_idx]
        if self.model.toggle_pin(m_idx):
            self.update_display(self.search_input.text())
            self._reselect_pinned_task(task, sel)

    def _reselect_pinned_task(self, task, fallback_sel: int):
        try:
            new_idx = self.model.tasks.index(task)
            if new_idx in self.visible_indices:
                self.task_list.setCurrentRow(self.visible_indices.index(new_idx))
                return
        except (ValueError, IndexError):
            pass
        self.task_list.setCurrentRow(fallback_sel)

    def _handle_move_command(self, m_idx: int, sel: int, direction: int):
        if 0 <= sel + direction < len(self.visible_indices):
            if self.model.move_task(m_idx, direction):
                self.update_display(self.search_input.text())
                tgt = m_idx + direction
                if tgt in self.visible_indices:
                    self.task_list.setCurrentRow(self.visible_indices.index(tgt))

    def _handle_undo_command(self):
        if self.model.undo():
            self.update_display(self.search_input.text())

    def dispatch_command(self, cmd: str):
        if cmd == "add":
            return self.on_add_task()
        if cmd == "undo":
            return self._handle_undo_command()
        if cmd == "open_notepad":
            return open_file_in_editor(self.filename)
        sel = self.task_list.currentRow()
        if sel < 0 or sel >= len(self.visible_indices):
            return
        m_idx, text = self.visible_indices[sel], self.task_list.item(sel).text()
        self._dispatch_selected_command(cmd, m_idx, sel, text)
        self.task_list.setFocus()

    def _dispatch_selected_command(self, cmd: str, m_idx: int, sel: int, text: str):
        actions = {
            "archive": lambda: self._handle_archive_command(m_idx, sel),
            "complete": lambda: self._handle_archive_command(m_idx, sel),
            "delete": lambda: self._handle_delete_command(m_idx, sel),
            "edit": lambda: self._handle_edit_command(m_idx, sel, text),
            "copy": lambda: copy_to_clipboard(text),
            "pin": lambda: self._handle_pin_command(m_idx, sel),
            "move_up": lambda: self._handle_move_command(m_idx, sel, -1),
            "move_down": lambda: self._handle_move_command(m_idx, sel, 1),
        }
        handler = actions.get(cmd)
        if handler:
            handler()

    def on_context_menu(self, pos: QPoint):
        item = self.task_list.itemAt(pos)
        if item:
            self.task_list.setCurrentItem(item)
        if self.task_list.currentRow() < 0:
            return
        menu = QMenu(self)
        self._populate_context_menu(menu)
        menu.exec(self.task_list.mapToGlobal(pos))

    def _populate_context_menu(self, menu: QMenu):
        items = [
            (self.translator._("Mark Complete (Ctrl+M)"), "complete"),
            (self.translator._("Pin ⭐"), "pin"),
            (self.translator._("Edit (F2)"), "edit"),
            (self.translator._("Copy (Ctrl+C)"), "copy"),
            (self.translator._("Delete (Del)"), "delete"),
            (self.translator._("Open in Notepad (F4)"), "open_notepad"),
        ]
        for label, cmd in items:
            action = menu.addAction(label)
            action.triggered.connect(lambda _, c=cmd: self.dispatch_command(c))


class MainWindow(QMainWindow):
    """Main window coordinating project tabs, shortcuts, and global toolbar."""

    def __init__(self, parent=None, base_dir=None):
        super().__init__(parent)
        self.setWindowTitle("Dar Tasks")
        self.resize(800, 600)
        self.single_instance_server = None
        self._btn_filter = ButtonEnterKeyFilter(self)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self._btn_filter)
        self._init_workspace(base_dir)
        self._apply_layout_direction()
        self._init_ui()
        self._setup_shortcuts()
        self.load_all_projects()

    def _init_workspace(self, base_dir=None):
        self.base_dir = Path(base_dir) if base_dir else get_base_dir()
        self.projects_dir = self.base_dir / "projects"
        self.backups_dir = self.base_dir / "backups"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.workspace = ProjectWorkspace(self.projects_dir)
        self.search_engine = SearchEngine(self.workspace)
        self.settings = SettingsManager(self.base_dir)
        self.translator = Translator(self.settings)
        self.backup_mgr = BackupManager(
            self.base_dir, self.projects_dir, self.backups_dir, self.settings
        )
        self.backup_mgr.setup_daily_tasks()

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(5, 5, 5, 5)
        self._setup_toolbar(layout)
        self.notebook = QTabWidget(central_widget)
        self.notebook.setAccessibleName(self.translator._("Projects Tabs"))
        self.notebook.setAccessibleDescription(
            self.translator._("Project notebook tabs")
        )
        layout.addWidget(self.notebook, 1)

    def _setup_toolbar(self, parent_layout: QVBoxLayout):
        toolbar = QHBoxLayout()
        self.toolbar_buttons = []
        for name, mnem, _, handler in self._get_toolbar_actions():
            btn = QPushButton(self)
            btn.setShortcut(QKeySequence(f"Alt+{mnem}"))
            btn.clicked.connect(handler)
            self.toolbar_buttons.append((btn, name, mnem))
            toolbar.addWidget(btn)
        parent_layout.addLayout(toolbar)
        self._update_toolbar_translations()

    def _update_toolbar_translations(self):
        actions = self._get_toolbar_actions()
        for (btn, _, mnem), (_, _, label, _) in zip(self.toolbar_buttons, actions):
            clean = label.replace("&", "")
            btn.setText(label)
            btn.setToolTip(f"{clean} (Alt+{mnem})")
            btn.setAccessibleName(clean)
            btn.setAccessibleDescription("")

    def _apply_layout_direction(self):
        app = QApplication.instance()
        if app:
            is_ar = self.translator.lang == "ar"
            direction = (
                Qt.LayoutDirection.RightToLeft
                if is_ar
                else Qt.LayoutDirection.LeftToRight
            )
            app.setLayoutDirection(direction)

    def _retranslate_tabs(self):
        for i in range(self.notebook.count()):
            page = self.notebook.widget(i)
            if isinstance(page, TaskProjectWidget):
                title = get_project_display_name(page.filename.name, self.translator)
                self.notebook.setTabText(i, title)
                page.retranslate_ui()

    def retranslate_ui(self):
        self.translator.setup_translations()
        self._apply_layout_direction()
        self._update_toolbar_translations()
        self._retranslate_tabs()
        _ = self.translator._
        self.notebook.setAccessibleName(_("Projects Tabs"))
        self.notebook.setAccessibleDescription(_("Project notebook tabs"))

    def _get_toolbar_actions(self):
        return self._get_primary_actions() + self._get_secondary_actions()

    def _get_primary_actions(self):
        p_mgr = lambda: self._show_dialog(ProjectManagerDialog, self.workspace)
        g_sch = lambda: self._show_dialog(
            GlobalSearchDialog, self.search_engine, self.settings
        )
        return [
            ("Project Manager", "P", f"&{self.translator._('proj_mgr')}", p_mgr),
            ("Global Search", "S", f"&{self.translator._('search')}", g_sch),
            (
                "Open in Notepad",
                "N",
                f"&{self.translator._('open_notepad')}",
                self.on_open_active_project_in_notepad,
            ),
        ]

    def _get_secondary_actions(self):
        is_en = self.translator.lang == "en"
        sett_lbl = "Se&ttings" if is_en else f"&{self.translator._('settings')}"
        return [
            (
                "Today's Harvest",
                "H",
                f"&{self.translator._('harvest')}",
                self.on_harvest,
            ),
            (
                "Open Archive",
                "O",
                f"&{self.translator._('open_arch')}",
                self.on_open_archive,
            ),
            ("Settings", "T", sett_lbl, self.on_settings),
        ]

    def _show_dialog(self, dlg_cls, *args):
        dlg = dlg_cls(self, *args)
        return dlg.exec()

    def load_all_projects(self):
        for proj in self.workspace.list_projects():
            if proj["filename"] != DAILY_TEMPLATE_FILENAME:
                page = TaskProjectWidget(
                    self.notebook, proj["path"], self.settings, self.base_dir
                )
                title = get_project_display_name(proj["filename"], self.translator)
                self.notebook.addTab(page, title)

    def open_or_select_project(self, filename: str):
        target = self.workspace.get_project_path(filename).resolve()
        for i in range(self.notebook.count()):
            page = self.notebook.widget(i)
            if (
                isinstance(page, TaskProjectWidget)
                and page.filename.resolve() == target
            ):
                self.notebook.setCurrentIndex(i)
                return
        self._add_new_project_tab(filename)

    def _add_new_project_tab(self, filename: str):
        path = self.workspace.get_project_path(filename)
        if path.exists():
            page = TaskProjectWidget(self.notebook, path, self.settings, self.base_dir)
            title = get_project_display_name(filename, self.translator)
            self.notebook.addTab(page, title)
            self.notebook.setCurrentIndex(self.notebook.count() - 1)

    def on_project_renamed(self, old_fn: str, new_fn: str):
        old_p = self.workspace.get_project_path(old_fn).resolve()
        new_p = self.workspace.get_project_path(new_fn)
        for i in range(self.notebook.count()):
            page = self.notebook.widget(i)
            if isinstance(page, TaskProjectWidget) and page.filename.resolve() == old_p:
                page.filename = new_p
                page.model.filename = new_p
                title = get_project_display_name(new_fn, self.translator)
                self.notebook.setTabText(i, title)
                break

    def on_project_deleted(self, filename: str):
        p_path = self.workspace.get_project_path(filename).resolve()
        for i in range(self.notebook.count()):
            page = self.notebook.widget(i)
            if (
                isinstance(page, TaskProjectWidget)
                and page.filename.resolve() == p_path
            ):
                page.model.has_unsaved_changes = False
                page.timer.stop()
                self.notebook.removeTab(i)
                page.deleteLater()
                break

    def select_tab(self, index: int):
        if 0 <= index < self.notebook.count():
            self.notebook.setCurrentIndex(index)
            page = self.notebook.currentWidget()
            if isinstance(page, TaskProjectWidget):
                page.task_list.setFocus()

    def next_tab(self):
        if self.notebook.count() > 0:
            self.select_tab((self.notebook.currentIndex() + 1) % self.notebook.count())

    def dispatch_to_active_tab(self, cmd: str):
        page = self.notebook.currentWidget()
        if isinstance(page, TaskProjectWidget):
            page.dispatch_command(cmd)

    def _setup_shortcuts(self):
        self._setup_command_shortcuts()
        self._setup_navigation_shortcuts()
        self._setup_numeric_tab_shortcuts()

    def _setup_command_shortcuts(self):
        mappings = [
            ("Ctrl+Up", lambda: self.dispatch_to_active_tab("move_up")),
            ("Ctrl+Down", lambda: self.dispatch_to_active_tab("move_down")),
            ("Delete", lambda: self.dispatch_to_active_tab("delete")),
            ("F2", lambda: self.dispatch_to_active_tab("edit")),
            ("Ctrl+Z", lambda: self.dispatch_to_active_tab("undo")),
            ("Ctrl+M", lambda: self.dispatch_to_active_tab("complete")),
            ("Ctrl+C", lambda: self.dispatch_to_active_tab("copy")),
        ]
        for seq, slot in mappings:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

    def _setup_navigation_shortcuts(self):
        mappings = [
            (
                "Ctrl+F",
                lambda: self._show_dialog(
                    GlobalSearchDialog, self.search_engine, self.settings
                ),
            ),
            ("F4", self.on_open_active_project_in_notepad),
            ("Ctrl+Tab", self.next_tab),
        ]
        for seq, slot in mappings:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

    def _setup_numeric_tab_shortcuts(self):
        for i in range(1, 10):
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda idx=i - 1: self.select_tab(idx))

    def on_open_active_project_in_notepad(self):
        page = self.notebook.currentWidget()
        if isinstance(page, TaskProjectWidget):
            open_file_in_editor(page.filename)

    def on_harvest(self):
        done = ProjectModel.get_today_harvest(self.base_dir, self.settings)
        msg = "\n".join(done) if done else self.translator._("no_harvest")
        QMessageBox.information(self, self.translator._("arch_title"), msg)

    def on_open_archive(self):
        arch = ProjectModel.get_archive_path(self.base_dir, self.settings)
        if arch.exists():
            open_file_in_editor(arch)
        else:
            QMessageBox.information(self, "Info", self.translator._("no_arch"))

    def on_settings(self):
        if (
            self._show_dialog(SettingsDialog, self.settings)
            == QDialog.DialogCode.Accepted
        ):
            self.retranslate_ui()

    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._handle_window_activated()
        super().changeEvent(event)

    def _handle_window_activated(self):
        if self.backup_mgr.check_and_rollover_daily_tasks():
            self.reload_daily_tab()
        for i in range(self.notebook.count()):
            page = self.notebook.widget(i)
            if isinstance(page, TaskProjectWidget) and page.model.reload_if_modified():
                page.update_display(page.search_input.text())

    def reload_daily_tab(self):
        for i in range(self.notebook.count()):
            page = self.notebook.widget(i)
            if (
                isinstance(page, TaskProjectWidget)
                and page.filename.name == DAILY_FILENAME
            ):
                page.model.load_tasks()
                page.update_display(page.search_input.text())

    def closeEvent(self, event: QCloseEvent):
        for i in range(self.notebook.count()):
            page = self.notebook.widget(i)
            if isinstance(page, TaskProjectWidget) and page.model.has_unsaved_changes:
                page.model.save_tasks_atomic()
        if self.single_instance_server:
            self.single_instance_server.close()
        event.accept()


def setup_single_instance(app, main_window, socket_name: str | None = None) -> bool:
    """Setup single instance via QLocalServer / QLocalSocket."""
    name = socket_name or f"DarTasksSingleInstance-{getpass.getuser()}"
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(500):
        socket.write(b"ACTIVATE\n")
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return False
    return _start_single_instance_server(main_window, name)


def _start_single_instance_server(main_window, socket_name: str) -> bool:
    QLocalServer.removeServer(socket_name)
    server = QLocalServer(main_window)
    if not server.listen(socket_name):
        return True
    server.newConnection.connect(
        lambda: _handle_instance_connection(server, main_window)
    )
    main_window.single_instance_server = server
    return True


def _handle_instance_connection(server: QLocalServer, main_window):
    socket = server.nextPendingConnection()
    if not socket:
        return
    if socket.waitForReadyRead(500) and b"ACTIVATE" in socket.readAll().data():
        _bring_window_to_front(main_window)
    socket.close()


def _bring_window_to_front(win):
    win.setWindowState(
        win.windowState() & ~Qt.WindowState.WindowMinimized
        | Qt.WindowState.WindowActive
    )
    win.showNormal()
    win.raise_()
    win.activateWindow()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    main_window = MainWindow()
    if not setup_single_instance(app, main_window):
        sys.exit(0)
    main_window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""Textual-интерфейс для личного кабинета ЛГТУ."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from rich.table import Table
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Center, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, RichLog, Select, Static

from Lstu_parser import LstuParser

ACCOUNTS_FILE = Path(__file__).with_name(".lstu_accounts.json")


def load_accounts() -> list[str]:
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, str) and item]
    except (OSError, json.JSONDecodeError):
        return []


def save_account(login: str) -> None:
    accounts = load_accounts()
    if login not in accounts:
        ACCOUNTS_FILE.write_text(json.dumps([*accounts, login], ensure_ascii=False, indent=2), encoding="utf-8")


class LoginScreen(Screen[None]):
    CSS = """
    LoginScreen { align: center middle; }
    #login-box { width: 60; height: auto; border: round $primary; padding: 1 2; }
    #login-error { color: $error; height: auto; margin-top: 1; }
    Button { width: 1fr; margin-top: 1; }
    """
    BINDINGS = [("tab", "next_login_field", "Следующее поле"), ("shift+tab", "previous_login_field", "Предыдущее поле")]

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="login-box"):
                yield Label("Вход в личный кабинет ЛГТУ")
                yield Select([(account, account) for account in load_accounts()], prompt="Сохранённый аккаунт", id="account-select")
                yield Input(placeholder="Логин", id="login")
                yield Input(placeholder="Пароль", password=True, id="password")
                yield Button("Войти", variant="primary", id="submit-login")
                yield Static("", id="login-error")

    def on_mount(self) -> None:
        self.query_one("#account-select", Select).focus()

    def _focus_login_field(self, step: int) -> None:
        fields = [self.query_one("#account-select", Select), self.query_one("#login", Input), self.query_one("#password", Input)]
        try:
            current = fields.index(self.focused)
        except ValueError:
            current = -1 if step > 0 else 0
        fields[(current + step) % len(fields)].focus()

    def action_next_login_field(self) -> None:
        self._focus_login_field(1)

    def action_previous_login_field(self) -> None:
        self._focus_login_field(-1)

    @on(Select.Changed, "#account-select")
    def choose_account(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self.query_one("#login", Input).value = str(event.value)

    @on(Button.Pressed, "#submit-login")
    @on(Input.Submitted, "#password")
    def submit(self) -> None:
        login = self.query_one("#login", Input).value.strip()
        password = self.query_one("#password", Input).value
        if not login or not password:
            self.show_error("Введите логин и пароль.")
            return
        self.run_worker(self.login(login, password), exclusive=True)

    async def login(self, login: str, password: str) -> None:
        self.show_error("Выполняется вход…")
        parser = LstuParser()
        try:
            authenticated = await asyncio.to_thread(parser.login, login, password)
        except Exception as error:
            self.show_error(f"Ошибка подключения: {error}")
            return
        if not authenticated:
            self.show_error("Не удалось войти: проверьте логин и пароль.")
            return
        save_account(login)
        self.app.start_session(parser, login)
        self.app.pop_screen()

    def show_error(self, text: str) -> None:
        self.query_one("#login-error", Static).update(text)


class SemesterScreen(Screen[None]):
    CSS = """
    SemesterScreen { align: center middle; }
    #semester-box { width: 70; height: auto; border: round $primary; padding: 1 2; }
    """

    def __init__(self, semesters: list[tuple[str, str]]) -> None:
        super().__init__()
        self.semesters = semesters

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="semester-box"):
                yield Label("Выберите семестр (стрелки + Enter)")
                yield Select(self.semesters, prompt="Семестр", id="semester-select")

    def on_mount(self) -> None:
        self.query_one("#semester-select", Select).focus()

    @on(Select.Changed, "#semester-select")
    def semester_chosen(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self.app.open_semester(str(event.value))
            self.app.pop_screen()


class SubjectScreen(Screen[None]):
    BINDINGS = [("escape", "back", "Назад")]

    CSS = """
    SubjectScreen { align: center middle; }
    #subject-box { width: 70; height: auto; border: round $primary; padding: 1 2; }
    """

    def __init__(self, subjects: list[tuple[str, str]]) -> None:
        super().__init__()
        self.subjects = subjects

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="subject-box"):
                yield Label("Выберите предмет (стрелки + Enter)")
                yield Select(self.subjects, prompt="Предмет", id="subject-select")

    def on_mount(self) -> None:
        self.query_one("#subject-select", Select).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Select.Changed, "#subject-select")
    def subject_chosen(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self.app.open_subject(str(event.value))
            self.app.pop_screen()


class SubjectDetailsScreen(Screen[None]):
    """Отдельный экран предмета: файлы, загрузка и сообщения."""

    CSS = """
    #subject-files, #subject-upload, #subject-messages {
        height: 1fr; border: round $primary; margin: 1 2 0 2; padding: 1;
    }
    """
    BINDINGS = [
        ("escape", "back", "Назад"),
        ("d", "download", "Скачать файл"),
        ("u", "upload", "Прикрепить файл"),
        ("x", "delete_file", "Удалить файл"),
    ]

    def __init__(self, data: dict[str, object]) -> None:
        super().__init__()
        self.data = data

    @staticmethod
    def format_item(kind: str, item: object) -> str:
        if not isinstance(item, dict):
            return f"• {item}"
        if kind == "files":
            return f"• {item.get('name', 'Без названия')}\n  [dim]{item.get('url', '')}[/]"
        if kind == "uploads":
            result = f"• {item.get('work', 'Работа')} — {item.get('topic', '')}\n  {item.get('teacher', '')}; оценка: {item.get('grade', '—')}"
            attachments = item.get("attachments", [])
            if attachments:
                result += "\n  Прикреплено: " + ", ".join(str(file.get("name", "")) for file in attachments if isinstance(file, dict))
            elif item.get("can_upload"):
                result += "\n  [green]Можно прикрепить файл[/]"
            return result
        if kind == "messages":
            header = " / ".join(part for part in (str(item.get("date", "")), str(item.get("author", ""))) if part)
            return f"• {header}\n  {item.get('text', '')}"
        return f"• {item}"

    @classmethod
    def section(cls, title: str, items: object, empty: str, kind: str) -> str:
        if not isinstance(items, list) or not items:
            return f"[bold]{title}[/]\n[dim]{empty}[/]"
        return f"[bold]{title}[/]\n" + "\n".join(cls.format_item(kind, item) for item in items)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="subject-files"):
            yield Static(self.section("Файлы", self.data.get("files"), "Файлов пока нет.", "files"), markup=True)
        with VerticalScroll(id="subject-upload"):
            upload_text = self.section("Загрузка файлов", self.data.get("upload_fields"), "Нет доступных полей загрузки.", "uploads")
            yield Static(upload_text + "\n[dim]U — прикрепить файл; X — удалить свой прикреплённый файл.[/]", markup=True)
        messages_empty = "Сообщений пока нет."
        if self.data.get("messages_error"):
            messages_empty += f" Ошибка загрузки: {self.data['messages_error']}"
        with VerticalScroll(id="subject-messages"):
            yield Static(self.section("Сообщения преподавателя", self.data.get("messages"), messages_empty, "messages"), markup=True)

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_download(self) -> None:
        self.app.choose_download(self.data)

    def action_upload(self) -> None:
        self.app.choose_upload(self.data)

    def action_delete_file(self) -> None:
        self.app.choose_delete(self.data)


class DownloadScreen(Screen[None]):
    BINDINGS = [("escape", "back", "Назад")]

    """Выбор файла для скачивания из материалов или прикреплённых работ."""

    CSS = """
    DownloadScreen { align: center middle; }
    #download-box { width: 80; height: auto; border: round $primary; padding: 1 2; }
    """

    def __init__(self, files: list[tuple[str, str]]) -> None:
        super().__init__()
        self.files = files

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="download-box"):
                yield Label("Выберите файл для скачивания (стрелки + Enter)")
                yield Select(self.files, prompt="Файл", id="download-select")

    def on_mount(self) -> None:
        self.query_one("#download-select", Select).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Select.Changed, "#download-select")
    def file_chosen(self, event: Select.Changed) -> None:
        if event.value is not Select.BLANK:
            self.app.download_selected(str(event.value))
            self.app.pop_screen()


class UploadScreen(Screen[None]):
    """Выбор работы и локального файла для прикрепления."""

    BINDINGS = [
        ("escape", "back", "Назад"),
        ("ctrl+enter", "submit", "Загрузить"),
    ]
    CSS = """
    UploadScreen { align: center middle; }
    #upload-box { width: 90; height: auto; border: round $primary; padding: 1 2; }
    #upload-path { margin-top: 1; }
    #upload-actions { height: auto; margin-top: 1; }
    #upload-actions Button { margin-right: 1; }
    """

    def __init__(self, subject_url: str, works: list[dict[str, object]]) -> None:
        super().__init__()
        self.subject_url = subject_url
        self.works = works

    def compose(self) -> ComposeResult:
        options = [
            (f"{work.get('work', 'Работа')} — {work.get('topic', '')}", str(index))
            for index, work in enumerate(self.works)
        ]
        with Center():
            with Vertical(id="upload-box"):
                yield Label("Выберите работу и файл для прикрепления")
                yield Select(options, prompt="Работа", id="upload-work")
                yield Input(placeholder=r"Путь к файлу, например C:\\Users\\...\\report.pdf", id="upload-path")
                with Vertical(id="upload-actions"):
                    yield Button("Выбрать файл…", id="browse-file")
                    yield Button("Загрузить", variant="primary", id="submit-upload")
                yield Static("Можно ввести путь вручную или открыть системное окно выбора файла. Esc — отмена.", classes="dim")

    def on_mount(self) -> None:
        self.query_one("#upload-work", Select).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_submit(self) -> None:
        selected = self.query_one("#upload-work", Select).value
        path = self.query_one("#upload-path", Input).value.strip().strip('"')
        if selected is Select.BLANK:
            self.notify("Выберите работу.", severity="warning")
            return
        if not path:
            self.notify("Укажите файл для загрузки.", severity="warning")
            return
        work = self.works[int(str(selected))]
        self.app.upload_selected(self.subject_url, work, Path(path))
        self.app.pop_screen()

    @on(Button.Pressed, "#submit-upload")
    def submit_pressed(self, event: Button.Pressed) -> None:
        self.action_submit()

    @on(Button.Pressed, "#browse-file")
    def browse_pressed(self, event: Button.Pressed) -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected_path = filedialog.askopenfilename(title="Выберите файл для загрузки")
            root.destroy()
        except Exception as error:
            self.notify(f"Не удалось открыть выбор файла: {error}", severity="error")
            return
        if selected_path:
            self.query_one("#upload-path", Input).value = selected_path


class DeleteScreen(Screen[None]):
    """Подтверждённое удаление прикреплённого к работе файла."""

    BINDINGS = [("escape", "back", "Назад")]
    CSS = """
    DeleteScreen { align: center middle; }
    #delete-box { width: 90; height: auto; border: round $error; padding: 1 2; }
    #delete-confirm { margin-top: 1; }
    """

    def __init__(self, subject_url: str, files: list[dict[str, str]]) -> None:
        super().__init__()
        self.subject_url = subject_url
        self.files = files

    def compose(self) -> ComposeResult:
        options = [
            (f"{item['work']}: {item['name']}", str(index))
            for index, item in enumerate(self.files)
        ]
        with Center():
            with Vertical(id="delete-box"):
                yield Label("Удаление файла нельзя отменить. Выберите файл:")
                yield Select(options, prompt="Файл", id="delete-select")
                yield Button("Удалить выбранный файл", variant="error", id="delete-confirm")

    def on_mount(self) -> None:
        self.query_one("#delete-select", Select).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#delete-confirm")
    def delete_pressed(self, event: Button.Pressed) -> None:
        selected = self.query_one("#delete-select", Select).value
        if selected is Select.BLANK:
            self.notify("Выберите файл.", severity="warning")
            return
        item = self.files[int(str(selected))]
        self.app.delete_selected(self.subject_url, item)
        self.app.pop_screen()


class LstuApp(App[None]):
    TITLE = "LSTU CLI"
    CSS = """
    #output { height: 1fr; border: round $primary; margin: 1 2 0 2; }
    #command { dock: bottom; margin: 1 2; }
    #status { dock: bottom; height: 1; padding: 0 2; background: $primary-darken-2; }
    """
    BINDINGS = [("ctrl+c", "quit", "Выход"), ("ctrl+l", "clear", "Очистить")]

    def __init__(self) -> None:
        super().__init__()
        self.parser: LstuParser | None = None
        self.login_name = ""
        self.semester_subjects: list[tuple[str, str]] = []
        self.semester_schedule: list[dict[str, object]] = []
        self.current_schedule_week: str | None = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="output", markup=True, wrap=True)
        yield Static("Сессия: нет", id="status")
        yield Input(placeholder="Введите команду; help — список команд", id="command")
        yield Footer()

    def on_mount(self) -> None:
        self.push_screen(LoginScreen())

    def start_session(self, parser: LstuParser, login: str) -> None:
        self.parser, self.login_name = parser, login
        self.write("[bold green]Авторизация успешна.[/] Введите [bold]help[/].")
        self.update_status()
        self.query_one("#command", Input).focus()

    @on(Input.Submitted, "#command")
    def command_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        event.input.value = ""
        if command:
            self.write(f"[bold cyan]> {command}[/]")
            self.run_worker(self.execute(command), exclusive=True)

    async def execute(self, raw: str) -> None:
        command, _, argument = raw.partition(" ")
        command, argument = command.lower(), argument.strip()
        if command in {"help", "?"}:
            self.write("[bold]Команды:[/] help, me, semesters, subject, today, tomorrow, schedule <today|tomorrow>, links, open <имя|URL>, clear, logout, quit")
            self.write("[dim]semesters — открыть семестр; subject или open subject — выбрать предмет текущего семестра.[/]")
            self.write("[dim]today / tomorrow — пары на сегодня или завтра по текущей неделе сайта.[/]")
        elif command == "me":
            await self.show_info()
        elif command in {"semesters", "semester"}:
            await self.choose_semester()
        elif command in {"subject", "subjects"} or (command == "open" and argument in {"subject", "предмет"}):
            self.choose_subject()
        elif command in {"today", "сегодня"} or (command in {"schedule", "расписание"} and argument in {"today", "сегодня"}):
            self.show_day_schedule(date.today())
        elif command in {"tomorrow", "завтра"} or (command in {"schedule", "расписание"} and argument in {"tomorrow", "завтра"}):
            self.show_day_schedule(date.today() + timedelta(days=1))
        elif command == "links":
            self.show_links()
        elif command == "open":
            await self.open_link(argument)
        elif command == "clear":
            self.action_clear()
        elif command == "logout":
            self.parser, self.semester_subjects = None, []
            self.semester_schedule, self.current_schedule_week = [], None
            self.query_one("#status", Static).update("Сессия: нет")
            self.push_screen(LoginScreen())
        elif command in {"quit", "exit"}:
            self.exit()
        else:
            self.write(f"[red]Неизвестная команда:[/] {command}. Используйте help.")

    async def show_info(self) -> None:
        if not self.parser:
            return
        try:
            data = await asyncio.to_thread(self.parser.user_info)
        except Exception as error:
            self.write(f"[red]Ошибка:[/] {error}")
            return
        table = Table(title="Профиль", show_header=False)
        table.add_column("Поле", style="bold cyan")
        table.add_column("Значение")
        for key, value in data.items():
            table.add_row(key, value)
        self.write(table)
        self.update_status()

    def show_links(self) -> None:
        if self.parser:
            self.write("[bold]Доступные ссылки:[/]")
            for key, (name, url) in self.parser.LINKS.items():
                self.write(f"  [cyan]{key}[/] — {name} ({url})")

    async def choose_semester(self) -> None:
        if not self.parser:
            return
        try:
            semesters = await asyncio.to_thread(self.parser.semesters)
        except Exception as error:
            self.write(f"[red]Не удалось загрузить семестры:[/] {error}")
            return
        if not semesters:
            self.write("[yellow]Доступных семестров не найдено.[/]")
            return
        self.update_status()
        self.push_screen(SemesterScreen(semesters))

    def open_semester(self, semester_url: str) -> None:
        self.write("[dim]Загружаю страницу семестра и ожидаю расписание…[/]")
        self.run_worker(self.load_semester(semester_url), exclusive=True)

    async def load_semester(self, semester_url: str) -> None:
        if not self.parser:
            return
        try:
            data = await asyncio.to_thread(self.parser.open_semester, semester_url)
        except Exception as error:
            self.write(f"[red]Не удалось открыть семестр:[/] {error}")
            return
        schedule = data.get("schedule", [])
        self.semester_schedule = [row for row in schedule if isinstance(row, dict)] if isinstance(schedule, list) else []
        week = data.get("current_week")
        self.current_schedule_week = str(week) if week in {"white", "green"} else None
        self.write("[bold green]Семестр открыт.[/]")
        if self.current_schedule_week:
            week_name = "белая" if self.current_schedule_week == "white" else "зелёная"
            self.write(f"[dim]По данным сайта сейчас {week_name} неделя.[/]")
        self.write("[bold]Расписание[/]")
        self.show_schedule(
            schedule,
            bool(data.get("schedule_found", True)),
            str(data.get("schedule_error") or ""),
        )
        subjects = data.get("subjects", [])
        if not isinstance(subjects, list) or not all(isinstance(item, tuple) and len(item) == 2 for item in subjects):
            self.write("[red]Некорректный формат предметов: ожидаются пары (название, URL).[/]")
            return
        self.semester_subjects = list(subjects)
        self.write("[bold]Предметы[/]")
        if self.semester_subjects:
            for index, (name, _) in enumerate(self.semester_subjects, start=1):
                self.write(f"  {index}. {name}")
            self.write("[dim]Введите subject или open subject, чтобы открыть предмет.[/]")
        else:
            self.write("[yellow]В этом семестре предметы не найдены.[/]")
        self.update_status()

    def show_schedule(self, schedule: object, found: bool, error: str = "") -> None:
        """Вывести пары занятий в читаемой таблице Rich."""
        if not found:
            self.write("[yellow]Таблица расписания не найдена на странице семестра.[/]")
            if error:
                self.write(f"[red]Не удалось получить расписание через скрытый браузер:[/] {error}")
            return
        if not isinstance(schedule, list) or not schedule:
            self.write("[dim]В расписании пока нет занятий.[/]")
            return

        def lesson_text(lesson: object) -> str:
            if not isinstance(lesson, dict):
                return "—"
            parts = [lesson.get("name"), lesson.get("groups"), lesson.get("teacher")]
            return "\n".join(str(part) for part in parts if part) or "—"

        table = Table(title="Расписание", show_lines=True)
        table.add_column("День", style="bold cyan", no_wrap=True)
        table.add_column("Время", no_wrap=True)
        table.add_column("Белая неделя")
        table.add_column("Ауд.", no_wrap=True)
        table.add_column("Зелёная неделя")
        table.add_column("Ауд.", no_wrap=True)
        for row in schedule:
            if not isinstance(row, dict):
                continue
            table.add_row(
                str(row.get("day") or "—"),
                str(row.get("time") or "—"),
                lesson_text(row.get("white")),
                str(row.get("white_room") or "—"),
                lesson_text(row.get("green")),
                str(row.get("green_room") or "—"),
            )
        self.write(table)

    def show_day_schedule(self, target_date: date) -> None:
        """Вывести пары одного дня, используя цвет недели, указанный сайтом."""
        if not self.semester_schedule:
            self.write("[yellow]Сначала откройте семестр командой semesters.[/]")
            return
        if not self.current_schedule_week:
            self.write("[yellow]Сайт не указал текущую неделю. Откройте семестр заново позже.[/]")
            return

        russian_days = {
            0: ("пн", "понедельник"), 1: ("вт", "вторник"), 2: ("ср", "среда"),
            3: ("чт", "четверг"), 4: ("пт", "пятница"), 5: ("сб", "суббота"),
            6: ("вс", "воскресенье"),
        }
        aliases = russian_days[target_date.weekday()]

        def is_target_day(value: object) -> bool:
            normalized = str(value or "").strip().lower().replace(".", "")
            return any(normalized == alias or normalized.startswith(alias) for alias in aliases)

        today = date.today()
        current_monday = today - timedelta(days=today.weekday())
        target_monday = target_date - timedelta(days=target_date.weekday())
        weeks_from_current = (target_monday - current_monday).days // 7
        week_key = self.current_schedule_week
        if weeks_from_current % 2:
            week_key = "green" if week_key == "white" else "white"

        rows = [row for row in self.semester_schedule if is_target_day(row.get("day"))]
        week_name = "Белая" if week_key == "white" else "Зелёная"
        title_date = target_date.strftime("%d.%m.%Y")
        if not rows:
            self.write(f"[dim]На {title_date} занятий нет ({week_name.lower()} неделя).[/]")
            return

        def lesson_text(lesson: object) -> str:
            if not isinstance(lesson, dict):
                return "—"
            return "\n".join(str(part) for part in (lesson.get("name"), lesson.get("groups"), lesson.get("teacher")) if part) or "—"

        table = Table(title=f"Расписание на {title_date} — {week_name.lower()} неделя", show_lines=True)
        table.add_column("Время", no_wrap=True)
        table.add_column("Занятие")
        table.add_column("Ауд.", no_wrap=True)
        lesson_key = week_key
        room_key = f"{lesson_key}_room"
        for row in rows:
            table.add_row(str(row.get("time") or "—"), lesson_text(row.get(lesson_key)), str(row.get(room_key) or "—"))
        self.write(table)

    def choose_subject(self) -> None:
        if not self.semester_subjects:
            self.write("[yellow]Сначала откройте семестр командой semesters.[/]")
            return
        self.push_screen(SubjectScreen(self.semester_subjects))

    def open_subject(self, subject_url: str) -> None:
        self.run_worker(self.load_subject(subject_url), exclusive=True)

    async def load_subject(self, subject_url: str) -> None:
        if not self.parser:
            return
        try:
            data = await asyncio.to_thread(self.parser.open_subject, subject_url)
        except Exception as error:
            self.write(f"[red]Не удалось открыть предмет:[/] {error}")
            return
        self.update_status()
        self.push_screen(SubjectDetailsScreen(data))

    def choose_download(self, data: dict[str, object]) -> None:
        """Показать единый список материалов и файлов, прикреплённых к работам."""
        files: list[tuple[str, str]] = []
        materials = data.get("files", [])
        if isinstance(materials, list):
            for item in materials:
                if isinstance(item, dict) and item.get("url"):
                    files.append((f"Материал: {item.get('name', 'Без названия')}", str(item["url"])))
        uploads = data.get("upload_fields", [])
        if isinstance(uploads, list):
            for work in uploads:
                if not isinstance(work, dict):
                    continue
                attachments = work.get("attachments", [])
                if isinstance(attachments, list):
                    for item in attachments:
                        if isinstance(item, dict) and item.get("url"):
                            files.append((f"{work.get('work', 'Работа')}: {item.get('name', 'Без названия')}", str(item["url"])))
        if not files:
            self.write("[yellow]Доступных для скачивания файлов нет.[/]")
            return
        self.push_screen(DownloadScreen(files))

    def choose_upload(self, data: dict[str, object]) -> None:
        """Открыть форму прикрепления для работ, в которых сайт разрешает загрузку."""
        fields = data.get("upload_fields", [])
        works = [
            field for field in fields
            if isinstance(field, dict) and field.get("can_upload") and field.get("attachment_oid")
        ] if isinstance(fields, list) else []
        subject_url = str(data.get("url", ""))
        if not works or not subject_url:
            self.notify("Для этого предмета нет доступных полей загрузки.", severity="warning")
            return
        self.push_screen(UploadScreen(subject_url, works))

    def choose_delete(self, data: dict[str, object]) -> None:
        """Показать только файлы, для которых сайт разрешает удаление."""
        fields = data.get("upload_fields", [])
        files: list[dict[str, str]] = []
        if isinstance(fields, list):
            for work in fields:
                if not isinstance(work, dict):
                    continue
                attachments = work.get("attachments", [])
                if not isinstance(attachments, list):
                    continue
                for attachment in attachments:
                    if isinstance(attachment, dict) and attachment.get("delete_oid"):
                        files.append({
                            "work": str(work.get("work", "Работа")),
                            "name": str(attachment.get("name", "Файл")),
                            "delete_oid": str(attachment["delete_oid"]),
                        })
        subject_url = str(data.get("url", ""))
        if not files or not subject_url:
            self.notify("Нет прикреплённых файлов, доступных для удаления.", severity="warning")
            return
        self.push_screen(DeleteScreen(subject_url, files))

    def download_selected(self, url: str) -> None:
        self.run_worker(self.download_file(url), exclusive=True)

    def upload_selected(self, subject_url: str, work: dict[str, object], path: Path) -> None:
        self.run_worker(self.upload_file(subject_url, work, path), exclusive=True)

    def delete_selected(self, subject_url: str, item: dict[str, str]) -> None:
        self.run_worker(self.delete_file(subject_url, item), exclusive=True)

    async def upload_file(self, subject_url: str, work: dict[str, object], path: Path) -> None:
        if not self.parser:
            return
        try:
            await asyncio.to_thread(
                self.parser.upload_work_file,
                subject_url,
                str(work.get("attachment_oid", "")),
                path,
            )
        except Exception as error:
            self.write(f"[red]Не удалось загрузить файл:[/] {error}")
            self.notify(f"Ошибка загрузки: {error}", severity="error")
            return
        self.write(f"[green]Файл прикреплён к работе «{work.get('work', 'Работа')}»:[/] {path.name}")
        self.notify("Файл успешно прикреплён.", severity="information")

    async def delete_file(self, subject_url: str, item: dict[str, str]) -> None:
        if not self.parser:
            return
        try:
            await asyncio.to_thread(self.parser.delete_work_file, subject_url, item["delete_oid"])
        except Exception as error:
            self.write(f"[red]Не удалось удалить файл:[/] {error}")
            self.notify(f"Ошибка удаления: {error}", severity="error")
            return
        self.write(f"[green]Файл удалён из работы «{item['work']}»:[/] {item['name']}")
        self.notify("Файл удалён.", severity="information")

    async def download_file(self, url: str) -> None:
        if not self.parser:
            return
        try:
            target = await asyncio.to_thread(self.parser.download_file, url, Path(__file__).with_name("downloads"))
        except Exception as error:
            self.write(f"[red]Не удалось скачать файл:[/] {error}")
            return
        self.write(f"[green]Файл сохранён:[/] {target}")

    async def open_link(self, link: str) -> None:
        if not self.parser:
            return
        if not link:
            self.write("[yellow]Укажите имя ссылки или URL: open profile[/]")
            return
        try:
            response = await asyncio.to_thread(self.parser.open, link)
        except Exception as error:
            self.write(f"[red]Не удалось открыть ссылку:[/] {error}")
            return
        self.write(f"[green]Открыто:[/] {response.url} (HTTP {response.status_code})")
        self.update_status()

    def update_status(self) -> None:
        location = self.parser.current_location if self.parser else "нет"
        self.query_one("#status", Static).update(f"Пользователь: {self.login_name or '—'} | Расположение: {location}")

    def write(self, renderable: Any) -> None:
        self.query_one("#output", RichLog).write(renderable)

    def action_clear(self) -> None:
        self.query_one("#output", RichLog).clear()


if __name__ == "__main__":
    LstuApp().run()

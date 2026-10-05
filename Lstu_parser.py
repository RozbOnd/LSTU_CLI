"""Клиент личного кабинета ЛГТУ без привязки к конкретному интерфейсу."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import copy
import time
import time

class LstuParser:
    BASE_URL = "http://lk.stu.lipetsk.ru/"
    PROFILE_URL = "http://lk.stu.lipetsk.ru/personal/"
    SEMESTERS_URL = "http://lk.stu.lipetsk.ru/education/0/"
    HTTP_RETRIES = 3
    HTTP_RETRY_DELAY = 2.0
    LINKS = {
        "home": ("Главная", BASE_URL),
        "profile": ("Личный кабинет", PROFILE_URL),
        "semesters": ("Семестры", SEMESTERS_URL),
    }

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
        })
        self.current_url = self.BASE_URL
        self.current_link_name = self.LINKS["home"][0]
        self.current_response: requests.Response | None = None

    @property
    def current_location(self) -> str:
        return self.current_link_name

    @staticmethod
    def soup_from_response(response: requests.Response) -> BeautifulSoup:
        """Разобрать байты ответа с учётом meta charset (в т.ч. windows-1251)."""
        return BeautifulSoup(response.content, "lxml")

    def open(self, link: str) -> requests.Response:
        key = link.strip().lower()
        if key in self.LINKS:
            name, url = self.LINKS[key]
        else:
            name, url = link.strip(), urljoin(self.BASE_URL, link.strip())
        last_error: requests.RequestException | None = None
        for attempt in range(self.HTTP_RETRIES):
            try:
                response = self.session.get(url, timeout=20)
                response.raise_for_status()
            except requests.RequestException as error:
                last_error = error
                if attempt < self.HTTP_RETRIES - 1:
                    time.sleep(self.HTTP_RETRY_DELAY * (attempt + 1))
                    continue
                break
            self.current_url, self.current_link_name, self.current_response = response.url, name, response
            return response
        raise RuntimeError(f"Сайт закрыл соединение после {self.HTTP_RETRIES} попыток: {last_error}")

    def is_auth(self) -> bool:
        response = self.session.get(self.PROFILE_URL, timeout=20)
        return response.ok and "AUTH_FORM" not in response.text

    def login(self, login: str, password: str) -> bool:
        login_page = self.open("home")
        sessid = self.soup_from_response(login_page).find("input", id="sessid")
        if sessid is None or not sessid.get("value"):
            raise RuntimeError("Не удалось получить форму авторизации сайта.")
        response = self.session.post(
            urljoin(self.BASE_URL, "index.php"),
            data={"AUTH_FORM": "1", "sessid": sessid["value"], "LOGIN": login, "PASSWORD": password},
            allow_redirects=True,
            timeout=20,
        )
        try:
            return response.json().get("SUCCESS") == "1"
        except ValueError:
            return self.is_auth()

    def user_info(self) -> dict[str, str]:
        soup = self.soup_from_response(self.open("profile"))
        personal_block = soup.find("div", class_="personal_text")
        if personal_block is None:
            raise RuntimeError("Страница профиля недоступна или изменила разметку.")
        data: dict[str, str] = {}
        if name := personal_block.find("h1"):
            data["ФИО"] = name.get_text(" ", strip=True)
        education_title = soup.find("h4", string="Обучение")
        education = education_title.find_next_sibling("p") if education_title else None
        if education:
            for field in education.find_all("strong"):
                key, value = field.get_text(strip=True).rstrip(":"), field.next_sibling
                if key and value:
                    data[key] = str(value).strip()
        return data

    def semesters(self) -> list[tuple[str, str]]:
        soup = self.soup_from_response(self.open("semesters"))
        semesters_block = soup.find("ul", class_="ul-main")
        if semesters_block is None:
            raise RuntimeError("Страница семестров недоступна или изменила разметку.")
        return [
            (anchor.get_text(" ", strip=True) or "Семестр", urljoin(self.BASE_URL, anchor["href"]))
            for anchor in semesters_block.find_all("a", href=True)
        ]

    def dynamic_html_from_browser(self, url: str, selector: str, settle_seconds: float = 0) -> str:
        """Дождаться CSS-селектора в скрытом Chrome и вернуть его HTML."""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as expected
            from selenium.common.exceptions import TimeoutException, WebDriverException
        except ImportError as error:
            raise RuntimeError("Для загрузки динамических данных установите Selenium: pip install selenium") from error

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        last_error: RuntimeError | None = None
        # ChromeDriver изредка завершает соединение при первом запуске. Повторная
        # попытка обычно решает это, не заставляя пользователя входить заново.
        for attempt in range(2):
            driver = None
            try:
                driver = webdriver.Chrome(options=options)
                # Сначала открыть домен, затем передать cookies авторизованной requests-сессии.
                driver.get(self.BASE_URL)
                for cookie in self.session.cookies:
                    if cookie.domain and not "lk.stu.lipetsk.ru".endswith(cookie.domain.lstrip(".")):
                        continue
                    selenium_cookie = {"name": cookie.name, "value": cookie.value, "path": cookie.path or "/"}
                    try:
                        driver.add_cookie(selenium_cookie)
                    except WebDriverException:
                        continue
                driver.get(url)
                try:
                    WebDriverWait(driver, 20).until(
                        expected.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                except TimeoutException as error:
                    last_error = RuntimeError(f"Не дождались блока {selector} в скрытом браузере.")
                    raise last_error from error
                if settle_seconds:
                    time.sleep(settle_seconds)
                return driver.page_source
            except RuntimeError:
                raise
            except WebDriverException:
                last_error = RuntimeError("Скрытый браузер не смог загрузить данные. Повторите открытие предмета.")
            finally:
                if driver is not None:
                    try:
                        driver.quit()
                    except WebDriverException:
                        pass

            if attempt == 0:
                time.sleep(1)

        raise last_error or RuntimeError("Не удалось запустить скрытый браузер.")

    def schedule_table_from_browser(self, semester_url: str):
        html = self.dynamic_html_from_browser(semester_url, "#schedule_lections table.table.table-small")
        soup = BeautifulSoup(html, "lxml")
        return soup

    @staticmethod
    def current_schedule_week(soup: BeautifulSoup) -> str | None:
        """Определить текущую неделю по информационной надписи страницы."""
        def week_from_text(text: str) -> str | None:
            normalized = " ".join(text.lower().replace("ё", "е").split())
            has_green = "зелен" in normalized and "недел" in normalized
            has_white = "бел" in normalized and "недел" in normalized
            # В заголовке расписания есть оба цвета — это не индикатор недели.
            if has_green and has_white:
                return None
            if has_green:
                return "green"
            if has_white:
                return "white"
            return None

        # Заголовки таблицы содержат обе недели, поэтому рассматриваем только
        # элементы-индикаторы: «сегодня», «текущая неделя» или id/class с week.
        for text_node in soup.find_all(string=re.compile(r"сегодня|текущ\w*\s+недел", re.IGNORECASE)):
            parent = text_node.parent
            # Начинаем с самого близкого контейнера, чтобы не захватить всю
            # таблицу расписания с двумя цветовыми колонками.
            for node in (parent, parent.parent if parent else None, parent.parent.parent if parent and parent.parent else None):
                if node and (week := week_from_text(node.get_text(" ", strip=True))):
                    return week
        for node in soup.select("[id*='week' i], [class*='week' i]"):
            if week := week_from_text(node.get_text(" ", strip=True)):
                return week
        return None

    @staticmethod
    def parse_lesson(td):
        """Разбирает ячейку с занятием: название, группы, преподаватель, ссылка."""
        if td is None:
            return None
        td = copy.copy(td)                      # не портим исходное дерево

        for br in td.find_all("br"):
            br.replace_with("\n")

        a = td.find("a", href=True)
        teacher = a.get_text(strip=True) if a else None
        teacher_url = urljoin(LstuParser.BASE_URL, a["href"]) if a else None
        if a:
            a.replace_with("")

        # \xa0 (nbsp) отсекается обычным strip()
        lines = [x.strip() for x in td.get_text().split("\n") if x.strip()]
        if not lines:
            return None

        return {
            "name": lines[0],
            "groups": lines[1] if len(lines) > 1 else None,
            "teacher": teacher,
            "teacher_url": teacher_url,
        }
    
    def open_semester(self, semester_url: str) -> dict[str, object]:
        """Открыть семестр; расписание заполняется отдельно."""
        response = self.open(semester_url)
        soup = self.soup_from_response(response)
        schedule_table = soup.select_one("table.table.table-small") or soup.select_one("table.table-small")
        schedule_soup = soup
        schedule_error: str | None = None
        current_week = self.current_schedule_week(schedule_soup)
        # Индикатор текущей недели сайт может вставить JS-ом позже таблицы.
        if schedule_table is None or current_week is None:
            try:
                browser_soup = self.schedule_table_from_browser(semester_url)
                browser_table = browser_soup.select_one("#schedule_lections table.table.table-small")
                if browser_table is not None:
                    schedule_soup = browser_soup
                    schedule_table = browser_table
                current_week = self.current_schedule_week(browser_soup) or current_week
            except Exception as error:
                if schedule_table is None:
                    schedule_error = str(error)
        if heading := soup.find(["h1", "h2"]):
            self.current_link_name = heading.get_text(" ", strip=True)
        subjects = [
            (anchor.get_text(" ", strip=True) or "Предмет", urljoin(self.BASE_URL, anchor["href"]))
            for item in soup.select("li.submenu.level3")
            if (anchor := item.find("a", href=True))
        ]

        schedule = []
        current_day = None
        if schedule_table is not None:
            for tr in schedule_table.select("tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 6:
                    continue
                day_cell = tds[0].get_text(strip=True)
                if day_cell:
                    current_day = day_cell

                schedule.append({
                    "day": current_day,
                    "time": tds[1].get_text(strip=True),
                    "white": self.parse_lesson(tds[2]),
                    "white_room": tds[3].get_text(" ", strip=True) or None,
                    "green": self.parse_lesson(tds[4]),
                    "green_room": tds[5].get_text(" ", strip=True) or None,
                })

        return {
            "schedule": schedule,
            "schedule_found": schedule_table is not None,
            "schedule_error": schedule_error,
            "current_week": current_week,
            "subjects": subjects,
        }

    @staticmethod
    def parse_messages(soup: BeautifulSoup) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for block in soup.select("div.block_msg div.comment__block, div.comment__block"):
            body = block.select_one(".comment__body")
            if body is None:
                continue
            author_link = body.select_one("p a")
            text_block = body.select_one(".col-xs-12")
            messages.append({
                "date": block.get("data-msg", ""),
                "author": author_link.get_text(" ", strip=True) if author_link else "",
                "text": text_block.get_text(" ", strip=True) if text_block else "",
            })
        return messages

    def open_subject(self, subject_url: str) -> dict[str, object]:
        """Открыть предмет, извлечь материалы, работы студента и сообщения."""
        response = self.open(subject_url)
        soup = self.soup_from_response(response)
        if heading := soup.find(["h1", "h2"]):
            self.current_link_name = heading.get_text(" ", strip=True)

        materials_heading = soup.find(
            lambda tag: tag.name == "strong" and "Материалы дисциплины" in tag.get_text(" ", strip=True)
        )
        materials_list = materials_heading.parent.find_next_sibling("ul") if materials_heading else None
        files = [
            {
                "name": link.get_text(" ", strip=True),
                "url": urljoin(self.BASE_URL, link["href"]),
            }
            for link in materials_list.select("a[href]")
        ] if materials_list else []

        upload_fields: list[dict[str, object]] = []
        works_table = soup.select_one("table.table.bg-gray.edu-table")
        if works_table:
            for row in works_table.select("tbody tr"):
                cells = row.find_all("td", recursive=False)
                if len(cells) < 5:
                    continue
                upload_link = cells[4].find(
                    "a", onclick=lambda value: value and "ME_SWATTACHMENT" in value
                )
                upload_match = re.search(
                    r"OnDialog\('add_attachment','ME_SWATTACHMENT','([^']*)','([^']*)'\)",
                    upload_link.get("onclick", "") if upload_link else "",
                )
                attachments: list[dict[str, str]] = []
                for item in cells[4].select("ul li"):
                    link = item.select_one("a[href]")
                    delete_link = item.find("a", onclick=lambda value: value and "Delete('ME_SWATTACHMENT'" in value)
                    delete_match = re.search(
                        r"Delete\('ME_SWATTACHMENT','([^']*)'",
                        delete_link.get("onclick", "") if delete_link else "",
                    )
                    if link:
                        attachments.append({
                            "name": link.get_text(" ", strip=True),
                            "url": urljoin(self.BASE_URL, link["href"]),
                            "delete_oid": delete_match.group(1) if delete_match else "",
                        })
                upload_fields.append({
                    "work": cells[0].get_text(" ", strip=True),
                    "topic": cells[1].get_text(" ", strip=True),
                    "grade": cells[2].get_text(" ", strip=True),
                    "teacher": cells[3].get_text(" ", strip=True),
                    "can_upload": upload_match is not None,
                    "attachment_oid": upload_match.group(1) if upload_match else "",
                    "attachment_bind": upload_match.group(2) if upload_match else "",
                    "attachments": attachments,
                })

        messages = self.parse_messages(soup)
        messages_error: str | None = None
        if not messages:
            try:
                messages_html = self.dynamic_html_from_browser(subject_url, "div.block_msg", settle_seconds=5)
                messages = self.parse_messages(BeautifulSoup(messages_html, "lxml"))
            except Exception as error:
                messages_error = str(error)
        return {
            "url": subject_url,
            "messages": messages,
            "messages_error": messages_error,
            "files": files,
            "upload_fields": upload_fields,
        }

    def upload_work_file(self, subject_url: str, attachment_oid: str, file_path: Path) -> None:
        """Прикрепить файл к работе через форму сайта в скрытом Chrome."""
        if not attachment_oid:
            raise RuntimeError("Для этой работы не найдена форма загрузки.")
        if not file_path.is_file():
            raise RuntimeError("Указанный файл не найден.")

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as expected
            from selenium.common.exceptions import TimeoutException, WebDriverException
        except ImportError as error:
            raise RuntimeError("Для загрузки файлов установите Selenium: pip install selenium") from error

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        driver = None
        try:
            driver = webdriver.Chrome(options=options)
            driver.get(self.BASE_URL)
            for cookie in self.session.cookies:
                if cookie.domain and not "lk.stu.lipetsk.ru".endswith(cookie.domain.lstrip(".")):
                    continue
                try:
                    driver.add_cookie({"name": cookie.name, "value": cookie.value, "path": cookie.path or "/"})
                except WebDriverException:
                    continue

            driver.get(subject_url)
            attachment_list = f"#sw_attachments{attachment_oid.rsplit(':', 1)[-1]} > li"
            attachments_before = len(driver.find_elements(By.CSS_SELECTOR, attachment_list))
            button = WebDriverWait(driver, 20).until(
                expected.element_to_be_clickable((
                    By.XPATH,
                    f"//a[contains(@onclick, 'ME_SWATTACHMENT') and contains(@onclick, '{attachment_oid}')]",
                ))
            )
            driver.execute_script("arguments[0].click();", button)
            file_input = WebDriverWait(driver, 20).until(
                expected.presence_of_element_located((By.CSS_SELECTOR, "#dialog input#uploadAttachment"))
            )
            file_input.send_keys(str(file_path.resolve()))
            name_input = driver.find_element(By.CSS_SELECTOR, "#dialog input#ATTACHMENT_NAME")
            if not name_input.get_attribute("value"):
                name_input.send_keys(file_path.name)
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));"
                "OnKeyUpAttachForm($('#attach_form'));",
                file_input,
            )
            submit = WebDriverWait(driver, 10).until(
                expected.element_to_be_clickable((By.CSS_SELECTOR, "#dialog input[name='SUBMIT']"))
            )
            submit.click()

            def upload_completed(browser) -> bool:
                if len(browser.find_elements(By.CSS_SELECTOR, attachment_list)) > attachments_before:
                    return True
                errors = browser.find_elements(By.CSS_SELECTOR, "#error")
                if errors and errors[0].is_displayed() and errors[0].text.strip():
                    raise RuntimeError(f"Сайт отклонил загрузку: {errors[0].text.strip()}")
                return False

            WebDriverWait(driver, 60).until(
                upload_completed
            )
        except TimeoutException as error:
            raise RuntimeError("Сайт не подтвердил загрузку файла за отведённое время.") from error
        except WebDriverException as error:
            raise RuntimeError("Скрытый браузер не смог загрузить файл. Повторите попытку.") from error
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException:
                    pass

    def delete_work_file(self, subject_url: str, attachment_oid: str) -> None:
        """Удалить прикреплённый студентом файл через кнопку страницы предмета."""
        if not attachment_oid:
            raise RuntimeError("Для этого файла недоступно удаление.")
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as expected
            from selenium.common.exceptions import TimeoutException, WebDriverException
        except ImportError as error:
            raise RuntimeError("Для удаления файлов установите Selenium: pip install selenium") from error

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        driver = None
        try:
            driver = webdriver.Chrome(options=options)
            driver.get(self.BASE_URL)
            for cookie in self.session.cookies:
                if cookie.domain and not "lk.stu.lipetsk.ru".endswith(cookie.domain.lstrip(".")):
                    continue
                try:
                    driver.add_cookie({"name": cookie.name, "value": cookie.value, "path": cookie.path or "/"})
                except WebDriverException:
                    continue
            driver.get(subject_url)
            button = WebDriverWait(driver, 20).until(
                expected.element_to_be_clickable((
                    By.XPATH,
                    f"//a[contains(@onclick, \"Delete('ME_SWATTACHMENT','{attachment_oid}'\")]",
                ))
            )
            driver.execute_script("arguments[0].click();", button)
            WebDriverWait(driver, 5).until(expected.alert_is_present()).accept()
            WebDriverWait(driver, 30).until(
                lambda browser: not browser.find_elements(
                    By.XPATH,
                    f"//a[contains(@onclick, \"Delete('ME_SWATTACHMENT','{attachment_oid}'\")]",
                )
            )
        except TimeoutException as error:
            raise RuntimeError("Сайт не подтвердил удаление файла.") from error
        except WebDriverException as error:
            raise RuntimeError("Скрытый браузер не смог удалить файл. Повторите попытку.") from error
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException:
                    pass

    def download_file(self, url: str, directory: Path) -> Path:
        """Скачать авторизованный файл в указанную папку и вернуть его путь."""
        response = self.session.get(
            url,
            headers={"Referer": self.current_url},
            timeout=60,
            stream=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            raise RuntimeError("Сайт вернул HTML вместо файла. Возможно, сессия авторизации истекла.")
        disposition = response.headers.get("Content-Disposition", "")
        match = re.search(r"filename\*?=(?:UTF-8''|\")?([^;\"]+)", disposition, flags=re.IGNORECASE)
        filename = match.group(1).strip() if match else url.rsplit("/", 1)[-1]
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename) or "download"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        counter = 2
        while target.exists():
            target = directory / f"{Path(filename).stem} ({counter}){Path(filename).suffix}"
            counter += 1
        with target.open("wb") as file:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    file.write(chunk)
        return target

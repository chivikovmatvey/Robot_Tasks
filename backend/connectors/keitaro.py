"""Коннектор к Keitaro через браузер (Playwright).

У аккаунта нет Admin API — скачивание/заливка лендов делается только в UI,
поэтому работаем через headless-браузер: логинимся, ищем оффер по ID в гриде
«Офферы» и жмём кнопку «Скачать» (data-test-id="download-button"), перехватывая
скачивание ZIP.

Пока реализована только проверка доступа + скачивание ленда. Заливка будет
добавлена позже.

Запуск как скрипт (см. также test_keitaro.py):
    .venv/bin/python -m connectors.keitaro 9224
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    sync_playwright,
)

log = logging.getLogger("keitaro.connector")


class KeitaroError(RuntimeError):
    pass


class KeitaroAuthError(KeitaroError):
    pass


class KeitaroClient:
    """Браузерный клиент Keitaro.

    Использовать как контекст-менеджер::

        with KeitaroClient(base_url, user, password) as kt:
            path = kt.download_offer(9224, dest_dir)
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        headless: bool = True,
        state_path: Optional[str] = None,
        timeout_ms: int = 45_000,
    ):
        if not base_url:
            raise KeitaroError("base_url пуст (заполни KEITARO_BASE_URL в .env)")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.headless = headless
        # Файл с cookie-сессией — чтобы не логиниться каждый запуск.
        self.state_path = Path(state_path) if state_path else None
        self.timeout_ms = timeout_ms

        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        # В рамках одного сеанса браузера логинимся один раз (login() дорогой —
        # перезагружает SPA), далее переиспользуем. Имена офферов кэшируем при
        # скачивании, чтобы не делать второй проход по гриду.
        self._authed = False
        self._name_cache: dict[str, str] = {}

    # ── lifecycle ────────────────────────────────────────────────
    def __enter__(self) -> "KeitaroClient":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        ctx_kwargs: dict = {"accept_downloads": True}
        if self.state_path and self.state_path.exists():
            ctx_kwargs["storage_state"] = str(self.state_path)
        self._ctx = self._browser.new_context(**ctx_kwargs)
        self._ctx.set_default_timeout(self.timeout_ms)
        self._page = self._ctx.new_page()
        self._attach_page_logging(self._page)
        log.info("Keitaro: браузер запущен (headless=%s, base=%s)",
                 self.headless, self.base_url)
        return self

    def _attach_page_logging(self, page: Page) -> None:
        """Слушатели событий страницы — чтобы видеть, где падает Playwright-сценарий
        (ошибки JS, упавшие запросы, краш страницы, навигация)."""
        try:
            page.on("console", lambda m: log.debug("PW console[%s]: %s", m.type, m.text))
            page.on("pageerror", lambda e: log.warning("PW pageerror: %s", e))
            page.on("crash", lambda: log.error("PW страница КРАШНУЛАСЬ"))
            page.on("framenavigated",
                    lambda f: log.debug("PW navigated: %s", f.url) if f == page.main_frame else None)
            page.on("requestfailed",
                    lambda r: log.warning("PW requestfailed: %s %s (%s)",
                                          r.method, r.url, (r.failure or "")))
        except Exception:  # noqa: BLE001
            pass

    def __exit__(self, *exc) -> None:
        try:
            if self._ctx and self.state_path:
                self._ctx.storage_state(path=str(self.state_path))
        except Exception:  # noqa: BLE001 — сохранение сессии необязательно
            pass
        for closer in (self._ctx, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            self._pw.stop()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise KeitaroError("Клиент не инициализирован (используй with KeitaroClient(...))")
        return self._page

    # ── навигация с ретраями (узел хрупкий) ──────────────────────
    def _goto(self, url: str, *, attempts: int = 3) -> None:
        last: Optional[Exception] = None
        for i in range(attempts):
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                return
            except Exception as e:  # noqa: BLE001 — ERR_CONNECTION_CLOSED и т.п.
                last = e
                log.warning("goto %s сбой (%d/%d): %s", url, i + 1, attempts, e)
                self.page.wait_for_timeout(1500 * (i + 1))
        raise KeitaroError(f"Не удалось открыть {url}: {last}")

    # ── auth ─────────────────────────────────────────────────────
    def _looks_logged_in(self) -> bool:
        """Признак, что мы внутри SPA, а не на странице логина."""
        try:
            self.page.wait_for_selector(
                '[data-test-id="search-input"], #search-field, [data-test-id="top-navbar"]',
                timeout=8_000,
            )
            return True
        except PWTimeout:
            return False

    def login(self) -> None:
        # Уже логинились в этом сеансе браузера — НЕ перезагружаем SPA
        # (повторный goto(base_url) был главным тормозом: каждый ленд = лишняя
        # перезагрузка интерфейса + 8с проверки).
        if self._authed:
            return
        page = self.page
        log.info("login: открываю %s", self.base_url)
        self._goto(self.base_url)

        # Уже залогинены по сохранённой сессии?
        if self._looks_logged_in():
            log.info("login: сессия активна по cookie, форма входа не нужна")
            self._authed = True
            return
        log.info("login: не залогинены — ищу форму входа")

        pwd = page.locator('input[type="password"]').first
        try:
            pwd.wait_for(state="visible", timeout=10_000)
        except PWTimeout:
            # Ни формы логина, ни признаков SPA — что-то не так.
            self._dump_debug("login-no-form")
            raise KeitaroAuthError(
                "Не найдена ни форма логина, ни интерфейс. Проверь KEITARO_BASE_URL."
            )

        if not self.username or not self.password:
            raise KeitaroAuthError("KEITARO_USERNAME / KEITARO_PASSWORD не заданы в .env")

        # Поле логина: у Keitaro обычно name=login, иногда username.
        login_field = None
        for sel in ('input[name="login"]', 'input[name="username"]',
                    'input[type="text"]', 'input:not([type])'):
            cand = page.locator(sel).first
            if cand.count() and cand.is_visible():
                login_field = cand
                break
        if login_field is None:
            self._dump_debug("login-no-user-field")
            raise KeitaroAuthError("Не найдено поле логина на странице входа")

        login_field.fill(self.username)
        pwd.fill(self.password)
        pwd.press("Enter")

        if not self._looks_logged_in():
            self._dump_debug("login-failed")
            raise KeitaroAuthError(
                "Логин не прошёл — проверь username/password (см. debug-скрин в storage/keitaro)"
            )
        self._authed = True
        log.info("Залогинились как %s", self.username)

    # ── offers ───────────────────────────────────────────────────
    _GRID_READY = '[data-test-id="grid-body"], tr.grid-tbody-row'
    # Фильтр ВНУТРИ грида (не глобальный поиск в шапке навбара!).
    _GRID_FILTER = (
        'input.search-filter, [data-test-id="grid-toolbar"] input[type="search"]'
    )
    # Задержка после ввода в фильтр грида: грид фильтрует по мере ввода (debounce)
    # и сперва отдаёт результаты по НЕПОЛНОМУ запросу → можно прочитать чужой
    # оффер/сеть. Ждём, пока догрузятся результаты по ПОЛНОМУ запросу.
    _GRID_SETTLE_MS = 2500

    def _apply_grid_filter(self, value: str) -> None:
        """Вводит значение в фильтр грида и ждёт догрузки результатов по ПОЛНОМУ
        запросу (debounce-settle). Использовать перед чтением строк."""
        filt = self.page.locator(self._GRID_FILTER).first
        filt.wait_for(state="visible", timeout=10_000)
        filt.fill("")
        filt.fill(str(value))
        self.page.wait_for_timeout(self._GRID_SETTLE_MS)

    def _open_offers(self) -> None:
        page = self.page
        # Идемпотентность: если грид офферов уже открыт — не перезагружаем
        # (повторная SPA-навигация на тот же hash иногда подвисает).
        if "#!/offers" in (page.url or ""):
            try:
                page.wait_for_selector(self._GRID_READY, timeout=3_000)
                return
            except PWTimeout:
                pass
        # Хэш-роут SPA Keitaro (важен и завершающий слэш). С ретраями —
        # tlgk.host транзиентно тормозит/рвёт TLS, одной попытки мало.
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                self._goto(f"{self.base_url}/#!/offers/")
                page.wait_for_selector(self._GRID_READY, timeout=15_000)
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("Грид офферов не открылся (%d/3): %s", attempt + 1, e)
                # Фолбэк: пункт меню «Офферы».
                try:
                    page.locator('a:has-text("Офферы")').first.click(timeout=4_000)
                    page.wait_for_selector(self._GRID_READY, timeout=10_000)
                    return
                except Exception:  # noqa: BLE001
                    page.wait_for_timeout(1500 * (attempt + 1))
        self._dump_debug("offers-grid-not-loaded")
        raise KeitaroError(f"Не удалось открыть грид офферов (#!/offers/): {last_err}")

    def _find_offer_row(self, offer_id: int | str):
        """Возвращает локатор строки грида для оффера с данным KT-id."""
        page = self.page
        # Фильтр грида сужает выдачу (грид постраничный, 16k+ офферов).
        filt = page.locator(self._GRID_FILTER).first
        filt.wait_for(state="visible", timeout=10_000)
        filt.fill("")
        filt.fill(str(offer_id))

        edit_link = page.locator(f'a[href$="/editor/offer/{offer_id}"]').first
        try:
            edit_link.wait_for(state="visible", timeout=15_000)
        except PWTimeout:
            self._dump_debug(f"offer-{offer_id}-not-found")
            raise KeitaroError(f"Оффер {offer_id} не найден в гриде после фильтра")
        # Строка-предок этой ссылки.
        return page.locator(
            f'tr.grid-tbody-row:has(a[href$="/editor/offer/{offer_id}"])'
        ).first

    @staticmethod
    def _extract_offer_name(txt: str, offer_id: int | str) -> str:
        """Из текста строки грида вытаскивает название оффера-донора.

        Название — ячейка со скобками вида
        '9224 Calmano [VARICOSIS-PE-VA_0050] [land es -] ...'. Берём строку
        со скобками; иначе — содержательную строку с id; иначе — самую длинную.
        """
        lines = [ln.strip() for ln in (txt or "").splitlines() if ln.strip()]
        bracketed = [ln for ln in lines if "[" in ln and "]" in ln]
        if bracketed:
            return max(bracketed, key=len)
        with_id = [ln for ln in lines if str(offer_id) in ln and ln != str(offer_id)]
        if with_id:
            return max(with_id, key=len)
        return max(lines, key=len) if lines else (txt or "").strip()

    def get_offer_name(self, offer_id: int | str) -> str:
        """Best-effort: название оффера-донора (содержит продукт/вертикаль/гео)."""
        key = str(offer_id).strip()
        if key in self._name_cache:  # снято при download_offer — без 2-го прохода
            return self._name_cache[key]
        self.login()
        self._open_offers()
        row = self._find_offer_row(offer_id)
        name = self._extract_offer_name(row.inner_text() or "", offer_id)
        self._name_cache[key] = name
        return name

    def get_offer_names(self, offer_ids) -> dict[str, str | None]:
        """Названия для нескольких офферов за ОДИН сеанс браузера.

        Логинимся и открываем грид один раз, далее по каждому id меняем фильтр.
        Сбой по отдельному id не валит весь батч (значение = None).
        """
        self.login()
        self._open_offers()
        out: dict[str, str | None] = {}
        for oid in offer_ids:
            key = str(oid).strip()
            if not key:
                continue
            try:
                row = self._find_offer_row(key)
                out[key] = self._extract_offer_name(row.inner_text() or "", key)
            except Exception as e:  # noqa: BLE001
                log.warning("Не получить название оффера %s: %s", key, e)
                out[key] = None
        return out

    def download_offer(self, offer_id: int | str, dest_dir: str | Path) -> Path:
        """Скачивает архив ленда оффера. Возвращает путь к сохранённому ZIP."""
        t0 = time.monotonic()

        def _t(step: str) -> None:
            log.info("download_offer[%s]: %s (%.1fс)", offer_id, step, time.monotonic() - t0)

        _t("старт → login()")
        self.login()
        _t("login ok → открываю грид офферов")
        self._open_offers()
        _t("грид открыт → ищу строку оффера")
        row = self._find_offer_row(offer_id)
        _t("строка найдена")

        # Снимаем название оффера из ТОЙ ЖЕ строки — чтобы get_offer_name не делал
        # повторный проход по гриду (это удваивало время на каждый ленд).
        try:
            self._name_cache[str(offer_id).strip()] = \
                self._extract_offer_name(row.inner_text() or "", offer_id)
        except Exception:  # noqa: BLE001
            pass

        dl_btn = row.locator('button[data-test-id="download-button"]').first
        try:
            dl_btn.wait_for(state="visible", timeout=10_000)
        except PWTimeout:
            self._dump_debug(f"offer-{offer_id}-no-download-button")
            raise KeitaroError(
                f"Кнопка скачивания не найдена в строке оффера {offer_id} "
                f"(см. debug-скрин в storage/keitaro)")
        _t("кнопка скачивания видна → клик и ожидание загрузки")

        with self.page.expect_download(timeout=self.timeout_ms) as dl_info:
            dl_btn.click()
        download = dl_info.value
        _t("загрузка началась → сохраняю файл")

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        suggested = download.suggested_filename or f"offer_{offer_id}.zip"
        target = dest_dir / suggested
        download.save_as(str(target))
        _t(f"готово: {target.name} ({target.stat().st_size} б)")
        return target

    # ══════════════════════════════════════════════════════════════
    # СОЗДАНИЕ ОФФЕРА (заливка ленда)
    #
    # ВНИМАНИЕ: код подготовлен по скриншотам интерфейса и НЕ протестирован
    # вживую (заливать нельзя). На каждом шаге делается скриншот в
    # storage/keitaro/upload-<step>.png — по ним при первом реальном прогоне
    # (в рабочее время) откалибровать селекторы, помеченные «СЕЛЕКТОР?».
    # ══════════════════════════════════════════════════════════════

    def _shot(self, tag: str) -> None:
        try:
            dbg = Path(__file__).resolve().parents[1] / "storage" / "keitaro"
            dbg.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(dbg / f"upload-{tag}.png"), full_page=True)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _bracket_vertical_geo(offer_name: str) -> str:
        """Первая скобка названия — [ВЕРТИКАЛЬ-ГЕО], напр. '[PARASITES-CO]'."""
        import re
        m = re.search(r"\[[^\]]+\]", offer_name or "")
        return m.group(0) if m else ""

    @staticmethod
    def _extract_network_number(row_text: str, offer_id: str = "") -> Optional[str]:
        """Партнёрская сеть = числовая колонка после названия/группы (напр. 75,
        114). Название содержит свои числа (id, коды в скобках) — поэтому ищем
        ТОЛЬКО в хвосте строки ПОСЛЕ названия (после последней ']'), и берём
        ПЕРВОЕ 2–4-значное число (это и есть колонка «Сеть», далее идут нули
        статистики и цены вида '0,00%')."""
        import re
        text = row_text or ""
        tail = text[text.rfind("]") + 1:] if "]" in text else text
        nums = [n for n in re.findall(r"\b(\d{2,4})\b", tail) if n != str(offer_id)]
        return nums[0] if nums else None

    def find_offer_meta(self, product: str, geo_code: str) -> dict:
        """Ищет существующий оффер того же продукта для копирования
        партнёрской сети и скобки [ВЕРТИКАЛЬ-ГЕО].

        Логика ТЗ: фильтруем по названию продукта, берём оффер на ТО ЖЕ гео;
        если такого нет — последний найденный. Возвращает
        {network, bracket, source_name} (значения могут быть None).
        """
        self.login()
        self._open_offers()
        self._apply_grid_filter(product)

        # НАДЁЖНО дожидаемся, пока грид отфильтруется и появятся строки офферов
        # (фикс гонки: фиксированный таймаут не гарантировал загрузку → 0 строк
        # → ложное «оффер не найден»). Ждём ссылку на редактор оффера.
        try:
            self.page.wait_for_selector('a[href*="/editor/offer/"]', timeout=15_000)
        except PWTimeout:
            log.warning("Офферы продукта '%s' не появились в гриде", product)
            return {"network": None, "bracket": None, "source_name": None}
        # Доп. settle: дать догрузиться результатам по ПОЛНОМУ запросу (грид
        # сперва показывает совпадения по неполному вводу — можно взять чужую сеть).
        self.page.wait_for_timeout(self._GRID_SETTLE_MS)

        # Один JS-проход (без поштучного inner_text — он зависал на 45с при
        # виртуализированном гриде с сотнями строк).
        texts = [t for _href, t in self._grid_rows_data() if t]
        if not texts:
            log.warning("Офферы продукта '%s' не найдены — нечего копировать", product)
            return {"network": None, "bracket": None, "source_name": None}

        geo_up = (geo_code or "").upper()
        chosen_text = None
        # Предпочитаем строку с нужным гео-кодом в названии.
        for t in texts:
            if geo_up and re.search(rf"\b{re.escape(geo_up)}\b", t.upper()):
                chosen_text = t
                break
        if chosen_text is None:
            chosen_text = texts[-1]  # фолбэк: последний

        name_line = self._extract_offer_name(chosen_text, "")
        return {
            "network": self._extract_network_number(chosen_text),
            "bracket": self._bracket_vertical_geo(name_line),
            "source_name": name_line,
        }

    # ── модалка создания ─────────────────────────────────────────
    def _open_create_offer_modal(self) -> None:
        self.login()
        self._open_offers()
        # СЕЛЕКТОР? Кнопка «Создать» над гридом офферов.
        for sel in ('[data-test-id="create-button"]',
                    'button:has-text("Создать")',
                    'a:has-text("Создать")'):
            try:
                self.page.locator(sel).first.click(timeout=5_000)
                break
            except PWTimeout:
                continue
        else:
            self._dump_debug("create-button-not-found")
            raise KeitaroError("Не найдена кнопка «Создать» оффер")
        # Дождаться модалки «Создание оффера».
        self.page.wait_for_selector('text=Создание оффера', timeout=10_000)
        self._shot("01-modal-open")

    def _select_group(self, group: str) -> None:
        """Открывает дропдаун «Группа», вводит название и выбирает ТОЧНУЮ опцию.

        Это и есть проверка существования группы: если опции с точным текстом
        нет — заливка прерывается (группа должна существовать заранее).
        Группа в дропдауне может быть как публичной ('VI Visiowell GT'), так и
        приватной с префиксом баера ('AVP VI Visiowell GT') — берём ТОЧНОЕ имя.
        """
        page = self.page
        # Контрол группы — кастомный react-select с якорем data-test-id="groups-select".
        try:
            page.locator('[data-test-id="groups-select"]').first.click(timeout=6_000)
        except Exception:  # noqa: BLE001
            self._dump_debug("group-open")
            raise KeitaroError("Не удалось открыть дропдаун группы")

        # Ввод названия в активный input react-select сужает список опций.
        page.keyboard.type(group)
        page.wait_for_timeout(1500)

        # Опции react-select — элементы с id*="option". Кликаем ту, чей текст
        # ТОЧНО равен group (чтобы 'VI Visiowell GT' не спутать с 'AVP VI ...').
        opts = page.locator('[id*="option"]')
        try:
            opts.first.wait_for(state="visible", timeout=6_000)
        except PWTimeout:
            self._dump_debug("group-no-options")
            raise KeitaroError(
                f"Группа '{group}' не найдена в списке Keitaro — заливка прервана")
        n = opts.count()
        target = group.strip().lower()
        for i in range(n):
            if (opts.nth(i).inner_text() or "").strip().lower() == target:
                opts.nth(i).click()
                self.page.wait_for_timeout(300)
                return
        # Точного совпадения нет (есть только приватные с префиксом баера и т.п.).
        self._dump_debug("group-select")
        raise KeitaroError(
            f"Группа '{group}' не найдена в списке Keitaro "
            f"(есть: {[(opts.nth(i).inner_text() or '').strip() for i in range(min(n, 6))]}) "
            f"— заливка прервана")

    def _select_country(self, country_query: str, country_name: str = "") -> None:
        """Выбирает шаблон страны в react-select [data-test-id="country-select"].

        Keitaro фильтрует страны и по коду, и по названию. Вводим код (mx/cz/hu),
        затем выбираем опцию, ТЕКСТ которой содержит код или название страны —
        а не вслепую первую (для 'cz' первая опция могла оказаться не Чехией).
        Если по коду опций нет — пробуем ввести название страны.
        """
        page = self.page
        code = (country_query or "").strip().upper()
        name_low = (country_name or "").strip().lower()

        def _pick(queries: list[str]) -> bool:
            for q in queries:
                if not q:
                    continue
                try:
                    page.locator('[data-test-id="country-select"]').first.click()
                except Exception:  # noqa: BLE001
                    pass
                # очистить ввод и ввести запрос
                try:
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                except Exception:  # noqa: BLE001
                    pass
                page.keyboard.type(q)
                page.wait_for_timeout(1200)
                opts = page.locator('[id*="option"]')
                try:
                    opts.first.wait_for(state="visible", timeout=4_000)
                except PWTimeout:
                    continue
                n = opts.count()
                # предпочитаем опцию, где есть код страны или её название
                for i in range(n):
                    t = (opts.nth(i).inner_text() or "").strip()
                    tl = t.lower()
                    if (code and re.search(rf"\b{re.escape(code)}\b", t.upper())) \
                            or (name_low and name_low in tl):
                        opts.nth(i).click()
                        page.wait_for_timeout(300)
                        return True
                # код/имя не нашли в тексте — берём первую (как раньше)
                opts.first.click()
                page.wait_for_timeout(300)
                return True
            return False

        # вкладка «Настройки» → шаблон страны
        try:
            page.get_by_text("Настройки", exact=True).first.click()
            page.wait_for_timeout(600)
        except Exception:  # noqa: BLE001
            pass

        if not _pick([country_query, country_name, code]):
            self._dump_debug("country-template")
            raise KeitaroError(
                f"Не удалось выбрать шаблон страны '{country_query}'/'{country_name}'")

    def create_offer(
        self,
        *,
        name: str,
        group: str,
        network: Optional[str],
        zip_path: str | Path,
        country_query: str,
        country_name: str = "",
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Создаёт оффер по шагам ТЗ. Оффер создаётся, но НЕ переименовывается.

        Возвращает кандидатов на id для ПОДТВЕРЖДЕНИЯ пользователем
        {best, confident, candidates} (см. find_offer_id_candidates) — авто-выбор
        id опасен (можно переименовать чужой оффер).
        on_progress(msg) — колбэк прогресса для отображения шагов в UI.
        """
        import re
        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise KeitaroError(f"ZIP не найден: {zip_path}")

        def _step(msg: str) -> None:
            log.info("create_offer: %s", msg)
            if on_progress:
                try:
                    on_progress(msg)
                except Exception:  # noqa: BLE001
                    pass

        # Существование группы проверяется при выборе в дропдауне (см. _select_group).
        _step("Открываю модалку создания оффера")
        self._open_create_offer_modal()
        page = self.page

        # 1) Название
        _step(f"Заполняю название: {name}")
        name_input = page.get_by_label("Название", exact=False)
        try:
            name_input.first.fill(name, timeout=5_000)
        except Exception:  # noqa: BLE001
            page.locator('input[name="name"]').first.fill(name)
        self._shot("02-name")

        # 2) Группа — выбрать ТОЧНУЮ опцию из дропдауна (он же проверка наличия).
        _step(f"Выбираю группу: {group}")
        self._select_group(group)
        self._shot("03-group")

        # 3) Партнёрская сеть (число, напр. 75) — НАТИВНЫЙ
        # <select data-test-id="offers-affiliate-network-select">. Выбираем по
        # label = номер сети.
        if network:
            _step(f"Выбираю партнёрскую сеть: {network}")
            try:
                page.locator('[data-test-id="offers-affiliate-network-select"]').first.select_option(label=str(network))
            except Exception:  # noqa: BLE001
                log.warning("Не удалось выбрать партнёрскую сеть %s — выбери вручную", network)
                self._shot("04-network-fail")
        self._shot("04-network")

        # 4) ZIP. По умолчанию активна вкладка «Редирект» (external) —
        # переключаем на «Локальный» через dispatch_event('click') (Angular
        # uib-btn-radio). Появляется input[type=file].
        _step("Загружаю ZIP-архив (вкладка «Локальный»)")
        try:
            page.locator('[data-test-id="local-button-group-item"]').first.dispatch_event("click")
            page.wait_for_timeout(800)
            page.locator('input[type="file"]').first.wait_for(state="attached", timeout=6_000)
            page.locator('input[type="file"]').first.set_input_files(str(zip_path))
        except Exception:  # noqa: BLE001
            self._dump_debug("zip-upload")
            raise KeitaroError("Не удалось загрузить ZIP-архив")
        # Архив грузится на сервер (XHR) — дожидаемся завершения, иначе «Создать»
        # сработает с ошибкой «загрузите файл». Ждём появления имени файла /
        # исчезновения прогресса; фолбэк — увеличенная пауза.
        try:
            page.wait_for_function(
                """() => {
                    const t = document.body.innerText || '';
                    const uploading = /загрузк|upload|%/i.test(
                        (document.querySelector('.progress, [class*=progress]')||{}).textContent||'');
                    return !uploading && /\\.zip/i.test(t);
                }""",
                timeout=20_000,
            )
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(4000)  # фолбэк, если эвристика не сработала
        self._shot("05-zip")

        # 5) Вкладка «Настройки» → шаблон страны. Страна — react-select
        # data-test-id="country-select": клик → ввод → выбор ПОДХОДЯЩЕЙ опции.
        _step(f"Выбираю страну: {country_name or country_query.upper()}")
        self._select_country(country_query, country_name)
        self._shot("06-country")

        # 6) Создать — финальная зелёная кнопка модалки. data-test-id="success-button".
        _step("Нажимаю «Создать»")
        page.locator('[data-test-id="success-button"]').first.click()
        page.wait_for_timeout(1000)
        self._shot("07-created")

        # 6b) ПРОВЕРКА что оффер реально создан: модалка «Создание оффера» должна
        # закрыться. Если осталась — форма не отправилась (валидация: не догрузился
        # ZIP, не выбрана страна/обяз. поле). РАНЬШЕ код сразу рапортовал «создан»,
        # хотя оффер не появлялся в гриде — теперь явная ошибка.
        # Заливка большого ZIP может идти долго → опрашиваем закрытие модалки
        # циклом: проверили → если открыта, ждём 2.5с → перепроверили (до 60с).
        _step("Проверяю, что оффер создан (модалка закрылась)")
        if not self._wait_modal_closed("Создание оффера", total_ms=60_000):
            errs = self._collect_modal_errors()
            self._dump_debug("create-not-submitted")
            self._shot("07-create-failed")
            raise KeitaroError(
                "Оффер НЕ создан: форма не отправилась (модалка осталась открыта). "
                + (f"Ошибки формы: {errs}" if errs else
                   "Проверь ZIP/страну/обязательные поля (см. storage/keitaro/upload-07-create-failed.png)."))
        page.wait_for_timeout(800)

        # 7) Собираем КАНДИДАТОВ на id (НЕ переименовываем — это делает
        # пользователь после подтверждения; авто-выбор опасен, см. инцидент с
        # переименованием чужого старого оффера id=6506).
        _step("Ищу созданный оффер для подтверждения id")
        product = name.split("[")[0].strip()
        return self.find_offer_id_candidates(name, product)

    def _wait_modal_closed(self, title: str, *, total_ms: int = 60_000,
                           poll_ms: int = 2_500) -> bool:
        """Опрашивает закрытие модалки `title` циклом: проверили её видимость,
        если ещё открыта — ждём poll_ms и перепроверяем, пока не закроется или
        не истечёт total_ms. Возвращает True, если модалка закрылась.

        Нужно для медленной заливки ZIP: модалка «Создание оффера» висит, пока
        идёт загрузка/сохранение — нельзя сразу бежать переименовывать (оффер
        ещё не появился в гриде → «не найден ленд для переименования»).
        """
        page = self.page
        sel = f"text={title}"
        deadline = time.monotonic() + total_ms / 1000.0
        while True:
            try:
                vis = page.locator(sel).first.is_visible()
            except Exception:  # noqa: BLE001
                vis = False
            if not vis:
                return True
            if time.monotonic() >= deadline:
                return False
            page.wait_for_timeout(poll_ms)

    def _collect_modal_errors(self) -> str:
        """Собирает видимые сообщения валидации из открытой модалки (для понятной
        ошибки, когда «Создать» не сработала)."""
        page = self.page
        msgs: list[str] = []
        for sel in (".help-block", ".alert-danger", ".alert", ".has-error",
                    "[class*='error']", ".ng-invalid + .help-block"):
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 8)):
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    t = " ".join((el.inner_text() or "").split()).strip()
                    if t and len(t) < 200 and t not in msgs:
                        msgs.append(t)
            except Exception:  # noqa: BLE001
                continue
        return " | ".join(msgs[:5])

    def find_offer_id_candidates(self, name: str, product: str = "") -> dict:
        """Кандидаты на id только что созданного оффера (только GET-чтение).

        Возвращает {best, confident, candidates:[{id,name,has_id_prefix}]}:
          - exact-совпадение названия БЕЗ id-префикса → confident=True, best=max(exact);
          - иначе confident=False, best=None — пусть пользователь выберет id вручную.
        НИКОГДА не выбирает оффер сам при неуверенности (защита от переименования
        чужого оффера). Кандидаты отсортированы по id убыв. (свежие сверху).
        """
        self.login()
        self._open_offers()
        needle = (product or name.split("[")[0]).strip()

        def _norm(s: str) -> str:
            return " ".join((s or "").split()).strip().lower()

        target = _norm(name)

        # Грид может не сразу проиндексировать только что созданный оффер (заливка
        # ZIP). Поэтому делаем несколько проходов: фильтр → чтение строк; если
        # точного совпадения ещё нет — ждём и перечитываем (до 3 попыток).
        candidates: list[dict] = []
        exact: list[int] = []
        for attempt in range(3):
            self._apply_grid_filter(needle)
            try:
                self.page.wait_for_selector('a[href*="/editor/offer/"]', timeout=15_000)
            except PWTimeout:
                if attempt < 2:
                    self.page.wait_for_timeout(2_500)
                    continue
                return {"best": None, "confident": False, "candidates": []}
            self.page.wait_for_timeout(self._GRID_SETTLE_MS)

            candidates = []
            exact = []
            for href, text in self._grid_rows_data():
                m = re.search(r"/editor/offer/(\d+)", href or "")
                if not m:
                    continue
                oid = int(m.group(1))
                name_line = self._extract_offer_name(text or "", oid)
                has_prefix = bool(re.match(r"^\s*\d{2,7}\b", name_line))
                candidates.append({"id": oid, "name": name_line.strip(),
                                   "has_id_prefix": has_prefix})
                if not has_prefix and _norm(name_line) == target:
                    exact.append(oid)
            # Нашли точное совпадение — оффер уже в гриде, выходим.
            if exact:
                break
            # Иначе, если ещё есть попытки, ждём появления свежесозданного оффера.
            if attempt < 2:
                self.page.wait_for_timeout(2_500)

        candidates.sort(key=lambda c: c["id"], reverse=True)
        best = max(exact) if exact else None
        return {"best": best, "confident": bool(exact), "candidates": candidates}

    def _grid_rows_data(self) -> list[tuple[str, str]]:
        """Снимает все строки грида ОДНИМ JS-проходом → [(href_оффера, текст_строки)].

        Поштучный обход `rows.nth(i).inner_text()` зависал на 45с (грид
        виртуализирован — дальние строки не материализованы). evaluate_all берёт
        всё, что есть в DOM, за один вызов без per-row ожиданий."""
        try:
            return self.page.locator("tr.grid-tbody-row").evaluate_all(
                """rows => rows.map(r => {
                    const a = r.querySelector('a[href*="/editor/offer/"]');
                    return [a ? a.getAttribute('href') : '', r.innerText || ''];
                })"""
            )
        except Exception:  # noqa: BLE001
            return []

    def rename_offer(self, offer_id: int | str, new_name: str, *,
                     country_query: str = "", country_name: str = "") -> None:
        """Переименовывает оффер (дописывает id в название).

        Механика (по UI Keitaro): клик по НАЗВАНИЮ оффера в гриде открывает
        модалку «Редактирование оффера» (та же структура, что создание) →
        поле «Название» → зелёная «Сохранить» (data-test-id="success-button").

        country_query/country_name — для переподтверждения страны (модалка при
        быстром сохранении сбрасывала её в «Неизвестно»).
        """
        self.login()
        self._open_offers()
        page = self.page
        filt = page.locator(self._GRID_FILTER).first
        filt.wait_for(state="visible", timeout=10_000)
        filt.fill("")
        filt.fill(str(offer_id))
        try:
            page.wait_for_selector(f'a[href$="/editor/offer/{offer_id}"]', timeout=15_000)
        except PWTimeout:
            self._dump_debug("rename-offer-notfound")
            raise KeitaroError(f"Оффер {offer_id} не найден для переименования")
        page.wait_for_timeout(500)

        row = page.locator(
            f'tr.grid-tbody-row:has(a[href$="/editor/offer/{offer_id}"])').first
        # Модалку редактирования открывает клик по ЯЧЕЙКЕ НАЗВАНИЯ (td), а НЕ по
        # иконке «Редактировать» (она не реагирует) и не по ссылке (название — не <a>).
        # Ячейка названия — td со скобкой '[' (напр. '[VISION-GT]').
        tds = row.locator("td")
        name_td = None
        best = -1
        for i in range(tds.count()):
            txt = (tds.nth(i).inner_text() or "").strip()
            if "[" in txt and len(txt) > best:
                best = len(txt)
                name_td = tds.nth(i)
        if name_td is None:
            self._dump_debug("rename-no-name-td")
            raise KeitaroError(f"Не нашёл ячейку-название оффера {offer_id}")
        name_td.click()

        try:
            page.wait_for_selector('text=Редактирование оффера', timeout=10_000)
            inp = page.get_by_label("Название", exact=False).first
            inp.wait_for(state="visible", timeout=6_000)

            # ВАЖНО: модалка подгружает данные оффера АСИНХРОННО. Если сразу
            # перезаписать название и сохранить — ещё не загруженные поля (страна!)
            # уйдут пустыми → страна станет «Неизвестно». Ждём, пока поле
            # «Название» заполнится текущим значением оффера (= форма загрузилась).
            loaded = False
            for _ in range(50):  # до ~10с
                if (inp.input_value() or "").strip():
                    loaded = True
                    break
                page.wait_for_timeout(200)
            if not loaded:
                log.warning("rename_offer: форма не показала текущее название за 10с")
            page.wait_for_timeout(800)  # дать догрузиться стране и пр. полям

            inp.fill("")
            inp.fill(new_name)

            # Страховка: повторно выставляем страну (если форма всё же сбросила её),
            # чтобы сохранение не затёрло её на «Неизвестно». Best-effort.
            if country_query or country_name:
                try:
                    self._select_country(country_query, country_name)
                except Exception as e:  # noqa: BLE001
                    log.warning("rename_offer: не удалось переподтвердить страну: %s", e)

            self._shot("08-rename-filled")
            # Зелёная «Сохранить» — тот же data-test-id, что «Создать».
            page.locator('[data-test-id="success-button"]').first.click()
            page.wait_for_timeout(2000)
            self._shot("09-renamed")
        except Exception as e:  # noqa: BLE001
            self._dump_debug("rename-offer")
            raise KeitaroError(f"Не удалось переименовать оффер {offer_id}: {e}")

    # ── тестовая кампания для залитого ленда ─────────────────────
    def create_test_campaign(self, offer_id: int | str, offer_full_name: str, *,
                             group: str = "Andrei AM",
                             name_prefix: str = "test mch",
                             on_progress=None) -> str:
        """Создаёт тестовую кампанию для оффера и возвращает ссылку на неё.

        Флоу: #!/campaigns/ → «Создать» → имя «test mch <полное имя с id>» →
        группа (Andrei AM) → «Создать поток» → вкладка «Схема» →
        «Добавить офферы» (ждём готовности кнопки) → поиск по id → чекбокс →
        «Добавить» → «Применить» → «Создать» → copy-link → ссылка из буфера.
        """
        page = self.page

        def _step(msg: str) -> None:
            log.info("test_campaign[%s]: %s", offer_id, msg)
            if on_progress:
                try:
                    on_progress(msg)
                except Exception:  # noqa: BLE001
                    pass

        self.login()

        # Буфер обмена понадобится для copy-link (headless Chromium).
        try:
            origin = re.match(r"https?://[^/]+", self.base_url).group(0)
            self._ctx.grant_permissions(["clipboard-read", "clipboard-write"],
                                        origin=origin)
        except Exception:  # noqa: BLE001
            pass

        campaign_name = f"{name_prefix} {offer_full_name}".strip()

        try:
            # 1) Грид кампаний → «Создать»
            _step("открываю кампании")
            self._goto(f"{self.base_url}/#!/campaigns/")
            create_btn = page.locator('[data-test-id="create-button"]').first
            create_btn.wait_for(state="visible", timeout=20_000)
            create_btn.click()

            # 2) Страница создания: фокус уже в поле названия — печатаем имя.
            _step(f"ввожу название: {campaign_name}")
            page.wait_for_timeout(1500)
            page.keyboard.type(campaign_name, delay=15)
            # Проверяем, что имя реально попало в какой-то input; иначе — руками.
            ok_typed = page.evaluate(
                "(name) => [...document.querySelectorAll('input')]"
                ".some(i => (i.value || '').includes(name))",
                campaign_name)
            if not ok_typed:
                filled = False
                for sel in ('input[name="name"]', '[data-test-id="name-input"]',
                            'form input[type="text"]'):
                    try:
                        page.locator(sel).first.fill(campaign_name, timeout=4_000)
                        filled = True
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if not filled:
                    raise KeitaroError("Не нашёл поле названия кампании")

            # 3) Группа — всегда Andrei AM (тот же react-select, что у офферов).
            _step(f"выбираю группу: {group}")
            self._select_group(group)

            # 4) «Создать поток» → модалка
            _step("создаю поток")
            page.locator('button:has-text("Создать поток")').first.click(timeout=10_000)
            page.wait_for_selector('.modal-content', timeout=15_000)

            # 5) Вкладка «Схема»
            _step("вкладка «Схема»")
            page.locator('.modal-content a.nav-link:has-text("Схема")').first.click(timeout=10_000)

            # 6) «Добавить офферы» — кнопка может долго грузиться (disabled,
            # пока entity selector не инициализирован). Ждём готовности циклом,
            # чтобы отличать «ещё грузится» от «ошибка».
            _step("жду готовности кнопки «Добавить офферы»")
            add_btn = page.locator('[data-test-id="add-offer-button"]').first
            add_btn.wait_for(state="visible", timeout=20_000)
            waited = 0
            while add_btn.is_disabled():
                page.wait_for_timeout(1_500)
                waited += 1500
                if waited % 9_000 == 0:
                    _step(f"кнопка «Добавить офферы» ещё грузится ({waited // 1000}с)…")
                if waited >= 60_000:
                    self._dump_debug("campaign-add-offer-stuck")
                    raise KeitaroError(
                        "Кнопка «Добавить офферы» не стала доступной за 60с — "
                        "похоже на ошибку загрузки (см. debug-скриншот)")
            add_btn.click()

            # 7) Модалка «Офферы»: поиск по id → чекбокс строки → «Добавить»
            _step(f"ищу оффер {offer_id}")
            search = page.locator('.modal-content input[type="search"]').last
            search.wait_for(state="visible", timeout=15_000)
            search.fill(str(offer_id))
            row = page.locator(f'[data-test-id="row-{offer_id}"]').first
            try:
                row.wait_for(state="visible", timeout=20_000)
            except PWTimeout:
                self._dump_debug("campaign-offer-not-found")
                raise KeitaroError(
                    f"Оффер {offer_id} не найден в списке модалки «Офферы»")
            row.locator('input[type="checkbox"]').first.click()
            _step("добавляю оффер в поток")
            # success-button ИМЕННО этой модалки (последняя открытая).
            page.locator('.modal-content').last \
                .locator('[data-test-id="success-button"]').first.click(timeout=10_000)
            page.wait_for_timeout(800)

            # 8) «Применить» в модалке потока
            _step("применяю поток")
            page.locator('.modal-content button:has-text("Применить")').first.click(timeout=10_000)
            page.wait_for_timeout(1_200)

            # 9) «Создать» кампанию
            _step("создаю кампанию")
            save_btn = page.locator('[data-test-id="save-button"]').first
            save_btn.click(timeout=10_000)

            # 10) Ждём появления id кампании (copy-link разблокируется) → клик
            _step("жду ссылку кампании")
            copy_btn = page.locator('[data-test-id="copy-link-button"]').first
            copy_btn.wait_for(state="visible", timeout=30_000)
            waited = 0
            while copy_btn.is_disabled():
                page.wait_for_timeout(1_000)
                waited += 1000
                if waited >= 45_000:
                    self._dump_debug("campaign-no-id")
                    raise KeitaroError(
                        "Кампания не получила id за 45с — создание, похоже, не прошло")
            page.wait_for_timeout(1_000)  # «немного подождать» после создания
            copy_btn.click()
            page.wait_for_timeout(500)

            link = ""
            try:
                link = page.evaluate("navigator.clipboard.readText()") or ""
            except Exception:  # noqa: BLE001
                log.warning("Буфер обмена недоступен — ищу ссылку в DOM")
            if not link.startswith("http"):
                # Фолбэк: видимое поле/элемент со ссылкой кампании на странице.
                link = page.evaluate(
                    "() => { const i = [...document.querySelectorAll('input')]"
                    ".find(x => /^https?:\\/\\//.test(x.value || '') && !/admin/.test(x.value));"
                    " return i ? i.value : ''; }") or ""
            if not link.startswith("http"):
                self._dump_debug("campaign-no-link")
                raise KeitaroError(
                    "Кампания создана, но ссылку получить не удалось "
                    "(см. debug-скриншот)")
            self._shot("campaign-created")
            _step(f"готово: {link}")
            return link.strip()
        except KeitaroError:
            raise
        except Exception as e:  # noqa: BLE001
            self._dump_debug("campaign-create")
            raise KeitaroError(f"Не удалось создать тестовую кампанию: {e}")

    # ── debug ────────────────────────────────────────────────────
    def _dump_debug(self, tag: str) -> None:
        try:
            dbg = Path(__file__).resolve().parents[1] / "storage" / "keitaro"
            dbg.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(dbg / f"debug-{tag}.png"), full_page=True)
            (dbg / f"debug-{tag}.html").write_text(self.page.content(), encoding="utf-8")
            log.warning("Сохранён debug-снимок: %s", dbg / f"debug-{tag}.png")
        except Exception:  # noqa: BLE001
            pass


def client_from_env(**overrides) -> KeitaroClient:
    """Создаёт KeitaroClient из переменных окружения (.env).

    Сессия (cookies) сохраняется в storage/keitaro/session.json.
    """
    storage = Path(__file__).resolve().parents[1] / "storage" / "keitaro"
    kwargs = dict(
        base_url=os.environ.get("KEITARO_BASE_URL", "https://tlgk.host/admin"),
        username=os.environ.get("KEITARO_USERNAME", ""),
        password=os.environ.get("KEITARO_PASSWORD", ""),
        headless=os.environ.get("KEITARO_HEADLESS", "1") != "0",
        state_path=str(storage / "session.json"),
    )
    kwargs.update(overrides)
    return KeitaroClient(**kwargs)


# ── CLI: python -m connectors.keitaro <offer_id> ─────────────────
def _main() -> None:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass

    offer_id = sys.argv[1] if len(sys.argv) > 1 else "9224"
    storage = Path(__file__).resolve().parents[1] / "storage" / "keitaro"

    with client_from_env() as kt:
        print(f"→ Скачиваю оффер {offer_id} …")
        path = kt.download_offer(offer_id, storage / "downloads")
        print(f"✓ Готово: {path}  ({path.stat().st_size} байт)")


if __name__ == "__main__":
    _main()

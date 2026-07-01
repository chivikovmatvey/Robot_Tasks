"""
Утилиты для запуска существующих CLI-скриптов в веб-контексте:
- захват stdout/stderr во время вызова
- парсинг ANSI escape-кодов в семантические категории (ok/warn/error/info/dim)
"""
import io
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field


# ── ANSI escape → семантический "уровень" строки ────────────────
ANSI_RE = re.compile(r'\x1b\[[0-9;]*m|\[[0-9;]+m')

# Цветовые коды, которые используются в скриптах
COLOR_TO_LEVEL = {
    '\x1b[91m': 'error',     # красный
    '\x1b[92m': 'success',   # зелёный
    '\x1b[93m': 'warning',   # жёлтый
    '\x1b[94m': 'info',      # синий
    '\x1b[96m': 'section',   # циан
    '\x1b[2m':  'dim',       # тусклый
    '[91m':     'error',
    '[92m':     'success',
    '[93m':     'warning',
    '[94m':     'info',
    '[96m':     'section',
    '[2m':      'dim',
}


@dataclass
class LogLine:
    """Одна строка лога с семантическим уровнем."""
    text: str                          # очищенный от ANSI текст
    level: str = 'plain'               # plain | success | warning | error | info | section | dim


@dataclass
class CaptureResult:
    """Результат захвата вывода скрипта."""
    lines: list[LogLine] = field(default_factory=list)
    raw: str = ''

    def to_dicts(self) -> list[dict]:
        return [{'text': l.text, 'level': l.level} for l in self.lines]


def parse_ansi_line(line: str) -> LogLine:
    """Определяем уровень по первому встретившемуся цвету, чистим ANSI."""
    level = 'plain'

    # Ищем первый цветовой код — он обычно определяет смысл строки
    for code, lvl in COLOR_TO_LEVEL.items():
        if code in line:
            level = lvl
            break

    # Дополнительные эвристики по содержимому
    stripped_test = ANSI_RE.sub('', line)
    if level == 'plain':
        if stripped_test.strip().startswith(('✓', '+')):
            level = 'success'
        elif stripped_test.strip().startswith(('✗', 'x', 'ERROR')):
            level = 'error'
        elif stripped_test.strip().startswith(('!', 'WARN')):
            level = 'warning'
        elif stripped_test.strip().startswith(('→', '>')):
            level = 'info'
        elif stripped_test.strip().startswith('-'):
            level = 'dim'
        elif stripped_test.strip().startswith(('─', '═', '━')):
            level = 'section'

    text = ANSI_RE.sub('', line).rstrip()
    return LogLine(text=text, level=level)


@contextmanager
def capture_output():
    """Перехватываем stdout/stderr во время выполнения блока."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    buf = io.StringIO()
    sys.stdout = buf
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def run_with_capture(fn, *args, **kwargs) -> tuple[any, CaptureResult]:
    """
    Вызывает fn(*args, **kwargs) с захватом stdout, парсит результат в LogLine'ы.
    Возвращает (результат функции, CaptureResult).
    """
    with capture_output() as buf:
        try:
            result = fn(*args, **kwargs)
        except SystemExit as e:
            # Скрипты иногда зовут sys.exit() — превращаем в ошибку
            result = None
            print(f"\x1b[91m[SystemExit] {e}\x1b[0m")
        except Exception as e:
            result = None
            print(f"\x1b[91m[Error] {type(e).__name__}: {e}\x1b[0m")
            import traceback
            print(traceback.format_exc())

    raw = buf.getvalue()
    lines = [parse_ansi_line(line) for line in raw.splitlines() if line.strip()]
    return result, CaptureResult(lines=lines, raw=raw)

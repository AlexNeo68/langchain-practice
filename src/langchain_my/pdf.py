"""Экспорт ответа агента в PDF через headless Chrome.

Схема работы: markdown-ответ модели + список источников -> HTML с CSS ->
печать в PDF браузером (``--headless=new --print-to-pdf``).
"""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import markdown

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
)

CSS = """
@page {
  size: A4;
  margin: 20mm 18mm 18mm 18mm;
}

:root {
  color-scheme: light;
  --ink: #1c1f23;
  --muted: #6b7280;
  --rule: #e5e7eb;
  --accent: #2563eb;
  --accent-soft: #eff4ff;
}

* { box-sizing: border-box; }

html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }

body {
  margin: 0;
  font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
  font-size: 11pt;
  line-height: 1.6;
  color: var(--ink);
}

.masthead {
  border-bottom: 2px solid var(--ink);
  padding-bottom: 10px;
  margin-bottom: 20px;
}

.masthead h1 {
  font-size: 21pt;
  line-height: 1.2;
  margin: 0 0 6px;
  letter-spacing: -0.01em;
}

.masthead .meta {
  font-size: 9pt;
  color: var(--muted);
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}

.question {
  background: var(--accent-soft);
  border-left: 3px solid var(--accent);
  border-radius: 0 6px 6px 0;
  padding: 12px 16px;
  margin: 0 0 24px;
  break-inside: avoid;
}

.question .label {
  display: block;
  font-size: 8pt;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 4px;
}

.question p { margin: 0; font-size: 11pt; }

.answer > :first-child { margin-top: 0; }

.answer h2 {
  font-size: 14pt;
  margin: 22px 0 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--rule);
  break-after: avoid;
}

.answer h3 {
  font-size: 12pt;
  margin: 18px 0 6px;
  break-after: avoid;
}

.answer p { margin: 0 0 10px; }

.answer ul, .answer ol { margin: 0 0 12px; padding-left: 20px; }

.answer li { margin-bottom: 4px; }

.answer li > p { margin-bottom: 4px; }

.answer strong { font-weight: 600; }

.answer code {
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 9.5pt;
  background: #f3f4f6;
  padding: 0 4px;
  border-radius: 3px;
}

.answer pre {
  background: #f8f9fa;
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 12px 14px;
  overflow-x: auto;
  break-inside: avoid;
}

.answer pre code { background: none; padding: 0; font-size: 9pt; }

.answer blockquote {
  margin: 0 0 12px;
  padding-left: 14px;
  border-left: 3px solid var(--rule);
  color: #374151;
}

.answer a { color: var(--accent); text-decoration: none; word-break: break-word; }

.answer table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 14px;
  font-size: 10pt;
  break-inside: avoid;
}

.answer th, .answer td {
  border: 1px solid var(--rule);
  padding: 6px 9px;
  text-align: left;
  vertical-align: top;
}

.answer th { background: #f8f9fa; font-weight: 600; }

.sources-section {
  margin-top: 28px;
  padding-top: 16px;
  border-top: 1px solid var(--rule);
  break-inside: avoid-page;
}

.sources-section h2 {
  font-size: 12pt;
  margin: 0 0 10px;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  color: var(--muted);
}

ol.sources {
  margin: 0;
  padding-left: 18px;
  font-size: 10pt;
}

ol.sources li { margin-bottom: 7px; }

ol.sources a {
  color: var(--ink);
  font-weight: 600;
  text-decoration: none;
}

ol.sources .url {
  display: block;
  color: var(--muted);
  font-size: 8.5pt;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  word-break: break-all;
}

.empty { color: var(--muted); font-size: 10pt; margin: 0; }

.footer {
  margin-top: 26px;
  padding-top: 10px;
  border-top: 1px solid var(--rule);
  font-size: 8pt;
  color: var(--muted);
}
"""

HTML_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
  <header class="masthead">
    <h1>{title}</h1>
    <div class="meta">
      <span>{generated_at}</span>
      {model_line}
    </div>
  </header>

  <section class="question">
    <span class="label">Запрос</span>
    <p>{question}</p>
  </section>

  <article class="answer">
{answer}
  </article>

  <section class="sources-section">
    <h2>Источники</h2>
    {sources}
  </section>

  <footer class="footer">
    Отчёт сгенерирован агентом langchain-my
  </footer>
</body>
</html>
"""


class PdfExportError(RuntimeError):
    """PDF не удалось собрать."""


def find_browser() -> str:
    """Возвращает путь к Chrome-совместимому браузеру."""
    override = os.getenv("CHROME_BIN")
    if override:
        if Path(override).exists():
            return override
        msg = f"CHROME_BIN указывает на несуществующий файл: {override}"
        raise PdfExportError(msg)

    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate

    msg = (
        "Не найден Chrome/Chromium для печати PDF. "
        "Установи Google Chrome или задай путь в переменной окружения CHROME_BIN."
    )
    raise PdfExportError(msg)


def _escape(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _render_sources(sources: list[Any]) -> str:
    """Собирает список источников без дублей, с человекочитаемыми доменами."""
    seen: set[str] = set()
    items: list[str] = []

    for source in sources:
        url = getattr(source, "url", None)
        if url is None and isinstance(source, dict):
            url = source.get("url")
        if not url:
            continue
        url = str(url).strip()
        if url in seen:
            continue
        seen.add(url)

        host = urlparse(url).netloc.removeprefix("www.") or url
        items.append(
            f'<li><a href="{_escape(url)}">{_escape(host)}</a>'
            f'<span class="url">{_escape(url)}</span></li>'
        )

    if not items:
        return '<p class="empty">Модель не указала источники.</p>'
    return '<ol class="sources">' + "".join(items) + "</ol>"


def build_html(
    *,
    question: str,
    answer: str,
    sources: list[Any],
    title: str = "Отчёт агента",
    model_name: str | None = None,
) -> str:
    """Собирает HTML-документ отчёта."""
    answer_html = markdown.markdown(
        answer,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
    )
    model_line = f"<span>Модель: {_escape(model_name)}</span>" if model_name else ""

    return HTML_TEMPLATE.format(
        title=_escape(title),
        css=CSS,
        generated_at=datetime.now().strftime("%d.%m.%Y, %H:%M"),
        model_line=model_line,
        question=_escape(question),
        answer=answer_html,
        sources=_render_sources(sources),
    )


def render_pdf(html_doc: str, out_path: str | Path) -> Path:
    """Печатает HTML в PDF headless-браузером и возвращает путь к файлу."""
    browser = find_browser()
    out_path = Path(out_path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="agent-report-") as tmp:
        tmp_dir = Path(tmp)
        html_file = tmp_dir / "report.html"
        html_file.write_text(html_doc, encoding="utf-8")

        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            # Колонтитулы Chrome рисует своим шрифтом и без кириллицы — отключаем.
            "--no-pdf-header-footer",
            "--virtual-time-budget=3000",
            f"--user-data-dir={tmp_dir / 'profile'}",
            f"--print-to-pdf={out_path}",
            html_file.as_uri(),
        ]

        try:
            subprocess.run(command, capture_output=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            msg = "Браузер не успел отрендерить PDF за 120 секунд."
            raise PdfExportError(msg) from exc

    if not out_path.exists() or out_path.stat().st_size == 0:
        msg = f"Браузер не создал PDF: {out_path}"
        raise PdfExportError(msg)

    with out_path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            msg = f"Файл не похож на PDF: {out_path}"
            raise PdfExportError(msg)

    return out_path


def save_report(
    *,
    question: str,
    response: Any,
    model_name: str | None = None,
    out_dir: str | Path = "output",
    filename: str | None = None,
    title: str = "Отчёт агента",
) -> Path:
    """Сохраняет ответ агента (`AgentResponse`) в PDF и возвращает путь.

    `response` — объект с полями `answer` и `sources` либо словарь с теми же ключами.
    """
    if isinstance(response, dict):
        answer = response.get("answer") or ""
        sources = list(response.get("sources") or [])
    else:
        answer = getattr(response, "answer", "") or ""
        sources = list(getattr(response, "sources", None) or [])

    html_doc = build_html(
        question=question,
        answer=str(answer),
        sources=sources,
        title=title,
        model_name=model_name,
    )

    name = filename or f"report-{datetime.now():%Y-%m-%d_%H-%M-%S}.pdf"
    return render_pdf(html_doc, Path(out_dir) / name)

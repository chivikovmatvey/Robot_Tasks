"""Split-text and split-price replacement helpers."""

import re
from bs4 import BeautifulSoup

from ._groups import collect_all_groups, collect_inline_groups, get_node_text, NO_TEXT_REPLACE_TAGS
from ._primitives import replace_outside_attrs
from bs4 import NavigableString, Tag

_INVISIBLE_RE = re.compile(r'[\u200b\u200c\u200d\ufeff\u00ad\u200e\u200f]')


def vis_normalize(text: str) -> str:
    """Убирает невидимые символы и неразрывные пробелы."""
    return _INVISIBLE_RE.sub('', text).replace('\xa0', ' ').replace(' ', '')


def replace_split_in_raw(raw_html: str, involved_texts: list[str],
                         replace_str: str) -> tuple[str, int]:
    """
    Заменяет split-текст: ['Dia', 'beet'] → 'Diabarol'
    Весь replacement кладётся в первый узел, остальные очищаются.
    """
    if len(involved_texts) < 2:
        return raw_html, 0

    first_text = involved_texts[0]
    last_text = involved_texts[-1]

    pattern = (
        re.escape(first_text)
        + r'(</[^>]*>.*?<[^>]*>)'
        + re.escape(last_text)
    )
    replacement = replace_str + r'\1'
    new_html, n = re.subn(pattern, replacement, raw_html, count=1, flags=re.DOTALL)
    return new_html, n


def replace_split_text(raw_html: str, soup: BeautifulSoup,
                       find_str: str, replace_str: str) -> tuple[str, int]:
    """
    Замена текста разорванного по inline-тегам.
    <span>Dia</span><span>beet</span> → <span>Diabarol</span><span></span>
    """
    groups = collect_all_groups(soup)
    count = 0
    find_norm = vis_normalize(find_str)

    for group in groups:
        group_norm = vis_normalize(group.text)
        idx = group_norm.find(find_norm)
        if idx == -1:
            continue

        match_start = group.text.find(find_str)
        if match_start == -1:
            match_start = -1
            match_end = -1
            fpos = 0
            for ci, ch in enumerate(group.text):
                ch_norm = vis_normalize(ch)
                if not ch_norm:
                    continue
                if fpos < len(find_norm) and ch_norm == find_norm[fpos]:
                    if fpos == 0:
                        match_start = ci
                    fpos += 1
                    if fpos == len(find_norm):
                        match_end = ci + 1
                        break
                elif ch == ' ':
                    continue
                else:
                    fpos = 0
                    match_start = -1
            if match_start == -1 or match_end == -1:
                continue
        else:
            match_end = match_start + len(find_str)

        involved = []
        for node, n_start, n_end in group.nodes:
            overlap_start = max(n_start, match_start)
            overlap_end = min(n_end, match_end)
            if overlap_start < overlap_end:
                node_text = get_node_text(node)
                if node_text.strip():
                    involved.append((node, node_text))

        if not involved:
            continue

        if len(involved) >= 2:
            involved_texts = [t for _, t in involved]
            first_clean = vis_normalize(involved_texts[0])
            last_clean = vis_normalize(involved_texts[-1])
            if find_norm.startswith(first_clean) and find_norm.endswith(last_clean):
                raw_html, n = replace_split_in_raw(raw_html, involved_texts, replace_str)
                if n:
                    count += n

        elif len(involved) == 1:
            node = involved[0][0]
            if isinstance(node, Tag):
                inner_groups = collect_inline_groups(node)
                for ig in inner_groups:
                    ig_norm = vis_normalize(ig.text)
                    if find_norm not in ig_norm:
                        continue
                    inner_involved = []
                    for inode, _, _ in ig.nodes:
                        itext = get_node_text(inode)
                        if itext.strip():
                            inner_involved.append((inode, itext))
                    if len(inner_involved) >= 2:
                        inner_texts = [t for _, t in inner_involved]
                        first_clean = vis_normalize(inner_texts[0])
                        last_clean = vis_normalize(inner_texts[-1])
                        if find_norm.startswith(first_clean) and find_norm.endswith(last_clean):
                            raw_html, n = replace_split_in_raw(raw_html, inner_texts, replace_str)
                            if n:
                                count += n

    return raw_html, count


def _split_price_parts(price_str: str) -> tuple[str, str] | None:
    """'590 MXN' → ('590', 'MXN')"""
    m = re.match(r'^([^\d]*)([\d.,\s]+)(.*)$', price_str.strip())
    if not m:
        return None
    prefix = m.group(1).strip()
    number = m.group(2).strip()
    suffix = m.group(3).strip()
    currency = prefix or suffix
    return (number, currency)


def replace_split_price(raw_html: str, soup: BeautifulSoup,
                        old_price: str, new_price: str) -> tuple[str, int]:
    """
    Замена цены разорванной по тегам.
    <span>2490</span><span>INR</span> → <span>39</span><span>EUR</span>
    """
    old_parts = _split_price_parts(old_price)
    new_parts = _split_price_parts(new_price)

    if not old_parts or not new_parts:
        return raw_html, 0

    old_num, old_cur = old_parts
    new_num, new_cur = new_parts
    count = 0

    if old_num and new_num:
        raw_html, n = replace_outside_attrs(raw_html, old_num, new_num)
        count += n

    if old_cur and new_cur and old_cur != new_cur:
        raw_html, n = replace_outside_attrs(raw_html, old_cur, new_cur)
        count += n

    return raw_html, count

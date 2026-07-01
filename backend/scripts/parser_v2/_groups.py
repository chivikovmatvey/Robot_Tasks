"""Visual inline-group detection via BeautifulSoup DOM."""

from bs4 import NavigableString, Tag

INLINE_TAGS = frozenset({
    'span', 'b', 'i', 'em', 'strong', 'small', 'a',
    'sub', 'sup', 'u', 'mark', 'abbr', 'code', 'font',
})

NO_TEXT_REPLACE_TAGS = frozenset({'script', 'style', 'noscript'})


class VisualGroup:
    """Группа соседних inline-узлов с общим визуальным текстом."""
    __slots__ = ('text', 'nodes')

    def __init__(self):
        self.text = ''
        self.nodes = []  # list of (node, start_in_text, end_in_text)


def collect_inline_groups(parent) -> list[VisualGroup]:
    groups: list[VisualGroup] = []
    current = VisualGroup()

    def _flush():
        nonlocal current
        if current.text.strip():
            groups.append(current)
        current = VisualGroup()

    for child in parent.children:
        if isinstance(child, NavigableString):
            txt = str(child)
            if not txt.strip() and not current.text:
                continue
            start = len(current.text)
            current.text += txt
            current.nodes.append((child, start, start + len(txt)))
        elif isinstance(child, Tag):
            if child.name in NO_TEXT_REPLACE_TAGS:
                _flush()
                continue
            if child.name in INLINE_TAGS:
                inner = child.get_text()
                start = len(current.text)
                current.text += inner
                current.nodes.append((child, start, start + len(inner)))
            else:
                _flush()
                groups.extend(collect_inline_groups(child))

    _flush()
    return groups


def collect_all_groups(soup) -> list[VisualGroup]:
    groups: list[VisualGroup] = []
    _walk(soup, groups)
    return groups


def _walk(node, groups: list[VisualGroup]):
    if isinstance(node, NavigableString):
        return

    has_inline = False
    for child in getattr(node, 'children', []):
        if isinstance(child, NavigableString) and child.strip():
            has_inline = True
            break
        if isinstance(child, Tag) and child.name in INLINE_TAGS:
            has_inline = True
            break

    if has_inline and (not isinstance(node, Tag) or node.name not in NO_TEXT_REPLACE_TAGS):
        groups.extend(collect_inline_groups(node))

    for child in getattr(node, 'children', []):
        if isinstance(child, Tag) and child.name not in NO_TEXT_REPLACE_TAGS:
            _walk(child, groups)


def get_node_text(node) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    elif isinstance(node, Tag):
        return node.get_text()
    return ''

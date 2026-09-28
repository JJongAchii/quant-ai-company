"""Small bounded HTML tree for the official, server-rendered announcement tables."""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def find(self, tag=None, **attrs):
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if (tag is None or child.tag == tag) and all(child.attrs.get(k) == v for k, v in attrs.items()):
                yield child
            yield from child.find(tag, **attrs)

    def text(self):
        if self.tag in {"script", "style"}:
            return ""
        return re.sub(r"\s+", " ", " ".join(c.text() if isinstance(c, Node) else c
                                            for c in self.children)).strip()

    def cells(self):
        return [c for c in self.children if isinstance(c, Node) and c.tag in {"td", "th"}]


class Tree(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.count = 0
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 50000 or len(self.stack) > 100:
            raise ValueError("housing_html_limit")
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(body):
    return Tree(body).root

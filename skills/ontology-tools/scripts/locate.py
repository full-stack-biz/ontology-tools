#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib"]
# ///
"""
Positions in a Turtle file: where each term is written, which section it is in, and where a
new term goes. Use it instead of grep to read or edit a TTL file.

Usage:
  locate.py header  <file.ttl>                     prefixes, ontology IRI, owl:versionInfo,
                                                   and the comments around them
  locate.py outline <file.ttl>                     banner sections: title, lines, term count
  locate.py where   <file.ttl> <term> [<term>...]  each term's line range and section
  locate.py text    <file.ttl> <regex> [--prop P]  terms with a literal that matches (case-
                                                   insensitive), with the matching snippet
  locate.py anchor  <file.ttl> --type T [--prefix P] [--section S]
                                                   the last term of type T (and prefix P, in
                                                   section S): the line to insert a new one after

A term is a qname (wash:Payment), a full IRI, or a ttl://file.ttl/prefix:Name reference.
A section starts at a banner (a comment line of ═ or = characters) followed by a comment line
with its title; it ends where the next one starts. Line numbers are 1-based, so
`Read offset=<start> limit=<end-start+1>` reads exactly the block.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import rdflib
from rdflib import RDF

BANNER = re.compile(r"^#\s*[═=]{10,}\s*$")


@dataclass
class Statement:
    subject: str  # the first token, as written: a qname, <iri> or @prefix
    start: int  # 1-based line of the first token
    end: int  # 1-based line of the closing '.'


@dataclass
class Section:
    title: str
    start: int
    end: int = 0
    statements: list = field(default_factory=list)


def scan(text: str) -> list[Statement]:
    """Statements of a Turtle document with their lines: strings, IRIs and comments are
    skipped, and a '.' ends a statement when it is outside [] and () and followed by
    whitespace, a comment or the end of the file."""
    statements = []
    i, n, line = 0, len(text), 1
    depth = 0
    current = None  # (subject, start)

    def token_at(j):
        m = re.match(r"(@prefix|@base|PREFIX|BASE|<[^>]*>|[^\s;,.()\[\]]+(?:\.[^\s;,.()\[\]]+)*)", text[j:])
        return m.group(1) if m else text[j]

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith('"""', i) or text.startswith("'''", i):
            q = text[i : i + 3]
            j = text.find(q, i + 3)
            j = n if j < 0 else j + 3
            line += text.count("\n", i, j)
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
        elif c == "<" and current is not None:
            j = text.find(">", i)
            i = n if j < 0 else j + 1
        else:
            if current is None:
                current = (token_at(i), line)
                if current[0].startswith("<"):
                    i = text.find(">", i) + 1
                    continue
            if c in "[(":
                depth += 1
            elif c in "])":
                depth -= 1
            elif c == "." and depth == 0 and (i + 1 >= n or text[i + 1] in " \t\r\n#"):
                statements.append(Statement(current[0], current[1], line))
                current = None
            i += 1
    return statements


def is_directive(s: Statement) -> bool:
    return s.subject.startswith("@") or s.subject.upper() in ("PREFIX", "BASE")


def sections(lines: list[str], statements: list[Statement]) -> list[Section]:
    found = []
    k = 0
    while k < len(lines):
        if BANNER.match(lines[k]) and k + 1 < len(lines):
            title_line = lines[k + 1].strip()
            if title_line.startswith("#") and not BANNER.match(title_line):
                title = title_line.lstrip("#").strip()
                found.append(Section(title or "(untitled)", k + 1))
                # skip to the closing banner of this header
                k += 2
                while k < len(lines) and lines[k].startswith("#") and not BANNER.match(lines[k]):
                    k += 1
        k += 1
    if not found or found[0].start > 1:
        found.insert(0, Section("(before the first section)", 1))
    for a, b in zip(found, found[1:]):
        a.end = b.start - 1
    found[-1].end = len(lines)
    for s in statements:
        if is_directive(s):
            continue
        for sec in found:
            if sec.start <= s.start <= sec.end:
                sec.statements.append(s)
                break
    return found


def section_of(secs: list[Section], line: int) -> str:
    for sec in secs:
        if sec.start <= line <= sec.end:
            return sec.title
    return "?"


def load(path: str):
    p = Path(path)
    if not p.exists():
        sys.exit(f"Error: file not found: {path}")
    text = p.read_text(encoding="utf-8")
    g = rdflib.Graph()
    try:
        g.parse(p, format="turtle")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Error: parse failed: {e}")
    statements = scan(text)
    return text, text.splitlines(), g, statements, sections(text.splitlines(), statements)


def qname(g, uri) -> str:
    try:
        return g.namespace_manager.normalizeUri(uri)
    except Exception:  # noqa: BLE001
        return f"<{uri}>"


def expand(g, term: str):
    term = re.sub(r"^ttl://[^/]+/", "", term)
    if term.startswith("<") and term.endswith(">"):
        return rdflib.URIRef(term[1:-1])
    if "://" in term:
        return rdflib.URIRef(term)
    prefix, _, name = term.partition(":")
    for p, ns in g.namespaces():
        if p == prefix:
            return rdflib.URIRef(str(ns) + name)
    sys.exit(f"Error: unknown prefix in {term}")


def statements_by_uri(g, statements):
    index = {}
    for s in statements:
        if is_directive(s):
            continue
        try:
            uri = expand(g, s.subject) if not s.subject.startswith("_:") else None
        except SystemExit:
            uri = None
        if uri is not None:
            index.setdefault(uri, []).append(s)
    return index


def cmd_header(args):
    text, lines, g, statements, secs = load(args.file)
    for s in statements:
        if is_directive(s):
            print(f"{s.start}: {lines[s.start - 1].strip()}")
    onto = list(g.subjects(RDF.type, rdflib.OWL.Ontology))
    index = statements_by_uri(g, statements)
    shown = set()
    for o in onto:
        where = index.get(o, [])
        span = f"lines {where[0].start}-{where[0].end}" if where else "line ?"
        print(f"\nontology <{o}>  ({span})")
        for v in g.objects(o, rdflib.OWL.versionInfo):
            print(f"  owl:versionInfo {v}")
        if where:
            for k in range(where[0].start - 1, where[0].end):
                if lines[k].strip().startswith("#"):
                    print(f"  {k + 1}: {lines[k].strip()}")
                    shown.add(k + 1)
    first = secs[1].start - 1 if len(secs) > 1 else len(lines)
    comments = [
        (k + 1, l.strip())
        for k, l in enumerate(lines[:first])
        if l.strip().startswith("#") and k + 1 not in shown and not BANNER.match(l.strip())
    ]
    if comments:
        print("\nheader comments:")
        for k, l in comments:
            print(f"  {k}: {l}")


def cmd_outline(args):
    _, _, _, _, secs = load(args.file)
    for sec in secs:
        print(f"lines {sec.start}-{sec.end}, {len(sec.statements)} terms: {sec.title}")


def cmd_where(args):
    _, _, g, statements, secs = load(args.file)
    index = statements_by_uri(g, statements)
    missing = 0
    for term in args.terms:
        uri = expand(g, term)
        found = index.get(uri, [])
        if not found:
            print(f"{term}: not the subject of any statement")
            missing += 1
            continue
        for s in found:
            print(f"{qname(g, uri)}: lines {s.start}-{s.end}  [{section_of(secs, s.start)}]")
    sys.exit(1 if missing else 0)


def cmd_text(args):
    _, _, g, statements, secs = load(args.file)
    index = statements_by_uri(g, statements)
    pattern = re.compile(args.regex, re.IGNORECASE)
    prop = expand(g, args.prop) if args.prop else None
    hits = 0
    for s, p, o in sorted(g, key=lambda t: (str(t[0]), str(t[1]))):
        if not isinstance(o, rdflib.Literal) or (prop is not None and p != prop):
            continue
        for m in pattern.finditer(str(o)):
            value = str(o)
            a, b = max(0, m.start() - 80), min(len(value), m.end() + 80)
            where = index.get(s, [])
            span = f"{where[0].start}-{where[0].end}" if where else "?"
            section = section_of(secs, where[0].start) if where else "?"
            print(f"{qname(g, s)}  {qname(g, p)}  lines {span}  [{section}]")
            print(f"    …{value[a:b]}…")
            hits += 1
            break
    if hits == 0:
        print(f"No literal matching {args.regex!r}")


def cmd_anchor(args):
    _, lines, g, statements, secs = load(args.file)
    index = statements_by_uri(g, statements)
    rdf_type = expand(g, args.type)
    candidates = []
    for uri in g.subjects(RDF.type, rdf_type):
        q = qname(g, uri)
        if args.prefix and not q.startswith(args.prefix + ":"):
            continue
        for s in index.get(uri, []):
            sec = section_of(secs, s.start)
            if args.section and args.section.lower() not in sec.lower():
                continue
            candidates.append((s.end, s.start, q, sec))
    if not candidates:
        sys.exit("No term matches; widen --prefix or --section, or read `outline`.")
    end, start, q, sec = max(candidates)
    print(f"last match: {q}, lines {start}-{end}  [{sec}]")
    print(f"insert after line {end}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("header", "outline"):
        sub.add_parser(name).add_argument("file")
    w = sub.add_parser("where")
    w.add_argument("file")
    w.add_argument("terms", nargs="+")
    t = sub.add_parser("text")
    t.add_argument("file")
    t.add_argument("regex")
    t.add_argument("--prop", help="only this property's literals, e.g. rdfs:comment")
    a = sub.add_parser("anchor")
    a.add_argument("file")
    a.add_argument("--type", required=True)
    a.add_argument("--prefix")
    a.add_argument("--section", help="a part of the section title, case-insensitive")
    args = parser.parse_args()
    {"header": cmd_header, "outline": cmd_outline, "where": cmd_where, "text": cmd_text, "anchor": cmd_anchor}[args.command](args)


if __name__ == "__main__":
    main()

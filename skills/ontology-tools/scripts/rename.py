#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib"]
# ///
"""
Rename a term of a Turtle file: every place the file writes it, its rdfs:label, and every
citation of it in the given files (a qname or a ttl://file.ttl/prefix:Name reference).

Usage:
  rename.py <file.ttl> <old> <new> [--label TEXT] [--cite PATH ...] [--dry-run]

  <old>, <new>   qnames of one prefix, e.g. ex:DecisionRefundFirst ex:RefundOrder
  --label TEXT   the new rdfs:label; the term must have exactly one
  --cite PATH    a file or a directory whose text files cite the term; a directory is
                 walked, without .git and the TTL file itself. Repeatable.
  --dry-run      print what would change, write nothing

The new name must not be written anywhere in the file. After the write the file is parsed
again; a failed parse restores it. The output lists each changed file with its count, then
the other terms whose statements name the term: their lines changed, so a check run with
--changed-since reads them next.

Exit 1 on an unknown old term, a new name in use, a label count other than one, or a parse
failure.
"""

import argparse
import re
import sys
from pathlib import Path

import rdflib

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
import locate  # noqa: E402


def token(qname: str) -> re.Pattern:
    """The qname as a whole token: not part of a longer name or of another prefix."""
    return re.compile(rf"(?<![\w:]){re.escape(qname)}(?![\w-])")


def relabel(text: str, statement, label: str) -> str:
    lines = text.splitlines(keepends=True)
    block = "".join(lines[statement.start - 1 : statement.end])
    found = re.findall(r"rdfs:label\s+(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", block)
    if len(found) != 1:
        sys.exit(f"Error: {statement.subject} has {len(found)} rdfs:label values, not one")
    quoted = label.replace("\\", "\\\\").replace('"', '\\"')
    block = block.replace(found[0], f'"{quoted}"', 1)
    return "".join(lines[: statement.start - 1]) + block + "".join(lines[statement.end :])


def cited_files(paths: list[str], ttl: Path):
    for raw in paths:
        p = Path(raw)
        candidates = p.rglob("*") if p.is_dir() else [p]
        for f in candidates:
            if f.is_file() and ".git" not in f.parts and f.resolve() != ttl.resolve():
                yield f


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--label")
    ap.add_argument("--cite", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ttl = Path(args.file)
    text, _lines, g, statements, _secs = locate.load(args.file)
    old_uri, new_uri = locate.expand(g, args.old), locate.expand(g, args.new)
    old_q, new_q = locate.qname(g, old_uri), locate.qname(g, new_uri)

    own = locate.statements_by_uri(g, statements).get(old_uri)
    if not own:
        sys.exit(f"Error: {old_q} is not the subject of a statement in {ttl}")
    if token(new_q).search(text) or (new_uri, None, None) in g or (None, None, new_uri) in g:
        sys.exit(f"Error: {new_q} is already written in {ttl}")

    original = text
    old_token = token(old_q)
    if args.label is not None:
        text = relabel(text, own[0], args.label)
    renamed, count = old_token.subn(new_q, text)
    changes = [(ttl, count)]

    cites = []
    for f in cited_files(args.cite, ttl):
        try:
            body = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new_body, n = old_token.subn(new_q, body)
        if n:
            cites.append((f, new_body))
            changes.append((f, n))

    lines = renamed.splitlines(keepends=True)
    new_token = token(new_q)
    namers = sorted(
        {
            s.subject
            for s in locate.scan(renamed)
            if not locate.is_directive(s)
            and s.subject != new_q
            and new_token.search("".join(lines[s.start - 1 : s.end]))
        }
    )

    for f, n in changes:
        print(f"{f}: {n}")
    if namers:
        print("named by: " + ", ".join(namers))
    if args.dry_run:
        return

    ttl.write_text(renamed, encoding="utf-8")
    try:
        rdflib.Graph().parse(ttl, format="turtle")
    except Exception as e:  # noqa: BLE001
        ttl.write_text(original, encoding="utf-8")
        sys.exit(f"Error: {ttl} does not parse after the rename, restored: {e}")
    for f, body in cites:
        f.write_text(body, encoding="utf-8")


if __name__ == "__main__":
    main()

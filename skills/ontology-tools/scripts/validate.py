#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib"]
# ///
"""
General-purpose Turtle ontology validator.

Usage:
  validate.py <file.ttl> [--namespace <prefix> <uri>] [--check-labels] [--check-ranges]
              [--check-orphans]

Always:
  - Parses the TTL and reports triple count (exit 1 on parse error)
  - Detects untyped named individuals (non-external subjects with no rdf:type)
  - Detects undeclared internal targets in rdfs:domain, rdfs:range, rdfs:subClassOf
  - Detects undeclared IRIs used inside OWL axioms (restrictions, lists) — typos that
    parse silently and disable the axiom they appear in

Optional checks (flags):
  --check-labels    Warn when rdfs:label is missing on any class or property
  --check-ranges    Warn when rdf:Property has no rdfs:range declared
  --check-orphans   Warn on declared terms never referenced by any other term
  --namespace p u   Report all terms in namespace u grouped by rdf:type

Naming checks (each finding names its rule; ❌ is an error, ⚠️ a warning):
  --check-names     ❌ label-count (not exactly one rdfs:label), label-unique (another term has
                    the label, ignoring case), label-format (CamelCase, an underscore, doubled or
                    edge spaces), label-case (the first word is capitalized and is not a proper
                    name or an acronym), type-in-name (the local name repeats the local name of
                    its own rdf:type), separator (CamelCase and an underscore in one local name;
                    names of both styles in one file), definition (no rdfs:comment and no
                    skos:definition). Sources: OBO Foundry principle 12; Schober et al. 2009,
                    BMC Bioinformatics 10:125, rules 1.3 and 3.3; W3C vocabulary principles 2.2.
  --check-wording   ⚠️ article, conjunction, negation, verb and abbreviation as words of the local
                    name or the label (Schober 1.1, 2.2, 2.4, 3.4), and genus (a subclass's label
                    does not end with its superclass's label, Schober 3.2).
  --check-header    ❌ version-iri and prior-version (the owl:Ontology has no owl:versionIRI or
                    owl:priorVersion, W3C 2.4), replacement (a term with owl:deprecated true has
                    no dcterms:isReplacedBy, W3C 2.3).
  --config FILE     The word lists, TOML, table [names]: proper_names, acronyms,
                    abbreviations, verbs, and articles, conjunctions, negations to replace the
                    English defaults. Without the flag, ontology-tools.toml next to the TTL.
  --changed-since R Check only the terms whose statements `git diff R` changes, and the
                    ontology header only when its statement changed.
  --strict          Exit 1 on a warning too.

An exemption is an annotation on the term: ot:exempt "<rule>: <reason>", where ot: is
https://github.com/full-stack-biz/ontology-tools# . The term is not checked for that rule. An
exemption without a reason is the error exempt-reason, and does not exempt.

Exit code: 1 on a parse error or an ❌ finding (and on a ⚠️ finding with --strict), else 0.
"""

import re
import subprocess
import sys
import argparse
import tomllib
from pathlib import Path

import rdflib
from rdflib import RDF, RDFS, OWL

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import locate  # noqa: E402

SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")
DCTERMS = rdflib.Namespace("http://purl.org/dc/terms/")
OT = rdflib.Namespace("https://github.com/full-stack-biz/ontology-tools#")

DEFAULT_WORDS = {
    "proper_names": [],
    "acronyms": [],
    "abbreviations": [],
    "verbs": [],
    "articles": ["a", "an", "the"],
    "conjunctions": ["and", "or"],
    "negations": ["not", "no", "never", "without", "non"],
}


def parse_args():
    p = argparse.ArgumentParser(description="Validate a Turtle ontology file.")
    p.add_argument("path", help="Path to the .ttl file")
    p.add_argument("--check-labels", action="store_true", help="Warn on missing rdfs:label")
    p.add_argument("--check-ranges", action="store_true", help="Warn on properties missing rdfs:range")
    p.add_argument("--check-orphans", action="store_true", help="Warn on declared terms with no inbound references")
    p.add_argument(
        "--orphan-exclude-type",
        action="append",
        metavar="TYPE",
        help="Skip terms of this rdf:type in --check-orphans; repeatable. "
             "Takes a full URI, a qname, or a bare local name.",
    )
    p.add_argument("--namespace", nargs=2, metavar=("PREFIX", "URI"), help="Namespace to inspect")
    p.add_argument("--check-names", action="store_true", help="Errors on labels, names and definitions")
    p.add_argument("--check-wording", action="store_true", help="Warnings on the words of names and labels")
    p.add_argument("--check-header", action="store_true", help="Errors on version IRIs and deprecations")
    p.add_argument("--config", help="TOML word lists; default: ontology-tools.toml next to the TTL")
    p.add_argument("--changed-since", metavar="REF", help="Check only the terms git diff REF changes")
    p.add_argument("--strict", action="store_true", help="Exit 1 on a warning too")
    return p.parse_args()


def short(uri, g):
    try:
        return g.qname(uri)
    except Exception:
        return str(uri)


EXTERNAL_PREFIXES = {
    str(RDF),
    str(RDFS),
    str(OWL),
    "http://www.w3.org/2001/XMLSchema#",
    "https://www.w3.org/ns/activitystreams#",
    "https://w3id.org/security#",
}


def is_external(uri: rdflib.URIRef) -> bool:
    s = str(uri)
    return any(s.startswith(p) for p in EXTERNAL_PREFIXES)


def resolve_types(names, g: rdflib.Graph) -> set:
    """Each --orphan-exclude-type value, as a full URI, a qname, or a bare local name."""
    resolved = set()
    for name in names:
        if name.startswith(("http://", "https://")):
            resolved.add(rdflib.URIRef(name))
            continue
        if ":" in name:
            prefix, local = name.split(":", 1)
            match = next((ns for p, ns in g.namespaces() if p == prefix), None)
            if match is not None:
                resolved.add(rdflib.URIRef(str(match) + local))
                continue
        for subject in set(g.subjects()):
            if isinstance(subject, rdflib.URIRef) and str(subject).rsplit("#", 1)[-1].rsplit("/", 1)[-1] == name:
                resolved.add(subject)
    return resolved


def cited_in_prose(term: rdflib.URIRef, g: rdflib.Graph) -> bool:
    """
    True when another term's string literal names this one.

    An ontology cross-references in two ways: a triple, and a sentence inside an
    rdfs:comment. The second is a reference the graph does not record, so a term cited
    only that way reads as an orphan. Both written forms count: the qname
    (`payroc:replayWindow`) and the bare local name (`Inv_RecordBeforeCall`).

    The bare form is accepted only for a local name that no sentence would use as an
    ordinary word: one that has an underscore, or two or more capitals. Without that
    rule the local name `Credit` matches the sentence "A credit is a refill", and a
    real orphan then reads as cited.
    """
    qname = short(term, g)
    local = str(term).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    distinctive = "_" in local or sum(1 for c in local if c.isupper()) >= 2
    for subject, _, obj in g:
        if not isinstance(obj, rdflib.Literal) or subject == term:
            continue
        text = str(obj)
        if qname in text:
            return True
        if distinctive and re.search(rf"\b{re.escape(local)}\b", text):
            return True
    return False


def check(g: rdflib.Graph, args) -> int:
    warnings = 0

    ns = rdflib.Namespace(args.namespace[1]) if args.namespace else None

    # Collect named classes and properties (skip anonymous blank nodes)
    def uri_subjects(*type_uris):
        result = set()
        for t in type_uris:
            for s, _, _ in g.triples((None, RDF.type, t)):
                if isinstance(s, rdflib.URIRef):
                    result.add(s)
        return result

    classes = uri_subjects(RDFS.Class, OWL.Class)
    properties = uri_subjects(RDF.Property, OWL.ObjectProperty, OWL.DatatypeProperty)

    print(f"  Classes:    {len(classes)}")
    print(f"  Properties: {len(properties)}")

    # Untyped named individuals: non-external subjects with no rdf:type
    defined = set(g.subjects())
    for subj in sorted(defined, key=str):
        if not isinstance(subj, rdflib.URIRef):
            continue
        if is_external(subj):
            continue
        if not list(g.triples((subj, RDF.type, None))):
            print(f"  ⚠️  Untyped subject: {short(subj, g)}")
            warnings += 1

    # Undeclared internal targets in domain/range/subClassOf
    for pred in (RDFS.domain, RDFS.range, RDFS.subClassOf):
        for s, _, o in g.triples((None, pred, None)):
            if not isinstance(o, rdflib.URIRef):
                continue
            if is_external(o):
                continue
            if o not in defined:
                print(f"  ⚠️  {short(pred, g)} target not declared: {short(o, g)} (on {short(s, g)})")
                warnings += 1

    # Undeclared IRIs inside OWL axioms (restriction components + list members)
    # A typo here parses silently and disables the axiom without any error.
    declared = set(
        s for s, _, _ in g.triples((None, RDF.type, None))
        if isinstance(s, rdflib.URIRef)
    )
    axiom_preds = (
        OWL.onProperty, OWL.allValuesFrom, OWL.someValuesFrom,
        OWL.onClass, OWL.hasValue,
    )
    for pred in axiom_preds:
        for _, _, o in g.triples((None, pred, None)):
            if isinstance(o, rdflib.URIRef) and not is_external(o) and o not in declared:
                print(f"  ⚠️  Undeclared IRI in axiom: {short(o, g)} (as {short(pred, g)})")
                warnings += 1
    list_preds = (OWL.members, OWL.unionOf, OWL.intersectionOf)
    for pred in list_preds:
        for _, _, list_node in g.triples((None, pred, None)):
            if not isinstance(list_node, rdflib.BNode):
                continue
            try:
                for item in rdflib.collection.Collection(g, list_node):
                    if isinstance(item, rdflib.URIRef) and not is_external(item) and item not in declared:
                        print(f"  ⚠️  Undeclared IRI in list: {short(item, g)} (via {short(pred, g)})")
                        warnings += 1
            except Exception:
                pass

    # Missing labels (skip blank nodes — they can't have labels)
    if args.check_labels:
        for term in sorted(classes | properties, key=str):
            if not isinstance(term, rdflib.URIRef):
                continue
            if not list(g.triples((term, RDFS.label, None))):
                print(f"  ⚠️  No rdfs:label on {short(term, g)}")
                warnings += 1

    # Properties missing rdfs:range
    if args.check_ranges:
        for prop in sorted(properties, key=str):
            if not list(g.triples((prop, RDFS.range, None))):
                print(f"  ⚠️  No rdfs:range on {short(prop, g)}")
                warnings += 1

    # Orphaned terms: declared but never referenced by any other term
    if args.check_orphans:
        # Collect all IRIs that appear as objects or inside RDF lists
        referenced = set(o for _, _, o in g if isinstance(o, rdflib.URIRef))
        for pred in list_preds:
            for _, _, list_node in g.triples((None, pred, None)):
                if not isinstance(list_node, rdflib.BNode):
                    continue
                try:
                    for item in rdflib.collection.Collection(g, list_node):
                        if isinstance(item, rdflib.URIRef):
                            referenced.add(item)
                except Exception:
                    pass
        excluded_types = resolve_types(args.orphan_exclude_type or [], g)
        individuals = uri_subjects(OWL.NamedIndividual)
        for term in sorted(classes | properties | individuals, key=str):
            if is_external(term):
                continue
            if term in referenced:
                continue
            if excluded_types & set(g.objects(term, RDF.type)):
                continue
            if cited_in_prose(term, g):
                continue
            print(f"  ⚠️  Orphan (never referenced): {short(term, g)}")
            warnings += 1

    # Namespace inspection
    if ns:
        prefix = args.namespace[0]
        print(f"\n  Terms in {prefix}: namespace:")
        ns_terms = set(
            s for s in set(g.subjects())
            if isinstance(s, rdflib.URIRef) and str(s).startswith(str(ns))
        )
        by_type = {}
        for term in sorted(ns_terms, key=str):
            types = tuple(sorted(str(o).split("#")[-1] for _, _, o in g.triples((term, RDF.type, None))))
            by_type.setdefault(types or ("(untyped)",), []).append(short(term, g))
        for types, terms in sorted(by_type.items()):
            print(f"    [{', '.join(types)}]")
            for t in terms:
                print(f"      {t}")

    return warnings


def load_words(args) -> dict:
    """The word lists: the defaults, replaced key by key by the [names] table of the config."""
    path = Path(args.config) if args.config else Path(args.path).resolve().parent / "ontology-tools.toml"
    words = {key: list(value) for key, value in DEFAULT_WORDS.items()}
    if path.exists():
        with path.open("rb") as file:
            table = tomllib.load(file).get("names", {})
        words.update({key: list(value) for key, value in table.items() if key in words})
    elif args.config:
        sys.exit(f"Error: config not found: {args.config}")
    return words


def local_name(uri) -> str:
    return str(uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]


NAME_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def name_words(local: str) -> list[str]:
    """The words of a local name, split at underscores and at capitals: Inv_APIKeyOwner is
    Inv, API, Key, Owner."""
    return [word for part in local.split("_") for word in NAME_WORD.findall(part)]


def label_words(label: str) -> list[str]:
    return [word for word in re.split(r"[\s\-]+", label) if word]


def contains_run(words: list[str], run: list[str]) -> bool:
    return any(words[i : i + len(run)] == run for i in range(len(words) - len(run) + 1))


def changed_terms(path: str, ref: str, g: rdflib.Graph) -> set:
    """The subjects whose statements contain a line that `git diff REF` adds or changes; a
    deletion counts as a change of the line after it."""
    file = Path(path).resolve()
    diff = subprocess.run(
        ["git", "-C", str(file.parent), "diff", "-U0", ref, "--", file.name],
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        sys.exit(f"Error: git diff {ref} failed: {diff.stderr.strip()}")
    lines = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff.stdout, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        lines.update(range(start, start + max(count, 1)))
    index = locate.statements_by_uri(g, locate.scan(file.read_text(encoding="utf-8")))
    return {
        uri
        for uri, statements in index.items()
        if any(s.start <= line <= s.end for s in statements for line in lines)
    }


def check_naming(g: rdflib.Graph, args, words: dict, only) -> tuple[int, int]:
    """--check-names, --check-wording and --check-header: (errors, warnings)."""
    counts = {"error": 0, "warning": 0}

    def report(kind, rule, display, message):
        mark = "❌" if kind == "error" else "⚠️ "
        print(f"  {mark} {rule} {display}: {message}")
        counts[kind] += 1

    def lower(key):
        return {word.lower() for word in words[key]}

    ontologies = set(g.subjects(RDF.type, OWL.Ontology))
    terms = sorted(
        {
            s
            for s in g.subjects(RDF.type, None)
            if isinstance(s, rdflib.URIRef)
            and not is_external(s)
            and s not in ontologies
            and not str(s).startswith(str(OT))
        },
        key=str,
    )

    def in_scope(term):
        return only is None or term in only

    if args.check_header:
        for onto in sorted(ontologies, key=str):
            if not in_scope(onto):
                continue
            if not list(g.objects(onto, OWL.versionIRI)):
                report("error", "version-iri", f"<{onto}>", "the ontology has no owl:versionIRI")
            if not list(g.objects(onto, OWL.priorVersion)):
                report("error", "prior-version", f"<{onto}>", "the ontology has no owl:priorVersion")

    first_with_label = {}
    for term in terms:
        for label in g.objects(term, RDFS.label):
            first_with_label.setdefault(str(label).lower(), term)

    proper = set(words["proper_names"])
    acronyms = set(words["acronyms"])

    for term in terms:
        if not in_scope(term):
            continue
        name = short(term, g)
        local = local_name(term)
        labels = [str(label) for label in g.objects(term, RDFS.label)]

        exempt = set()
        for value in g.objects(term, OT.exempt):
            rule, colon, reason = str(value).partition(":")
            if colon and reason.strip():
                exempt.add(rule.strip())
            elif args.check_names:
                report("error", "exempt-reason", name, f'"{value}" does not state a reason after "<rule>:"')

        def finding(kind, rule, message):
            if rule not in exempt:
                report(kind, rule, name, message)

        if args.check_names:
            if len(labels) != 1:
                finding("error", "label-count", f"{len(labels)} rdfs:label values, not exactly one")
            for label in labels:
                owner = first_with_label[label.lower()]
                if owner != term:
                    finding("error", "label-unique", f'"{label}" is also the label of {short(owner, g)}')
                words_of_label = label_words(label)
                camel = [w for w in words_of_label if re.search(r"[a-z][A-Z]", w) and w not in proper]
                if camel or "_" in label or "  " in label or label != label.strip():
                    finding("error", "label-format", f'"{label}" is not words separated by single spaces')
                first = words_of_label[0] if words_of_label else ""
                if first[:1].isupper() and not first.isupper() and first not in proper and first not in acronyms:
                    finding("error", "label-case", f'"{label}" starts with a capital; "{first}" is not a proper name')
            words_of_name = [w.lower() for w in name_words(local)]
            for rdf_type in g.objects(term, RDF.type):
                if not isinstance(rdf_type, rdflib.URIRef) or is_external(rdf_type):
                    continue
                type_words = [w.lower() for w in name_words(local_name(rdf_type))]
                if type_words and len(words_of_name) > len(type_words) and contains_run(words_of_name, type_words):
                    finding("error", "type-in-name", f"the name repeats its type's name, {local_name(rdf_type)}")
            if "_" in local and re.search(r"[a-z][A-Z]", local):
                finding("error", "separator", "CamelCase and an underscore in one name")
            if not list(g.objects(term, RDFS.comment)) and not list(g.objects(term, SKOS.definition)):
                finding("error", "definition", "no rdfs:comment and no skos:definition")

        if args.check_header:
            deprecated = any(value.toPython() is True for value in g.objects(term, OWL.deprecated))
            if deprecated and not list(g.objects(term, DCTERMS.isReplacedBy)):
                finding("error", "replacement", "owl:deprecated without dcterms:isReplacedBy")

        if args.check_wording:
            name_lower = [w.lower() for w in name_words(local)]
            label_tokens = [w for label in labels for w in label_words(label)]
            tokens = set(name_lower) | {w.lower() for w in label_tokens}
            for rule, key in (
                ("article", "articles"),
                ("conjunction", "conjunctions"),
                ("negation", "negations"),
                ("verb", "verbs"),
            ):
                found = sorted(tokens & lower(key))
                if found:
                    finding("warning", rule, ", ".join(found))
            abbreviations = sorted(tokens & lower("abbreviations"))
            abbreviations += sorted(
                {
                    w
                    for w in label_tokens
                    if len(w) > 1 and w.isalpha() and w.isupper() and w not in acronyms and w not in proper
                }
            )
            if abbreviations:
                finding("warning", "abbreviation", ", ".join(abbreviations))
            parent_labels = [
                str(label)
                for parent in g.objects(term, RDFS.subClassOf)
                if isinstance(parent, rdflib.URIRef) and not is_external(parent)
                for label in g.objects(parent, RDFS.label)
            ]
            if labels and parent_labels and not any(
                labels[0].lower().endswith(parent.lower()) for parent in parent_labels
            ):
                quoted = ", ".join(f'"{parent}"' for parent in parent_labels)
                finding("warning", "genus", f'"{labels[0]}" does not end with a superclass\'s label: {quoted}')

    if args.check_names and only is None:
        names = [local_name(term) for term in terms]
        underscore = [n for n in names if "_" in n and not re.search(r"[a-z][A-Z]", n)]
        camel = [n for n in names if "_" not in n and re.search(r"[a-z][A-Z]", n)]
        if underscore and camel:
            report(
                "error",
                "separator",
                "(file)",
                f"{len(underscore)} names separate words with an underscore, {len(camel)} with capitals",
            )

    return counts["error"], counts["warning"]


def main():
    args = parse_args()
    print(f"Parsing {args.path} ...")
    g = rdflib.Graph()
    try:
        g.parse(args.path, format="turtle")
    except Exception as e:
        print(f"❌ Parse error: {e}")
        sys.exit(1)

    print(f"OK: {len(g)} triples\n")
    print("Checking:")
    warnings = check(g, args)
    errors = 0

    if args.check_names or args.check_wording or args.check_header:
        only = changed_terms(args.path, args.changed_since, g) if args.changed_since else None
        naming_errors, naming_warnings = check_naming(g, args, load_words(args), only)
        errors += naming_errors
        warnings += naming_warnings

    if errors:
        print(f"\n❌ {errors} error(s)")
    if warnings:
        print(f"\n⚠️  {warnings} warning(s)")
    if not errors and not warnings:
        print("\n✅ No issues found")

    sys.exit(1 if errors or (args.strict and warnings) else 0)


if __name__ == "__main__":
    main()

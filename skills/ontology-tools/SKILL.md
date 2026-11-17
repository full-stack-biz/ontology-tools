---
name: ontology-tools
description: >-
  Turtle ontology toolbox: (1) validate — check syntax, labels, ranges, find
  gaps, fix TTL; (2) apply to decisions — check code/schema against declared
  axioms and invariants, report PASS/FAIL; (3) browse and search — list all
  classes, properties, and individuals without reading the raw TTL, search for
  terms by name or label; (4) resolve ttl:// references — look up a specific
  term by ttl://filename.ttl/prefix:LocalName and return its triples; (5) draw —
  emit a Mermaid classDiagram of a scoped part of the ontology; (6) locate —
  the sections, the line range of a term, the terms whose comments match a
  pattern, the prefixes and version, and the line to add a new term after,
  so a TTL file is never read or edited with grep. Use when
  asked to: "validate ontology", "check against ontology", "does this violate
  the ontology", "ontology gap", "audit domain model", "apply ontology to this
  decision", "what's in this ontology", "list ontology terms", "find ontology
  concept for X", "look up ontology term", "resolve ttl:// reference", or
  "draw / visualise / diagram the ontology".
allowed_tools: ["Read", "Write", "Bash", "Grep"]
---

# Ontology Validator

Two distinct workflows. Pick the right one from context.

---

## Goal 1 — Keep the Ontology Valid

Use when the ontology file itself is being edited, extended, or audited for completeness.

### Step 1 — Parse and check structure

```bash
# Syntax + triple count + undeclared IRI check (always run first after any TTL edit)
uv run "$SKILLS_DIR/scripts/validate.py" path/to/ontology.ttl

# Also check labels, ranges, and orphaned terms
uv run "$SKILLS_DIR/scripts/validate.py" path/to/ontology.ttl \
  --check-labels --check-ranges --check-orphans

# Orphan check — skip terms of a given rdf:type (repeatable; full URI, qname, or bare local name)
uv run "$SKILLS_DIR/scripts/validate.py" path/to/ontology.ttl \
  --check-orphans --orphan-exclude-type owl:NamedIndividual

# Inspect all terms in a namespace
uv run "$SKILLS_DIR/scripts/validate.py" path/to/ontology.ttl \
  --namespace prefix https://example.org/ns#
```

If parse fails: fix the TTL before anything else. Parse errors are shown with line numbers.

### Naming checks (validate.py)

```bash
# Errors (❌): labels, names, definitions; warnings (⚠️): the words of names and labels;
# errors on the header: version IRIs and deprecated terms
uv run "$SKILLS_DIR/scripts/validate.py" path/to/ontology.ttl \
  --check-names --check-wording --check-header

# Only the terms a change touches: their statements differ from the git ref
uv run "$SKILLS_DIR/scripts/validate.py" path/to/ontology.ttl \
  --check-names --check-wording --check-header --changed-since origin/main
```

Each finding is `<mark> <rule> <term>: <message>`. The rules and their sources:

| Rule | Kind | Reported when | Source |
|---|---|---|---|
| `label-count` | ❌ | not exactly one `rdfs:label` | OBO Foundry principle 12 |
| `label-unique` | ❌ | another term has the label, ignoring case | OBO principle 12 |
| `label-format` | ❌ | CamelCase, `_`, doubled or edge spaces in a label | OBO principle 12; Schober et al. 2009, 3.3 |
| `label-case` | ❌ | the label's first word is capitalized and is not a proper name or an acronym | OBO principle 12; Schober 4.1 |
| `type-in-name` | ❌ | the local name repeats the local name of its own `rdf:type` | Schober 1.3 |
| `separator` | ❌ | CamelCase and `_` in one local name; `(file)`: names of both styles in one file | Schober 3.3 |
| `definition` | ❌ | no `rdfs:comment` and no `skos:definition` | W3C vocabulary principles 2.2 |
| `version-iri`, `prior-version` | ❌ | the `owl:Ontology` has no `owl:versionIRI` or `owl:priorVersion` | W3C 2.4 |
| `replacement` | ❌ | `owl:deprecated true` without `dcterms:isReplacedBy` | W3C 2.3 |
| `exempt-reason` | ❌ | an exemption does not state a reason | — |
| `article`, `conjunction`, `negation`, `verb`, `abbreviation` | ⚠️ | the word is in the local name or the label | Schober 1.1, 2.2, 2.4, 3.4 |
| `genus` | ⚠️ | a subclass's label does not end with the label of any of its superclasses | Schober 3.2 |

The word lists are a TOML file: `--config FILE`, or `ontology-tools.toml` next to the TTL.
Table `[names]`: `proper_names` and `acronyms` (allowed capitals), `abbreviations` (words to
report), `verbs` (no default), and `articles`, `conjunctions`, `negations`, which replace the
English defaults.

An exemption is written on the term, with its reason, and covers one rule:

```turtle
@prefix ot: <https://github.com/full-stack-biz/ontology-tools#> .
ex:cardPresentAndCardNotPresent ot:exempt "conjunction: card present and card not present are Visa's terms" .
```

Exit 1 on an ❌ finding, and with `--strict` on a ⚠️ finding too.

### Renaming a term (rename.py)

A name a naming check reports is fixed with `rename.py`, never with an editor's search and
replace: it changes the term wherever the TTL writes it as a whole token (not a longer name that
starts with it), sets the label, and rewrites each citation in the given files.

```bash
uv run "$SKILLS_DIR/scripts/rename.py" path/to/ontology.ttl ex:DecisionRefundFirst ex:RefundOrder \
  --label "refund order" --cite lib --cite test --cite docs --dry-run
```

It prints each changed file with its count, then `named by:` with the other terms whose
statements name the term. Their lines change, so `--changed-since` checks them next; run the
naming check after each rename and rename what it reports. It exits 1, writing nothing, when the
old term is not a subject, the new name is already written in the file, or the term does not have
exactly one `rdfs:label`; a file that does not parse after the write is restored.

### Step 2 — Read and audit the ontology

```
Read: path/to/ontology.ttl
```

Check for gaps:

| Gap type | What to look for |
|---|---|
| Missing `rdfs:label` | Every class, property, named individual should have one |
| Missing `rdfs:comment` | Every non-obvious term needs a comment explaining its semantics |
| Wrong `rdf:type` | Named individuals typed as the wrong class (e.g. an `allows` value typed as a Role) |
| Missing `rdfs:domain`/`rdfs:range` | Properties should declare both where it makes sense |
| Undocumented invariants | Constraints that exist in code but have no representation in the TTL |
| Spec references missing | If the ontology models a standard (AP, ForgeFed, etc.), comments should cite spec sections |
| Undeclared IRI in axiom | A typo inside a restriction or list creates a fresh IRI that parses and stays consistent but silently disables the axiom — reported by `validate.py` by default |

Check for all internal contradictions, unresolved tensions, or claims that don't fully follow from the evidence. Offer to fix them, one at a time, with explaination how did you fix it and what is situation with contradictions, tensions and claims afterwards, do not move to the next item in list without confirming with me that I understand results and effect of changes.

### Step 3 — Fix and re-validate

After every TTL edit:

```bash
uv run "$SKILLS_DIR/scripts/validate.py" path/to/ontology.ttl
```

Exit code 0 = syntactically valid. Re-read the edited section to confirm semantics are correct.

---

## Goal 2 — Apply the Ontology to Decisions

Use when evaluating proposed code, schema, architecture, or implementation choices against what the ontology declares.

### Step 1 — Read the ontology

```
Read: path/to/ontology.ttl
```

Parse three sections:
- **Named invariants** — `*:Inv_*` instances with `rdfs:comment`. Always check on domain-path changes.
- **Structural axioms** — `rdfs:subClassOf`, `owl:Restriction`, `rdfs:range`, `rdfs:domain`.
- **Type classifications** — named individuals typed as enumerations.

### Step 2 — Identify relevant axioms

| Proposed change | Axioms to check |
|---|---|
| New column / property | `rdfs:range`, `rdfs:domain`, cardinality restrictions |
| New FK / relation | Disjoint classes, `rdfs:range` (which type can it point to?) |
| New enum value | Type classification (is this a valid instance of the declared class?) |
| New code flow / ordering | Named invariants (`Inv_*`) that constrain sequencing |
| New entity type | `AllDisjointClasses`, subclass restrictions |

### Step 3 — Gather evidence

```bash
# Find uses of a concept in source
grep -rn "SomeConcept\|some_concept" src/ lib/ app/ 2>/dev/null

# Find column definitions
grep -rn "column_name" database/migrations/ schema/ 2>/dev/null

# Find enum/state assignments
grep -rn '"value"\|:value' lib/ app/ 2>/dev/null

# Trace code flow for ordering invariants
# Read the relevant service/handler file directly
```

### Step 4 — Report

One entry per axiom checked. Most-critical first.

```
### ns:Inv_SomeInvariant — Short label
Status: ✅ PASS
Evidence: grep finds no violations; constraint holds.

### ns:SomeProp rdfs:range ns:SomeClass
Status: ❌ FAIL
Evidence: app/models/foo.rb:42 — stores raw integer, ontology requires ns:SomeClass reference
Fix: Store a typed reference, not a bare integer.
```

End with a summary table and overall verdict:

```
| Axiom | Status |
|---|---|
| ns:Inv_Foo | ✅ PASS |
| ns:Bar rdfs:range | ❌ FAIL |
| ns:Baz cardinality | ⚠️ MISMATCH |
| ns:Missing coverage | ⚠️ ONTOLOGY GAP |

Overall: ❌ FAIL — ns:Bar range violated.
```

### Verdict types

| Verdict | Meaning | Fix goes in |
|---|---|---|
| ✅ PASS | Code matches ontology | — |
| ❌ FAIL | Code violates ontology | Code |
| ⚠️ MISMATCH | Ontology wrong about the code | Ontology |
| ⚠️ ONTOLOGY GAP | Concept exists in code, not in ontology | Ontology |

Overall: `✅ COMPLIANT` / `⚠️ PARTIAL` (gaps/mismatches only) / `❌ FAIL` (any violation).

---

## Standard Namespace Prefixes

Always use these exact URIs. Copy-paste the block below into every new TTL file:

```turtle
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
```

| Prefix | Correct URI | Common mistake |
|--------|-------------|----------------|
| `xsd:` | `http://www.w3.org/2001/XMLSchema#` | `2000/01/XMLSchema#` (wrong year) |
| `rdfs:` | `http://www.w3.org/2000/01/rdf-schema#` | `2001/rdf-schema#` |
| `owl:` | `http://www.w3.org/2002/07/owl#` | — |
| `rdf:` | `http://www.w3.org/1999/02/22-rdf-syntax-ns#` | — |

---

## Key Facts

- **Find the ontology**: `find . -name "*.ttl" | head` or check project CLAUDE.md
- **Named invariants**: all `*:Inv_*` instances — read their `rdfs:comment` for the exact rule
- **If ontology and CLAUDE.md prose conflict**: the ontology wins
- **MISMATCH** → fix the ontology; **GAP** → add an axiom; **FAIL** → fix the code
- After any TTL edit: always re-run the validator script before reporting done

---

## Browsing and Searching the Ontology

**Do not read the whole TTL file to understand its structure or find a term.** Use `list.py` instead.

Index mode — compact overview of all classes, properties (with domain/range), and named individuals:

```bash
uv run "$SKILLS_DIR/scripts/list.py" path/to/domain.ttl
```

Search mode — find terms by name or label, returns `ttl://` refs ready for `lookup.py`:

```bash
uv run "$SKILLS_DIR/scripts/list.py" path/to/domain.ttl <filter>
# e.g.
uv run "$SKILLS_DIR/scripts/list.py" docs/domain.ttl tenant
# → ttl://domain.ttl/wash:Tenant  # Tenant
# → ttl://domain.ttl/wash:hasTenant  # has tenant
# → ...
```

Exit 1 on file not found or parse error. Exit 0 with "No terms matching" message when filter finds nothing.

---

## Positions in the File (locate.py)

**Never `grep`, `sed -n` or read a TTL file to find a term, a section, a prefix or a sentence.**
`locate.py` answers each of those with the line numbers, so the Read tool opens exactly the
block (`offset=<start>`, `limit=<end-start+1>`) and an Edit lands in the right section.

| Question | Command |
|---|---|
| Which prefixes, which ontology IRI, which `owl:versionInfo`, and the comments around them (a version rule, the namespace rules)? | `locate.py header <file>` |
| Which sections does the file have, on which lines, with how many terms? | `locate.py outline <file>` |
| On which lines is a term written, and in which section? (every block whose subject it is) | `locate.py where <file> <term>...` |
| Which terms say something about X? (their comments, not only names and labels) | `locate.py text <file> '<regex>' [--prop rdfs:comment]` |
| After which line does a new term of a type go? | `locate.py anchor <file> --type <class> [--prefix p] [--section <part of title>]` |

```bash
uv run "$SKILLS_DIR/scripts/locate.py" outline docs/domain.ttl
# → lines 1867-2029, 26 terms: VENDOR: WEVEND
uv run "$SKILLS_DIR/scripts/locate.py" where docs/domain.ttl wevend:theMachineStoresItsOwnCyclePrices
# → wevend:theMachineStoresItsOwnCyclePrices: lines 2017-2021  [VENDOR: WEVEND]
uv run "$SKILLS_DIR/scripts/locate.py" text docs/domain.ttl 'top.?off' --prop rdfs:comment
# → wash:OpenQuestion…  rdfs:comment  lines 2698-2701  [OPEN QUESTIONS]
#       …value_of_coin_2 (the cents of a dryer's top-off coin) and…
uv run "$SKILLS_DIR/scripts/locate.py" anchor docs/domain.ttl --type wash:VendorFact --prefix wevend --section wevend
# → last match: wevend:pciScopeIsUnanswered, lines 2023-2027  [VENDOR: WEVEND]
#   insert after line 2027
```

A term is a qname, a full IRI or a `ttl://file.ttl/prefix:Name` reference. A section begins at a
banner comment line of `═` or `=` characters followed by a comment line with its title. `where`
exits 1 when a term is not the subject of any statement. Use `lookup.py` for a term's triples,
`locate.py` for where they are.

---

## Looking Up Terms (lookup.py)

Code comments use `ttl://filename.ttl/prefix:LocalName` to reference ontology items.
**Do not read the whole TTL file to resolve terms.** Use the lookup script instead.

**Single or multi-ref** — positional args, blank line between each result:

```bash
uv run "$SKILLS_DIR/scripts/lookup.py" wash:amountCents wash:Payment --base-dir path/to/project
```

**Bare names** (`prefix:Name`) auto-find the TTL when only one exists in `--base-dir`. Use full `ttl://` form when multiple TTL files are present.

**stdin** — pipe refs one per line (removes the shell loop, "Installed N packages" prints once):

```bash
grep -rho 'ttl://[^ ]*' app/ | sort -u | uv run "$SKILLS_DIR/scripts/lookup.py" - --base-dir .
```

**Dangling-ref check** — exit 1 lists any refs that failed to resolve. Grep finds refs, the tool resolves them; a non-zero exit means something in the code points nowhere.

**Closure** — assembles all constraints on a class: its own triples, inherited restrictions (transitive `rdfs:subClassOf` chain expanded), disjointness memberships, and every property that declares it as `rdfs:domain` or `rdfs:range`:

```bash
uv run "$SKILLS_DIR/scripts/lookup.py" wash:Payment --closure --base-dir path/to/project
```

---

## Drawing the Ontology (ttl2mermaid.py)

Emits a Mermaid `classDiagram` block, for a document or a review. Use it when somebody
asks to see, draw, visualise or diagram the ontology.

```bash
uv run "$SKILLS_DIR/scripts/ttl2mermaid.py" path/to/domain.ttl --root wash:MoneyObject
uv run "$SKILLS_DIR/scripts/ttl2mermaid.py" path/to/domain.ttl --prefix payroc --individuals
```

**Always scope it.** `--root prefix:Class` draws that class and every subclass, transitively.
`--prefix p` draws the classes of one namespace. The whole file in one diagram is unreadable,
and the named individuals of a fact or invariant class swamp it.

**What a node states**: a datatype property whose `rdfs:domain` names the class, including
each member of a `owl:unionOf` domain, and a property named by an `owl:Restriction` on the
class, with the bound it states — `1`, `0..1`, `0..0`, `1..*`, or `only prefix:Class`. Read
the domain alone and you lose the restriction-declared properties, which is where most of
the rules of an ontology are written.

**What an edge states**: `rdfs:subClassOf` as inheritance, an object property with both ends
in the view as an association, and a disjointness as a dashed link, from `owl:disjointWith`
and from `owl:AllDisjointClasses`.

**What stderr states**: the classes outside the view, the object properties with one end
outside it, whether individuals are drawn, and every class with a nested restriction, which
a node renders as `only a restriction`. Read those with `lookup.py --closure`. A diagram
that drops a term in silence reads as the whole domain, so quote the stderr notes beside the
diagram whenever the reader is deciding something.

**What no diagram states**: `owl:hasKey`, a property characteristic such as
`owl:AllDisjointProperties`, an `owl:intersectionOf` class definition, and every
`rdfs:comment`. Mermaid has no shape for them. The file remains the specification.

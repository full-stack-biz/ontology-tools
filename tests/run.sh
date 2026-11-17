#!/usr/bin/env bash
# Checks for the orphan report and the literal rendering. No framework: each case runs a
# script against tests/fixtures/orphans.ttl and compares the output to what the fixture
# states must happen.
#
# Usage: tests/run.sh
set -u

here="$(cd "$(dirname "$0")" && pwd)"
scripts="$here/../skills/ontology-tools/scripts"
fixture="$here/fixtures/orphans.ttl"
failures=0

pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; failures=$((failures + 1)); }

expect_orphan() {
    local term="$1" want="$2" out="$3"
    if printf '%s' "$out" | grep -q "Orphan (never referenced): $term\$"; then
        [ "$want" = yes ] && pass "$term reported" || fail "$term reported, must not be"
    else
        [ "$want" = no ] && pass "$term not reported" || fail "$term not reported, must be"
    fi
}

echo "orphan report, no exclusions:"
out="$(uv run "$scripts/validate.py" "$fixture" --check-orphans 2>&1)"
expect_orphan ex:Cited          no  "$out"   # a triple points at it
expect_orphan ex:citedByQname   no  "$out"   # named by qname in a comment
expect_orphan ex:Inv_SomeRule   no  "$out"   # bare name, has an underscore
expect_orphan ex:HostedFields   no  "$out"   # bare name, two capitals
expect_orphan ex:Credit         yes "$out"   # the sentence uses the word, not the term
expect_orphan ex:SelfNamer      yes "$out"   # names only itself
expect_orphan ex:trueOrphan     yes "$out"
expect_orphan ex:excludedFact   yes "$out"   # no flag, so it reports

echo "orphan report, --orphan-exclude-type ex:Fact:"
out="$(uv run "$scripts/validate.py" "$fixture" --check-orphans --orphan-exclude-type ex:Fact 2>&1)"
expect_orphan ex:excludedFact   no  "$out"
expect_orphan ex:trueOrphan     no  "$out"   # also an ex:Fact
expect_orphan ex:Credit         yes "$out"   # not an ex:Fact, still reports
expect_orphan ex:SelfNamer      yes "$out"

echo "--orphan-exclude-type by bare local name:"
out="$(uv run "$scripts/validate.py" "$fixture" --check-orphans --orphan-exclude-type Fact 2>&1)"
expect_orphan ex:excludedFact   no  "$out"

echo "literal rendering:"
# The full ttl:// form, because tests/fixtures holds more than one TTL and a bare
# name only auto-finds the file when the directory has exactly one.
out="$(uv run "$scripts/lookup.py" ttl://orphans.ttl/ex:Bounded --closure --base-dir "$here" 2>&1)"
if printf '%s' "$out" | grep -q "owl:cardinality 1 \]"; then
    pass "an integer cardinality prints bare"
else
    fail "an integer cardinality prints bare — got: $(printf '%s' "$out" | grep cardinality)"
fi
if printf '%s' "$out" | grep -q "rdfs:label  'Bounded'"; then
    pass "a string label keeps its quotes"
else
    fail "a string label keeps its quotes"
fi

expect_line() {
    local want="$1" text="$2"
    if printf '%s' "$text" | grep -qF -- "$want"; then pass "$want"; else fail "$want — absent"; fi
}

echo "mermaid diagram, --root ex:Money:"
out="$(uv run "$scripts/ttl2mermaid.py" "$here/fixtures/mermaid.ttl" --root ex:Money 2>&1)"
expect_line "ex_Money <|-- ex_Payment"            "$out"  # a subclass edge
expect_line "ex_Payment --> ex_Wallet : ex:funds" "$out"  # both ends in the view
expect_line "ex_Payment .. ex_Wallet : disjoint"  "$out"  # owl:disjointWith
expect_line "ex:key 1"                            "$out"  # owl:cardinality
expect_line "ex:handle 0..0"                      "$out"  # owl:maxCardinality 0
expect_line "ex:nested only a restriction"        "$out"  # a nested allValuesFrom
expect_line "Properties that leave this view: ex:leaves"  "$out"
expect_line "Classes outside this view: ex:Outside"       "$out"
if printf '%s' "$out" | grep -qF 'ex:one'; then
    fail "an individual is drawn without --individuals"
else
    pass "no individual without --individuals"
fi
if [ "$(printf '%s' "$out" | grep -cF 'ex:cents xsd:integer')" = 2 ]; then
    pass "a union domain reaches both members"
else
    fail "a union domain reaches both members — got $(printf '%s' "$out" | grep -cF 'ex:cents')"
fi

echo "mermaid diagram, --individuals:"
out="$(uv run "$scripts/ttl2mermaid.py" "$here/fixtures/mermaid.ttl" --root ex:Money --individuals 2>&1)"
expect_line "ex:one()" "$out"

locate="$here/fixtures/locate.ttl"

echo "locate.py outline:"
out="$(uv run "$scripts/locate.py" outline "$locate" 2>&1)"
expect_line "lines 1-12, 1 terms: (before the first section)" "$out"   # the ontology block; prefixes are not terms
expect_line "lines 13-25, 2 terms: CLASSES"                    "$out"
expect_line "lines 26-39, 3 terms: VENDOR FACTS"               "$out"

echo "locate.py where:"
out="$(uv run "$scripts/locate.py" where "$locate" ex:Payment vendor:firstFact ttl://locate.ttl/vendor:secondFact 2>&1)"
expect_line "ex:Payment: lines 20-24  [CLASSES]"              "$out"   # a period inside a string and a [ ] block
expect_line "vendor:firstFact: lines 30-34  [VENDOR FACTS]"   "$out"   # a triple-quoted string over two lines
expect_line "vendor:secondFact: lines 36-37  [VENDOR FACTS]"  "$out"
if uv run "$scripts/locate.py" where "$locate" ex:Missing >/dev/null 2>&1; then
    fail "an absent term exits 0"
else
    pass "an absent term exits 1"
fi

echo "locate.py header:"
out="$(uv run "$scripts/locate.py" header "$locate" 2>&1)"
expect_line "5: @prefix ex: <https://example.org/ex#> ."      "$out"
expect_line "owl:versionInfo 1.2.3"                           "$out"
expect_line "10: # The version rule"                          "$out"
expect_line "4: # A vendor namespace, as a header comment."   "$out"

echo "locate.py text:"
out="$(uv run "$scripts/locate.py" text "$locate" 'top.?off' --prop rdfs:comment 2>&1)"
expect_line "vendor:firstFact  rdfs:comment  lines 30-34  [VENDOR FACTS]"  "$out"
expect_line "vendor:secondFact  rdfs:comment  lines 36-37  [VENDOR FACTS]" "$out"  # case-insensitive
if printf '%s' "$out" | grep -qF 'ex:Payment'; then fail "a term without a match is listed"; else pass "a term without a match is not listed"; fi

echo "locate.py anchor:"
out="$(uv run "$scripts/locate.py" anchor "$locate" --type ex:Fact --prefix vendor 2>&1)"
expect_line "last match: vendor:secondFact, lines 36-37  [VENDOR FACTS]" "$out"
expect_line "insert after line 37" "$out"
out="$(uv run "$scripts/locate.py" anchor "$locate" --type ex:Fact --section classes 2>&1)"
expect_line "No term matches" "$out"   # no individual of ex:Fact is in CLASSES

names="$here/fixtures/names.ttl"
names_config="$here/fixtures/names.toml"

expect_absent() {
    local unwanted="$1" text="$2"
    if printf '%s' "$text" | grep -qF -- "$unwanted"; then fail "$unwanted — present"; else pass "not: $unwanted"; fi
}

echo "names, --check-names --check-wording --check-header:"
out="$(uv run "$scripts/validate.py" "$names" --config "$names_config" --check-names --check-wording --check-header 2>&1)"
status=$?
expect_line "❌ version-iri <https://example.org/names>"     "$out"
expect_line "❌ prior-version <https://example.org/names>"   "$out"
expect_line "❌ label-count ex:Wallet"                       "$out"
expect_line "❌ label-count ex:Unlabelled"                   "$out"
expect_line "❌ label-unique ex:LedgerCopy"                  "$out"
expect_line "❌ label-format ex:BadFormat"                   "$out"
expect_line "❌ label-case ex:Capital"                       "$out"
expect_line "❌ label-case ex:LedgerCopy"                    "$out"
expect_line "❌ type-in-name ex:DecisionRefundFirst"         "$out"
expect_line "❌ separator ex:Inv_MixedName"                  "$out"
expect_line "❌ definition ex:Undefined"                     "$out"
expect_line "❌ exempt-reason ex:debitOrCredit"              "$out"
expect_line "❌ replacement ex:oldTerm"                      "$out"
expect_line "⚠️  genus ex:Refund"                            "$out"
expect_line "⚠️  article ex:theArticle"                      "$out"
expect_line "⚠️  conjunction ex:cashOrCard"                  "$out"
expect_line "⚠️  conjunction ex:debitOrCredit"               "$out"
expect_line "⚠️  negation ex:notPaid"                        "$out"
expect_line "⚠️  verb ex:deviceSendsMoney"                   "$out"
expect_line "⚠️  abbreviation ex:accessKey"                  "$out"
expect_line "⚠️  abbreviation ex:oemDevice"                  "$out"
for passing in "ex:Payment" "ex:CardPayment" "ex:CardCharge" "ex:WeVendDevice" "ex:dnaSample" "ex:cardPresentAndCardNotPresent" "ex:movedTerm" "label-unique ex:Ledger:"; do
    expect_absent " $passing" "$out"
done
expect_absent "label-case ex:Ledger:" "$out"
[ "$status" -eq 1 ] && pass "an error exits 1" || fail "an error exits 1 — got $status"

echo "names, warnings only:"
out="$(uv run "$scripts/validate.py" "$names" --config "$names_config" --check-wording 2>&1)"
status=$?
[ "$status" -eq 0 ] && pass "warnings exit 0" || fail "warnings exit 0 — got $status"
out="$(uv run "$scripts/validate.py" "$names" --config "$names_config" --check-wording --strict 2>&1)"
status=$?
[ "$status" -eq 1 ] && pass "warnings with --strict exit 1" || fail "warnings with --strict exit 1 — got $status"

echo "names, the config next to the file:"
tmp="$(mktemp -d)"
cp "$names" "$tmp/names.ttl"
cp "$names_config" "$tmp/ontology-tools.toml"
out="$(uv run "$scripts/validate.py" "$tmp/names.ttl" --check-names 2>&1)"
expect_absent "label-case ex:WeVendDevice" "$out"   # WeVend from ontology-tools.toml

echo "names, --changed-since:"
git -C "$tmp" init -q
git -C "$tmp" -c user.name=t -c user.email=t@t add names.ttl
git -C "$tmp" -c user.name=t -c user.email=t@t commit -q -m fixture
sed 's/"refund first"/"first refund"/' "$tmp/names.ttl" > "$tmp/edited.ttl" && mv "$tmp/edited.ttl" "$tmp/names.ttl"
out="$(uv run "$scripts/validate.py" "$tmp/names.ttl" --check-names --check-wording --check-header --changed-since HEAD 2>&1)"
expect_line "❌ type-in-name ex:DecisionRefundFirst" "$out"   # its label line changed
expect_absent "ex:Wallet" "$out"                               # unchanged
expect_absent "version-iri" "$out"                             # the ontology block is unchanged
rm -rf "$tmp"

echo "rename.py:"
tmp="$(mktemp -d)"
cp "$here/fixtures/rename.ttl" "$tmp/rename.ttl"
printf '%s\n' "# (ttl://rename.ttl/ex:DecisionRefundFirst) and ex:DecisionRefundFirstOther" > "$tmp/code.ex"
out="$(uv run "$scripts/rename.py" "$tmp/rename.ttl" ex:DecisionRefundFirst ex:RefundOrder --label "refund order" --cite "$tmp" 2>&1)"
expect_line "rename.ttl: 2"                         "$out"   # its subject and the other term's comment
expect_line "code.ex: 1"                            "$out"   # the ttl:// citation only
expect_line "named by: ex:DecisionRefundFirstOther" "$out"
renamed="$(cat "$tmp/rename.ttl")"
expect_line 'rdfs:label   "refund order"'           "$renamed"
expect_line "ex:DecisionRefundFirstOther"           "$renamed" # a longer name is unchanged
expect_absent "ex:DecisionRefundFirst "             "$renamed"
expect_line "(ttl://rename.ttl/ex:RefundOrder) and ex:DecisionRefundFirstOther" "$(cat "$tmp/code.ex")"
if uv run "$scripts/rename.py" "$tmp/rename.ttl" ex:RefundOrder ex:Taken >/dev/null 2>&1; then
    fail "a name in use exits 0"
else
    pass "a name in use exits 1"
fi
if uv run "$scripts/rename.py" "$tmp/rename.ttl" ex:Missing ex:Other >/dev/null 2>&1; then
    fail "an unknown term exits 0"
else
    pass "an unknown term exits 1"
fi
before="$(cat "$tmp/rename.ttl")"
uv run "$scripts/rename.py" "$tmp/rename.ttl" ex:RefundOrder ex:Dry --dry-run >/dev/null 2>&1
[ "$before" = "$(cat "$tmp/rename.ttl")" ] && pass "--dry-run writes nothing" || fail "--dry-run writes nothing"
rm -rf "$tmp"

echo
if [ "$failures" -eq 0 ]; then
    echo "all checks pass"
else
    echo "$failures failed"
fi
exit $((failures > 0))

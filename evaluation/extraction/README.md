# Extraction scorecard

`scripts/extraction_scorecard.py` scores one bfo-agent run for content quality.
`scripts/ontology_audit.py` (QS-G1) asks whether an artifact is well formed. The
scorecard asks whether what was extracted is right:

- in scope;
- placed in a hierarchy and defined;
- encoded at the strength the source text has;
- stable from one run to the next.

It is report-only. It never alters, drops or rolls back a claim, so it is
consistent with FG-0 in both fidelity modes. `--fail-on` turns chosen metrics
into gates, in the same way the audit does.

## Running it

```
python scripts/extraction_scorecard.py run.owl                       # structure, strength, category, surface
python scripts/extraction_scorecard.py run.owl \
    --previous prior.owl \                                           # drift between runs
    --source source.txt \                                            # span checks against the text
    --scope evaluation/extraction/scope.parrot-diet.yaml \           # scope coverage
    --gold evaluation/extraction/gold.json \                         # precision and recall
    --reasoner --bfo ontology/bfo.owl \                              # HermiT satisfiability under BFO
    --json card.json --fail-on flat_share>0.5
```

The text summary prints the headline numbers and the false-universal
candidates. Every list behind them is in the JSON report.

## Metrics

| Headline key | What it measures | What a high value usually means |
| --- | --- | --- |
| `flat_share` | classes whose only named parent is a BFO category | the extractor files terms under BFO but builds no domain hierarchy |
| `definition_absent_share` | classes with no IAO definition | the extractor commits classes it cannot define |
| `possible_relations` | `meta:possibleRelation` notes left unasserted | relations proposed but never resolved |
| `caveat_as_definition` | definitions that read as a bias ("greater importance", "overestimates") | a caveat sentence became the definition |
| `material_participation` | `participates in some X` on a material-entity class | generic sentences ("feathers can be used in…") read as universals |
| `modal_span_axioms` | `some` restrictions on classes whose source span is modal or generic | the same failure, found from the text side |
| `kind_like_individuals` | individuals with lowercase common-noun labels ("lead", "knowledge of morphology") | kinds committed as particulars |
| `has_value_in_class_axioms` | `hasValue` restrictions on classes | instance data in the TBox |
| `taxa_as_material_entities` | "species", "taxon", or a parenthesised binomial under material entity | taxa modelled as physical things, not taxon concepts |
| `label_collisions` | one normalised label on several IRIs | duplicate classes |
| `plural_labels` | labels whose head noun is plural | surface noise, and often a collection class that should be singular |
| `spans_with_line_breaks` | source spans with raw line breaks | PDF text not cleaned before chunking |
| `out_of_scope_share` | classes no scope term or competency question reaches (`--scope`) | scope drift |
| `retention`, `category_conflicts`, `reminted` | against the previous run (`--previous`) | runs that are not cumulative |
| `unsatisfiable` | unsatisfiable classes under BFO disjointness (`--reasoner`) | category clashes, usually after a merge |
| `class_recall`, `axiom_precision`, `axiom_recall` | against a gold annotation (`--gold`) | the one direct measure of correctness |

The strength section also checks `quantifierDefaulted`. The flag
`quantifier_annotation_missing` is set when the property is declared but no
axiom carries it. In that case the export is losing the axiom annotations
that QS-A3 writes, and the defaulted quantifiers can no longer be audited.

## Baseline: the parrot diet runs (Nov 2026)

| | Parrots.owl (Hoenig) | Parrots2.owl (Hoenig + Rosenberg & Cooper) | hand-merged 0.3.1 |
| --- | --- | --- | --- |
| classes | 150 | 179 | 261 |
| `flat_share` | 0.82 | 0.82 | 0.66 |
| `definition_absent_share` | 0.60 | 0.70 | 0.58 |
| `possible_relations` | 25 | 29 | 50 |
| `material_participation` | 16 | 22 | 24 |
| `modal_span_axioms` | 10 | 12 | 23 |
| `kind_like_individuals` | 1 | 7 | 11 |
| `out_of_scope_share` | 0.80 | 0.77 | 0.65 |
| `retention` vs previous | n/a | 0.46 | n/a |
| `category_conflicts` vs previous | n/a | 3 | n/a |
| `reminted` vs previous | n/a | 7 | n/a |
| `unsatisfiable` | 0 | 0 | 0 |

`unsatisfiable` is 0 on each file by itself. The plain union of the two runs
has 3 unsatisfiable classes (`ReadAbundance`, `PresenceAbsenceMethod`,
`IsotopicDiscrimination`): the `category_conflicts`.

The scope file for the pilot is `scope.parrot-diet.yaml`. `out_of_scope_share`
depends on its term list, so compare it only across runs scored with the same
file.

## What the numbers point to in the pipeline

1. **Modality before encoding** (`material_participation`, `modal_span_axioms`).
   Classify each claim as universal, generic, capability ("can") or
   proportional ("often", "most"). Encode only universals as `some`
   restrictions. Send the rest to `meta:caveat` or a disposition, and route a
   participation claim on a material entity to review.
2. **Scope gate** (`out_of_scope_share`, `plural_labels`). Give each job a
   scope file. Mint a class only when a competency question needs it, or when
   it is the parent of one that does. A one-off noun (equipment, a passing
   example) becomes an annotation on the class that mentions it.
3. **Cumulative runs** (`retention`, `category_conflicts`, `reminted`). Load
   the current artifact as context, and match by label, synonym and definition
   before minting. Refuse a commit that changes an existing IRI's BFO category
   or label without an explicit merge.
4. **Hierarchy and definitions at commit** (`flat_share`,
   `definition_absent_share`). In curated mode, a class with only a BFO parent
   and no definition is not committed. Ask for a genus-and-difference
   definition derived from a domain parent, recorded with `derivedDefinition`.
5. **Kind and particular lints** (`kind_like_individuals`,
   `has_value_in_class_axioms`, `taxa_as_material_entities`). Chemical
   elements and substances are classes. Taxon names go to a taxon-concept
   layer. A proper name is an individual only when it is in scope.
6. **Text cleaning** (`spans_with_line_breaks`, `spans_truncated_start`).
   Repair hyphenation and line breaks, and strip running headers, before
   chunking.

## Gold sets

Copy `gold.template.json` and annotate one section of a source, about 40
claims. Keep only what the section states as true of every instance. Generic
and "can" sentences go in no axiom, because the point of the gold set is to
measure whether the extractor can tell the difference.

## Known interaction with the QS-G1 audit

On these runs the audit reports 11 to 22 "punned" class-to-class triples. They
are `meta:distinctFrom` annotations. The audit counts any non-standard
predicate with an IRI object between classes, even when that predicate is
declared as an annotation property. Either the audit should skip declared
annotation properties, or `distinctFrom` should be added to its annotation
list. Until then, `punned_triples` overstates the defect on bfo-agent's own
output.

# BFO-Agent

A Basic Formal Ontology (BFO 2020) grounded dialogue agent that extracts
ontological commitments from philosophical text, validates each proposal
against an OWL-DL reasoner (HermiT), and builds a persistent, typed,
reasoner-consistent knowledge graph with provenance to source passages.

## Primary artifact

The pipeline's first applied output is a formal rendering of David R.
Koepsell's *A Structural Ontology of the Law* (forthcoming, Palgrave 2026):

**DOI:** https://doi.org/10.5281/zenodo.19713357

6,881 classes, 1,678 individuals, derived from 6,445 committed claims.
HermiT-consistent under 9 BFO disjointness axioms and 16 property
declarations.

## Key result

On a 20-probe evaluation, the agent refused 0.933 ± 0.058 of questions
outside its graph, against a baseline of 0.100 ± 0.100 (n=3 runs per
condition), demonstrating that persistent typed memory substantially
reduces LLM confabulation on domain-specific questions.

## License

Code: Apache 2.0
Ontology artifact: CC-BY 4.0

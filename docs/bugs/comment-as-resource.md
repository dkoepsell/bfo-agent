# Bug: comments emitted as malformed rdf:resource

When committing a Proposal that includes a comment annotation on an
individual or class, the storage code can emit:

  <work:comment rdf:resource="#"COMMENT TEXT""/>

This is malformed in two ways: the IRI fragment contains spaces and
quotes, and the comment text appears as a resource reference rather
than a literal.

Found in GeometryofTheGood/working.owl at line 19843, fixed manually
on 2026-04-25 with a sed substitution.

Suspected cause: somewhere in app/storage.py or app/ontology_manager.py,
when a Proposal annotation has type=comment and value="some text", the
serializer is treating the value as an IRI rather than a literal.

To reproduce: build an ontology where the proposer outputs a comment
annotation on an individual.

Fix: locate the comment-emission code and change rdf:resource= to a
literal-typed rdfs:comment element.

Priority: low. Frequency is once per ~6000 commits; salvageable via
sed if it happens again.

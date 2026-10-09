The documents are CONTRACTS. A retrieved span is a clause's operative text, and the category is a clause type (e.g.
"Cap On Liability", "Renewal Term", "Source Code Escrow"): the main test is whether the span is a clause of, or
squarely about, that clause type. A specific condition narrows the type (e.g. "capped at a multiple of fees",
"auto-renews unless notice is given").

Typed properties can mislead across clause types: a Source Code Escrow clause can carry
`cap_basis = multiple_of_fees` and still have nothing to do with a Cap On Liability search.

"Relevant" means a lawyer scanning the results would say "yes, this is the clause you were looking for."

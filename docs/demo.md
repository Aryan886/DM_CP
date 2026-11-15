# Viva / demo guide (about 8 minutes)

Start the app with `.\.venv\Scripts\python.exe -m streamlit run app.py`. It opens on the
real Yahoo Finance snapshot. For the scripted A–E story below, first pick **Teaching matrix**
in the sidebar (it applies all five stocks at τ = 0.65). Use the historical source at the end
(see the last row) to show the same ideas on real companies.

| Time | Do | Say |
|---|---|---|
| 0–1 min | **1 Sets**: show U, S, sector subsets. Put X = {A,B,C}, Y = {B,D}. | Membership, cardinality, ∪ = {A,B,C,D}, ∩ = {B}, X\Y = {A,C}, Y\X = {D}. Sector subsets partition S; X and Y overlap at B. |
| 1–2 min | **2 Direct relation**: point to the definition of Rτ. | Rτ ⊆ S×S, signed, inclusive comparison C ≥ τ. 5 nodes, 3 edges, 11 ordered pairs = 5 + 2·3. |
| 2–3 min | Compare matrix and graph; use the pair inspector on (A,A) and (A,C). | Diagonal pairs are in R though the graph hides loops; one line = two ordered pairs. (A,C): 0.3 < 0.65, not related. |
| 3–4 min | **3 Properties**: transitive card. | Witness (A,B,C): 0.80 ≥ 0.65, 0.70 ≥ 0.65, 0.30 < 0.65. Correlation is not transitive. |
| 4–5 min | **4 Reachability**: path A→C, Q next to R. | BFS path A–B–C. Warshall adds exactly (A,C) and (C,A): 13 true cells. These are reachability links, not new correlations. |
| 5–6 min | Classes table and partition checks. | {A,B,C} and {D,E}; Q passes all three checks, so its classes partition S. A class is not a clique. |
| 6–7 min | **5 Explorer**: presets 0.20 → 0.65 → 0.75 → 0.90. | Edges only disappear; classes split, never merge. Transitivity: yes → no → yes → yes. |
| 7–8 min | Switch to **Historical prices**; set τ = 0.50, then 0.70. Open the validation summary. | Real data, 501 aligned returns. At 0.50 the classes match sectors (banks, energy, staples). At 0.70 transitivity fails with (SLB, COP, XOM) — the same failure, in real stocks. |

## Questions to be ready for

1. **Why a set but a fixed matrix order?** Sets are unordered; matrices need one canonical index so labels, rows and columns agree.
2. **Why returns, and why common dates?** Price levels trend and correlate spuriously; common dates keep every correlation on the same sample.
3. **Why does hiding loops not make R irreflexive?** The matrix passed to the analyzer keeps every (a,a); only the picture omits them.
4. **Counterexample to transitivity?** (A,B,C) at τ = 0.65, verified from the three correlations.
5. **Why classes of Q, not R?** R fails transitivity; Q is reflexive, symmetric and transitive by the path argument.
6. **What does BFS's parent map record?** The vertex from which each vertex was first discovered; following it back gives a fewest-edge path.
7. **Why is k the outer loop in Warshall?** The invariant "paths through intermediates 0..k" needs every pair updated for k before k+1 is used.
8. **Why can raising τ split but not merge?** Rτ₂ ⊆ Rτ₁, so no new edge can appear.
9. **Why can transitivity return?** Removing the edge B–C leaves A–B plus isolated C, which has no broken chain.
10. **Constant returns?** The correlation denominator is zero, so the value is undefined; the app rejects such series by name.
11. **Why is a class not a clique?** Membership requires a path, not a direct edge; A and C are joined only through B.
12. **What did we implement vs use from libraries?** See docs/mathematics.md §7; BFS and Warshall cross-check each other.

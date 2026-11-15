# Mathematical notes

## 1. Sets

`U` is the set of all stock identifiers in the loaded metadata; `S ⊆ U` is the applied selection.
For sector `k`, `U_k = {s ∈ U : sector(s) = k}` and `S_k = S ∩ U_k`. Each stock has exactly one
sector, so the nonempty `S_k` are pairwise disjoint and their union is `S`: they partition `S`.
User subsets `X, Y ⊆ S` may overlap.

`sets.py` implements `X ∪ Y`, `X ∩ Y`, `X \ Y` and `X ⊆ Y` with explicit loops and membership
tests. Sets are displayed in lexicographic order; this order is a display and matrix-index
convention only.

## 2. The direct relation Rτ

For a fixed correlation matrix `C` and threshold `τ ∈ [0, 1]`:

    Rτ = {(a, b) ∈ S × S : C[a][b] ≥ τ}

- Signed: `C = −0.9` never creates an edge. At `τ = 0` an exact `0` does.
- Inclusive and unrounded: `0.70 ≥ 0.70` holds, `0.70 ≥ 0.700000000001` does not.
- Reflexive for valid data, since `C[a][a] = 1 ≥ τ`.
- Symmetric, since `C` is symmetric and both entries are set from one comparison.

The graph `Gτ = (S, Eτ)` has `Eτ = {{a, b} : a ≠ b, (a, b) ∈ Rτ}`. With `n = |S|` and
`m = |Eτ|`: `|Rτ| = n + 2m`, `Σ deg = 2m`, and density `= 2m / (n(n−1))` for `n ≥ 2`
(shown as 0 by convention for `n < 2`).

## 3. Property checks and witnesses

| Property | Definition | Counterexample |
|---|---|---|
| Reflexive | ∀a: aRa | `a` with ¬aRa |
| Symmetric | ∀a,b: aRb ⇒ bRa | `(a, b)` with aRb, ¬bRa |
| Transitive | ∀a,b,c: aRb ∧ bRc ⇒ aRc | `(a, b, c)` with aRb, bRc, ¬aRc |

The checkers loop over indices in canonical order and return the first counterexample, so the
witness is deterministic. Indices may repeat: for `R = {(A,B),(B,A)}` the witness is `(A, B, A)`.
On the empty carrier all three hold vacuously.

Complexities: reflexive `O(n)`, symmetric `O(n²)`, transitive `O(n³)`.

### The chain counterexample

Teaching matrix at τ = 0.65: `C(A,B) = 0.80 ≥ 0.65`, `C(B,C) = 0.70 ≥ 0.65`, but
`C(A,C) = 0.30 < 0.65`. So `(A,B), (B,C) ∈ R` and `(A,C) ∉ R`: R is not transitive.
Correlation above a threshold is not a transitive relation.

## 4. Reachability Qτ

    a Qτ b  ⇔  there is a path of length ≥ 0 from a to b in Gτ
    Qτ = (I ∪ Rτ)*

**Theorem.** For an undirected graph, Qτ is an equivalence relation.

*Proof.* Reflexive: the length-zero path joins `a` to itself. Symmetric: reversing a path from
`a` to `b` gives a path from `b` to `a`, because every edge is undirected. Transitive: joining a
path `a → b` with a path `b → c` gives a walk `a → c`; removing repeated vertices from a walk
leaves a path. ∎

The equivalence class `[a] = {b : a Q b}` is the connected component of `a`. Classes are
nonempty, disjoint and cover `S`; an isolated vertex is a singleton class.

The classes are **not** equivalence classes of Rτ unless Rτ itself passes all three checks.
A class need not be a clique: at τ = 0.65, A and C share a class although `C(A,C) = 0.30`.

## 5. Algorithms

### BFS components — `O(n + m)`

For each unvisited symbol in canonical order, enqueue it and mark it visited; repeatedly dequeue
a vertex, record it, and enqueue (marking immediately) each unvisited neighbor. Marking on
enqueue guarantees no vertex is queued twice.

### BFS shortest path — `O(n + m)`

BFS from the source records `parent[v]`. When the target is reached, follow parents back to the
source and reverse. Source = target returns `[source]` (length 0); an unreachable target returns
`None`. The path minimizes the number of edges, not any correlation distance.

### Warshall closure — `O(n³)` time, `O(n²)` space

    Q = copy(R); Q[i][i] = True for all i
    for k:            # intermediate vertex — outer loop
      for i:
        for j:
          Q[i][j] = Q[i][j] or (Q[i][k] and Q[k][j])

*Invariant:* after processing `k = 0..K`, `Q[i][j]` is true iff there is a path from `i` to `j`
whose internal vertices all lie in `{0..K}`. The intermediate vertex must be the outer loop: if
`k` were inner, `Q[i][k]` could be read before it had been updated for earlier intermediates,
missing paths. The input matrix is copied, never modified.

### Independent cross-check

BFS and Warshall compute reachability by different methods. Every analysis verifies that
`Q[i][j]` is true exactly when `i` and `j` are in the same BFS component, that
`Σ |class|² = |Q|`, and that `R ⊆ Q`.

## 6. Threshold monotonicity

For fixed prepared data and `τ₁ ≤ τ₂`: `C ≥ τ₂ ⇒ C ≥ τ₁`, so `Rτ₂ ⊆ Rτ₁`, `Eτ₂ ⊆ Eτ₁` and
`Qτ₂ ⊆ Qτ₁`. Raising τ removes edges, so classes can split but never merge: the partition
becomes finer and the class count never decreases.

Transitivity is **not** monotone. On the teaching matrix: τ = 0.20 (triangle ABC) transitive;
τ = 0.65 (path A–B–C) not transitive; τ = 0.75 (edge A–B, C isolated) transitive again.

| τ | edges | ordered pairs | R transitive | classes |
|---|---:|---:|---|---|
| 0.00 | 10 | 25 | yes | {A,B,C,D,E} |
| 0.20 | 4 | 13 | yes | {A,B,C}, {D,E} |
| 0.65 | 3 | 11 | no | {A,B,C}, {D,E} |
| 0.70 | 3 | 11 | no | {A,B,C}, {D,E} |
| 0.75 | 2 | 9 | yes | {A,B}, {C}, {D,E} |
| 0.90 | 0 | 5 | yes | five singletons |
| 1.00 | 0 | 5 | yes | five singletons |

## 7. Library versus manual work

| Work | Implementation |
|---|---|
| Set operations, relation construction, property checks, neighbor lists, BFS, paths, Warshall, partition checks | Manual Python loops |
| CSV parsing, pivoting, return arithmetic, Pearson correlation | pandas / NumPy |
| PSD check of the teaching matrix, numerical validation | NumPy |
| Charts and fixed circular layout | Plotly |

Built-in Python set operators are used only as test oracles.

# Inverse folding — definitions, theorems, proofs (draft v1, 2026-10-29)

Status: draft for review by the user. Proofs are written to be checked, not yet machine-checked. Items marked OPEN
are not proved.

## 1. Algorithm graphs (single-rate signal-flow graphs)
An algorithm graph is G = (V, E, f, w, I, O):
- V nodes; I ⊆ V input nodes (no update function), O ⊆ V output nodes.
- E ⊆ V × V edges; w : E → ℕ the delay of each edge (in samples).
- f_v for v ∉ I: a function of the values on v's incoming edges (bit-vector semantics, any operation).
- Semantics on sample index n ∈ ℤ: s_v[n] = x_v[n] for inputs; otherwise
  s_v[n] = f_v( s_u[n − w(u,v)] : (u,v) ∈ E ),  with s_u[m] = σ_u (initial value) for m < 0.
- Well-formed: every directed cycle C has total delay w(C) = Σ_{e∈C} w(e) ≥ 1. Then s is uniquely defined
  (topological order of the zero-delay subgraph inside each sample).

Retiming (Leiserson–Saxe): ρ : V → ℤ with w_ρ(u,v) = w(u,v) + ρ(v) − ρ(u) ≥ 0 for all edges. G_ρ computes
s^ρ_v[n] = s_v[n − ρ(v)] (away from the start-up window). Cycle delays are invariant: w_ρ(C) = w(C).

Copies: a node v with a single in-edge (u,v) and f_v = identity is a copy. Copy elimination replaces u → v → x by
u → x with delay w(u,v) + w(v,x). Write G ≈ G' if they are equal after copy elimination and node renaming.

## 2. Hardware (cycle-level equations)
Registers R; hardware cycle t ∈ ℕ; register values q_r[t]. At every cycle each register either holds
(q_r[t+1] = q_r[t]) or is written: q_r[t+1] = E_{r,t}( q[t], in[t] ), a word expression over current register values
and current inputs. (Trace-guided symbolic execution produces expressions over earlier cycles; normalizing every
read to "the value at the last write" gives this one-step form.)
H is P-periodic from t0 if, for all t ≥ t0: r is written at t iff it is written at t + P, and E_{r,t+P} = E_{r,t}.
Write slot: φ = t mod P (after fixing an origin, §5). Write event: a pair (r, φ) with r written in slot φ.
Inputs: input port j is sampled in a fixed slot φ_j: x_j[n] = in_j[P n + φ_j].

## 3. Folding (forward map, after Parhi et al. 1992)
A folding of G with period P assigns to each non-input node v a slot φ(v) ∈ {0..P−1} and a register b(v) ∈ R, and
inserts transfer registers (copies) as needed, such that the resulting H writes b(v) in slot φ(v) of sample n with
the value s_v[n]. It is LEGAL if every read made while computing s_v[n] (at cycle P n + φ(v)) of an operand
s_u[n − w(u,v)] finds a register whose last write before that cycle stored exactly s_u[n − w(u,v)]
(register-lifetime correctness; this is what Parhi's folding equation and register allocation guarantee).
Write H = Fold(G, φ, b).

## 4. Inverse folding
Input: P-periodic H. For every read of register r' in an expression E_{r,t} with t = P n + φ:
  let t_w = max { t' < t : r' written at t' }   (the last write; t' < t because register reads see the value
                                                 before the current write)
  write t_w = P n' + φ'. The read refers to write event (r', φ') at sample n'; its delay is k = n − n'.
By periodicity (t ≥ t0 + P), the pair (φ', k) depends only on (r, φ, r'), not on n. If the write at t_w is a copy
(E_{r',t_w} = q_{r''}), resolve recursively and add the delays (copy elimination).
Reads of input ports in slot φ_j give edges from input node j with delay 0 when φ_j ≤ φ, else 1 (the sample is
taken later in the same period, so the read sees the previous sample) — same rule as registers.
InvFold(H) = graph with one node per write event, update function E_{r,φ} with register reads replaced by the
resolved (node, delay) references.

Lemma 1 (non-negativity and well-formedness). Every delay k is ≥ 0; k = 0 only if φ' < φ. Hence every cycle of
InvFold(H) has total delay ≥ 1.
Proof. t_w < t gives P n' + φ' < P n + φ, so n' ≤ n; n' = n forces φ' < φ. A cycle of zero-delay edges would need
φ to strictly increase along the cycle and return to its start: impossible. ∎

Theorem 1 (InvFold simulates H). Let H be P-periodic from t0 with all reads resolvable (every register read has a
previous write, inputs aside). Let G_H = InvFold(H) with initial values taken from the hardware state at the first
complete period. Then for every write event (r, φ) and every n ≥ n0: q_r[P n + φ + 1] = s_{(r,φ)}[n].
Proof. Induction on the hardware cycle t = P n + φ in increasing order. The written value is E_{r,φ} evaluated on
q[t]. Each register operand q_{r'}[t] equals the value stored at its last write t_w < t (no write in between, by
definition of t_w), which by the induction hypothesis is s_{(r',φ')}[n']: exactly the operand G_H's semantics uses
(delay n − n'). Copies propagate values unchanged. Inputs are equal by definition of x_j. Base: the first complete
period, where G_H's initial values are the hardware values. ∎

Theorem 2 (round trip). If Fold(G, φ, b) is legal, then InvFold(Fold(G, φ, b)) ≈ G (up to copies and node names),
with every edge delay equal to w(u,v), provided slots are numbered from the same origin as φ.
Proof. In H = Fold(G, φ, b), the only writes are the node writes (b(v), φ(v)) and copies. Take a read made while
computing s_v[n] of the operand intended as s_u[n − w(u,v)]. Legality says the last write of the register read
stored s_u[n − w(u,v)]; following copies, that value was produced by the node write (b(u), φ(u)) at sample
n − w(u,v). So inverse folding resolves the read to node u with delay w(u,v). Nodes correspond one-to-one to node
writes, and update functions are the f_v by construction. ∎

## 5. Identifiability: what the hardware determines
The only arbitrary choice in InvFold is the origin of slot numbering (which slot starts a sample).

Lemma 2 (origin shift = retiming). Moving the origin by δ ∈ {1..P−1} maps a write event with slot φ < δ to sample
n + 1 (relabelled slot φ + P − δ), others to sample n (slot φ − δ). The resulting graph is G_H retimed by
ρ(v) = 1 if φ(v) < δ else 0.
Proof. For an edge u → v with delay k, the new delay is k + ρ(v) − ρ(u) (both endpoints' sample indices shift by
ρ), which is the retiming rule; non-negativity holds by Lemma 1 applied to the new origin. ∎

Theorem 3 (identifiability up to retiming). Let G1, G2 be algorithm graphs with legal foldings producing the same
hardware H (same equations, same input slots). Then G1 ≈ (G2)_ρ for some retiming ρ.
Proof. By Theorem 2, InvFold(H) computed with G1's origin is ≈ G1, and computed with G2's origin is ≈ G2. By
Lemma 2 the two InvFold results differ by a retiming. ≈ is preserved by retiming (copies just move delay). ∎

Granularity (OPEN, needs care). Theorem 3 assumes both foldings write the same intermediate values. A folding may
instead split one algorithm node over several slots (partial sums written to registers) or merge several nodes
into one write. Then the recovered graph is a refinement or coarsening of G. Proposed canonical granularity:
inline every node with exactly one consumer reached through a zero-delay edge (maximal inlining), and compare the
inlined graphs. Claim to prove: two legal foldings of graphs with the same inlined form give InvFold results with
the same inlined form, up to retiming. Counter-example search needed (associativity: (a+b)+c vs a+(b+c) split over
slots gives different intermediate nodes; after inlining both are one node, but the expressions differ
syntactically — equality then needs the canonical expression form of §6).

Corollary 1 (invariants). Retiming preserves the total delay of every cycle; hence for a linear section the
characteristic polynomial of its loops (its poles) is determined by H, and every input-output transfer function is
determined up to a factor z^{−c} (c = ρ(out) − ρ(in)). Identification by poles/zeros/transfer functions modulo pure
delay is therefore well defined on the hardware.

## 6. Canonical identification (sections)
Symmetry group G_obs of transformations not observable from anonymous equations: (i) retiming (Theorem 3 and
Corollary 1), (ii) node renaming, (iii) swapping the two signals of a complex (I/Q) pair, which conjugates a complex
filter, (iv) global fixed-point scale (a gain moved between adjacent sections or a change of the binary point).
Canonical form of a linear section: exact rational transfer matrix with pure delays stripped, I/Q orientation chosen
lexicographically minimal, leading coefficient normalized. Claim (OPEN, for the linear class): two sections have
the same canonical form iff they are related by an element of G_obs. "If": each generator leaves the form
unchanged by construction. "Only if": requires (a) the granularity claim above and (b) that the transfer matrix
determines the section up to G_obs — false in general (different state-space realizations share a transfer
function); the claim must therefore be stated for the realization fixed by the hardware, i.e. for the inlined
graph, not for the transfer function alone. To be settled before writing the paper.

## 7. Extensions (not covered by the theorems above)
- Memories: a RAM with address a(t) periodic in t behaves as registers indexed by (memory, address); Theorem 1
  applies if every address is a function of the slot (and of n linearly for ring buffers: address = c + n mod L,
  giving a delay line of length L). OPEN for data-dependent addresses.
- Multi-rate: a node written in several slots with the same expression is a P-unfolded (polyphase) signal;
  recovering the original rate is inverse unfolding (Parhi & Messerschmitt unfolding), OPEN.
- Non-periodic schedules (data-dependent control): split into modes; each mode periodic. OPEN.
- Pipelined/multi-cycle units: the expression of a write may combine operands read in earlier cycles; trace-guided
  symbolic execution already returns them with their cycles, so the resolution rule applies per operand.

## 8. Complexity
One period of equations with m register reads: computing the write slots per register and resolving each read by
binary search over that register's write slots is O(m log P); copy chains are followed once each with memoization.
Linear in the equation size up to the log factor.

## 9. What to check first (with E1)
Theorem 1 on real recovered equations (E1 design), Lemma 1's k ≥ 0 as a runtime check, Lemma 2 by re-running with
every origin and comparing poles/zeros (Corollary 1), and a counter-example search for the granularity claim with
associativity-varied foldings of the same FIR.

"""X1 attack: 'block transform identified as a DFT/DCT up to output permutation, per-stage scaling and conjugation'.
(1) A DIT FFT that takes its input in bit-reversed order (the port carries x[0], x[2], x[1], x[3] in time order) has
    H = F P_br. No output permutation Q, conjugation and scalar s give s Q H = F (checked exhaustively for N = 4, 8):
    false negative; input permutations must be in the group too.
(2) Scaled DCT (AAN, JPEG): H = D C with a per-OUTPUT diagonal D (not per-stage scalars): false negative.
(3) DFT vs IDFT: IDFT = conj(F) = R F (row reversal k -> -k), so with output permutation or conjugation allowed,
    forward and inverse transforms are the same class (consistent with I/Q orientation being unobservable, but the
    label 'DFT' is then not a statement about direction)."""
import itertools, numpy as np
def F(N): return np.array([[np.exp(-2j * np.pi * k * n / N) for n in range(N)] for k in range(N)])
def br(N):
    b = int(np.log2(N)); return [int(format(i, '0%db' % b)[::-1], 2) for i in range(N)]
def matches_dft(H, N):
    for perm in itertools.permutations(range(N)):
        for G in (H, np.conj(H)):
            Q = G[list(perm), :]
            s = F(N)[0, 0] / Q[0, 0] if abs(Q[0, 0]) > 1e-12 else None
            if s is not None and np.allclose(s * Q, F(N)): return perm
    return None
for N in (4, 8):
    H = F(N)[:, br(N)]
    print('(1) N=%d DIT with bit-reversed input: matches DFT up to output perm/conj/scale: %s' % (N, matches_dft(H, N)))
    print('    control: DIF with bit-reversed output F[br,:] matches with perm', matches_dft(F(N)[br(N), :], N) is not None)
C = np.array([[np.cos(np.pi * (2 * n + 1) * k / 16) * (np.sqrt(1 / 8) if k == 0 else np.sqrt(2 / 8)) for n in range(8)] for k in range(8)])
D = np.diag([1, 0.72, 0.54, 1.9, 1, 0.3, 1.3, 0.25])   # an arbitrary per-output scaling as in AAN
H = D @ C
ok = any(np.allclose((H[list(p), :] * (C[0, 0] / H[list(p), :][0, 0])), C) for p in [tuple(range(8))])
print('(2) scaled DCT D C equals C up to a scalar (identity permutation): %s' % ok)
print('(3) IDFT row-reversed equals DFT:', np.allclose(np.conj(F(8))[[(-k) % 8 for k in range(8)], :], F(8)))

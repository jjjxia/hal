"""X3 attacks (temporal rerolling), checked exhaustively on small slices.
(1) Support-set signatures do not give the key-bit permutation for DES: the 6 subkey bits that enter one S-box have
    identical supports (all 4 outputs of that S-box), so a round with key bits permuted inside an S-box group has the
    same signature; only a semantic check separates them (S1 has no nontrivial input-bit automorphism, so pi IS unique,
    but signatures leave 6! = 720 candidates per S-box, 720^8 per round).
(2) Constants: XTEA's guided round nodes differ by the resolved round constant sum_i (and key-word index), so
    f_i(s,k) = g(s, pi_i(k)) has no solution for any key-bit permutation: false negative on an E4 design.
(3) Feistel test f(L,R,K) xor L independent of L: false negative for modular-add Feistel (TEA/XTEA: L + F(R,K));
    false positive for a round (L,R) -> (L xor R, L xor F(R,K)) that passes but is not a Feistel round and is not
    even invertible (the test looks only at one half)."""
import itertools, random
S1 = [[14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],[0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
      [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],[15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13]]
def s1(x): return S1[((x >> 5) & 1) << 1 | (x & 1)][(x >> 1) & 15]
def f(r, k): return s1(r ^ k)       # one S-box slice of the DES round: 6-bit R part (after E), 6-bit subkey part
def support(fn, nin, nout, which):
    sup = set()
    for r in range(64):
        for k in range(64):
            k2 = k ^ (1 << which)
            d = fn(r, k) ^ fn(r, k2)
            sup |= {b for b in range(nout) if d >> b & 1}
    return frozenset(sup)
print('(1) support of each subkey bit of S1:', [sorted(support(f, 6, 4, j)) for j in range(6)])
perm = lambda p: (lambda k: sum(((k >> p[i]) & 1) << i for i in range(6)))
auts = [p for p in itertools.permutations(range(6)) if all(s1(perm(p)(x)) == s1(x) for x in range(64))]
print('    input-bit automorphisms of S1:', auts)
sig = lambda fn: tuple(sorted(support(fn, 6, 4, j)) for j in range(6))
g_sw = lambda r, k: f(r, perm((1, 0, 2, 3, 4, 5))(k))   # round with subkey bits 0 and 1 swapped
print('    signature(f) == signature(f with k0<->k1):', sig(f) == sig(g_sw), '; functions equal:', all(f(r, k) == g_sw(r, k) for r in range(64) for k in range(64)))

# (2) XTEA half-round as resolved by guided execution (sum is a known control value): constants differ per round
M = 0xffffffff; DELTA = 0x9E3779B9
def xtea_half(i):
    s = (i * DELTA) & M
    return lambda v0, v1, K: (v0 + ((((v1 << 4) ^ (v1 >> 5)) + v1) ^ (s + K[s & 3]))) & M
rng = random.Random(1)
def same_up_to_keyperm(fa, fb, trials=200):
    # necessary condition for f_b(s,k) = f_a(s, pi(k)): with the key fixed to 0, pi(0) = 0 for every permutation
    for _ in range(trials):
        v0, v1 = rng.getrandbits(32), rng.getrandbits(32)
        if fa(v0, v1, [0, 0, 0, 0]) != fb(v0, v1, [0, 0, 0, 0]): return False
    return True
print('(2) XTEA rounds 1 and 2 equal up to a key-bit permutation:', same_up_to_keyperm(xtea_half(1), xtea_half(2)))

# (3) Feistel tests on 4-bit halves
F = lambda r, k: [0, 0, 1, 1, 4, 4, 5, 5, 8, 8, 9, 9, 12, 12, 13, 13][r ^ k]   # an arbitrary non-injective F
def xor_test(fn):   # f(L,R,K) xor L independent of L
    return all(fn(l, r, k) ^ l == fn(0, r, k) for l in range(16) for r in range(16) for k in range(16))
add_feistel = lambda l, r, k: (l + F(r, k)) & 15
print('(3) modular-add Feistel L + F(R,K) passes the xor test:', xor_test(add_feistel))
bad_round_right = lambda l, r, k: l ^ F(r, k)
bad_round_left = lambda l, r, k: l ^ r
imgs = {(bad_round_left(l, r, 3), bad_round_right(l, r, 3)) for l in range(16) for r in range(16)}
print('    round (L,R)->(L^R, L^F(R,K)): right half passes xor test:', xor_test(bad_round_right),
      '; round is a permutation:', len(imgs) == 256, '(%d of 256 images)' % len(imgs))

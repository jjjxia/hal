"""B9: ext.tex 'Coordinate-free schedule test' is false as stated: with partial permutations the hypothesis
'sigma2 = sigma1 o sigma1 where both are defined' can hold vacuously while no single permutation pi with
pi ⊇ sigma1 and pi^2 ⊇ sigma2 exists. Brute force on a 5-element set."""
import itertools
s1 = {'a': 'b', 'c': 'd'}; s2 = {'a': 'd'}
both = [x for x in s2 if x in s1 and s1[x] in s1]
print('points where sigma2 and sigma1 o sigma1 are both defined:', both, '-> hypothesis holds vacuously')
U = 'abcde'
ok = [p for p in itertools.permutations(U) if all(dict(zip(U, p))[x] == y for x, y in s1.items())
      and all(dict(zip(U, p))[dict(zip(U, p))[x]] == y for x, y in s2.items())]
print('permutations pi of %s extending sigma1 with pi^2 extending sigma2: %d' % (U, len(ok)))

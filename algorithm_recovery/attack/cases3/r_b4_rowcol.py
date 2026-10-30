"""B4: frame.row_col_delays assigns row/column delays greedily in sorted entry order and fixes a[i] = 0 whenever an
entry starts a new row AND a new column. If two such components are later joined by an entry, the arbitrary offsets
conflict and the test answers False although a decomposition d_ij = a_i + b_j exists. Sparse block maps (zeros:
reorder buffers with mixing, Haar/wavelet blocks, triangular blocks) are then reported as time-varying.
Matrix (3 outputs x 2 inputs, entries c z^-d): (0,0) d=0, (1,1) d=3, (2,0) d=2, (2,1) d=4, others zero;
a = (0, 1, 2), b = (0, 2) is a decomposition."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot3'))
import frame
from identify import zk, RF
ent = {(0, 0): 0, (1, 1): 3, (2, 0): 2, (2, 1): 4}
ins = [('in', 'x', 0), ('in', 'x', 1)]
H = {('y', i): {x: (zk(ent[(i, j)], 1) if (i, j) in ent else RF([0])) for j, x in enumerate(ins)} for i in range(3)}
ok, a, b = frame.row_col_delays(H, ins)
print('row_col_delays ->', ok, a, b, '; decomposition a=(0,1,2), b=(0,2) valid:', all(ent[(i, j)] == [0, 1, 2][i] + [0, 2][j] for (i, j) in ent))

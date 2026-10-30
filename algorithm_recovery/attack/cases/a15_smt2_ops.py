"""A15: smt2.py does not implement several SMT-LIB bit-vector operators that z3 prints for ordinary datapaths:
signed comparisons (bvslt/bvsle/bvsgt/bvsge: saturation, max/min, sign tests), bvashr (arithmetic shift by a
variable amount), division/remainder, bvnand/bvnor/bvxnor, rotate, repeat, Boolean xor/=>/distinct. The compiler raises
ValueError, so a saturating or comparing node aborts the whole recovery instead of becoming a 'nonlinear' node as
the paper states. Also bvashr/bvlshr/bvshl with an amount >= width follow Python semantics, checked here."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot'))
import smt2
W = {'a': 8, 'b': 8}
for op in ['(ite (bvslt a b) a b)', '(bvashr a b)', '(bvsdiv a b)', '(bvurem a b)', '(bvnand a b)', '((_ rotate_left 1) a)',
           '((_ repeat 2) a)', '(ite (xor (= a b) (bvult a b)) a b)', '(ite (distinct a b) a b)', '(bvcomp a b)']:
    try: smt2.compile_term(op, W); print('%-40s ok' % op)
    except Exception as e: print('%-40s FAILS: %s: %s' % (op, type(e).__name__, e))
fn, _, _ = smt2.compile_term('(bvshl a b)', W); print('bvshl a=1,b=9 ->', fn({'a': 1, 'b': 9}), '(SMT-LIB: 0)')
fn, _, _ = smt2.compile_term('(bvlshr a b)', W); print('bvlshr a=128,b=200 ->', fn({'a': 128, 'b': 200}), '(SMT-LIB: 0)')

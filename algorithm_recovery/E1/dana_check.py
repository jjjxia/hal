"""Variant B probe: DANA register grouping on the E1 netlist vs the RTL registers (scoring only). Same preprocessing
as run_hal.py; DANA configured as in the artifact example (FFs only, control pins clock/enable/reset/set).
Usage: dana_check.py NETLIST.v"""
import sys, os, re, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_hal as R
from run_hal import hal_py
from hal_plugins import dataflow
nl = hal_py.NetlistFactory.load_netlist(sys.argv[1], R.GL)
qconn = dict(re.findall(r'FDRE #\(\s*\.INIT\([^)]*\)\s*\)\s*(\S+)\s*\((?:[^;]*?)\.Q\(([^)]*)\)', open(sys.argv[1]).read(), re.S))
gt = {g.id: re.sub(r'\[\d+\]', '', qconn[g.name]) for g in nl.get_gates(lambda g: g.type.has_property(hal_py.GateTypeProperty.ff))}
R.preprocess(nl)
for size in (8, 4):
    cfg = dataflow.Configuration(nl); cfg.with_min_group_size(size)
    cfg.with_gate_types({hal_py.GateTypeProperty.ff}); cfg.with_expected_sizes([24, 16])
    cfg.with_control_pin_types({hal_py.PinType.clock, hal_py.PinType.enable, hal_py.PinType.reset, hal_py.PinType.set})
    res = dataflow.analyze(cfg)
    groups = [sorted(gt[g.id] for g in gs) for gs in res.get_groups().values()]
    want = collections.Counter(gt.values())
    exact = sum(1 for gr in groups if len(set(gr)) == 1 and len(gr) == want[gr[0]])
    print('min_group_size', size, 'groups', len(groups), 'exactly equal to an RTL register', exact, 'of', len(want))
    for gr in sorted(groups, key=len, reverse=True):
        c = collections.Counter(gr); print('   ', len(gr), dict(c))

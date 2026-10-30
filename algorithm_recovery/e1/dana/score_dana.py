"""White-box scoring of HAL DANA words: FDRE instance -> RTL register via its Q net (or the output port it drives
through an OBUF). Usage: score_dana.py NETLIST.v RESULT.hal"""
import sys, os, re, collections
v = open(sys.argv[1]).read()
q = {}
for m in re.finditer(r'FDRE\s+(?:#\([^;]*?\)\s*)?(\\?\S+)\s*\((.*?)\);', v, re.S):
    mq = re.search(r'\.Q\(([^)]*)\)', m.group(2))
    if mq: q[m.group(1).strip('\\')] = mq.group(1).strip()
ob = {}
for m in re.finditer(r'OBUF\s+(?:#\([^;]*?\)\s*)?\S+\s*\((.*?)\);', v, re.S):
    i = re.search(r'\.I\(([^)]*)\)', m.group(1)); o = re.search(r'\.O\(([^)]*)\)', m.group(1))
    if i and o: ob[i.group(1).strip()] = o.group(1).strip()
def reg(inst): n = q.get(inst, '?'); n = ob.get(n, n); return re.sub(r'\[\d+\]$', '', n)
R = '/home/jiahao/HardwareRE/dynamicRE/reproduction/functional_dynamic_sota/dynamic'
sys.path.append(R + '/build/lib'); os.environ['HAL_BASE_PATH'] = R + '/build/'
import hal_py
hal_py.plugin_manager.load_all_plugins()
nl = hal_py.NetlistFactory.load_netlist(sys.argv[2])
words = []
for m in nl.get_modules():
    if m.name.startswith('DANA_module_'):
        ffs = [g.name for g in m.gates if g.type.has_property(hal_py.ff)]
        if ffs: words.append(ffs)
by_reg = collections.defaultdict(set)
for w in words:
    for g in w: by_reg[reg(g)].add(g)
exact = 0
for r_, gs in sorted(by_reg.items()):
    ok = any(set(w) == gs for w in words); exact += ok
    print('  %-6s %2d bits -> %s' % (r_, len(gs), 'ONE word' if ok else [dict(collections.Counter(reg(g) for g in w)) for w in words if set(w) & gs][:4]))
print('DANA: %d of %d registers exactly one word; %d words' % (exact, len(by_reg), len(words)))

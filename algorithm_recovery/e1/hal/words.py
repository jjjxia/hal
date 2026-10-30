# white-box inspection only (instance names): DANA word -> RTL register bits; also identified arithmetic modules
import sys, os, re, collections
R = '/home/jiahao/HardwareRE/dynamicRE/reproduction/functional_dynamic_sota/dynamic'
sys.path.append(R + '/build/lib'); os.environ['HAL_BASE_PATH'] = R + '/build/'
import hal_py
hal_py.plugin_manager.load_all_plugins()
nl = hal_py.NetlistFactory.load_netlist(sys.argv[1])
for m in sorted(nl.get_modules(), key=lambda m: m.id):
    if m.name.startswith('DANA_module_'):
        ffs = [g for g in m.gates if g.type.has_property(hal_py.ff)]
        regs = collections.Counter(re.sub(r'\[\d+\]$', '', re.sub(r'_reg.*', '', g.name)) for g in ffs)
        if ffs: print('%-16s %3d FFs  %s' % (m.name, len(ffs), dict(regs)))
    elif m.name != 'top_module' and not m.name.startswith('DANA'):
        print('   arith module:', m.name, m.type if hasattr(m, 'type') else '', len(m.gates))

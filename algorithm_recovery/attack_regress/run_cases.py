"""Run the attack reproducers (../attack/cases) against the LIVE invfold instead of the attacker's snapshot.
Each case runs in a temp dir holding a harness.py whose import path points at ../invfold. Output: <case>.live.out"""
import sys, os, subprocess, shutil, tempfile
A = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'attack')
LIVE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'invfold'))
CD = os.environ.get('CASES', 'cases')
cases = sys.argv[1:] or sorted(f[:-3] for f in os.listdir(os.path.join(A, CD)) if f.endswith('.py'))
for c in cases:
    d = tempfile.mkdtemp(prefix='reg_')
    for hn in ('harness.py', 'harness2.py', 'harness3.py', 'harness4.py'):
        if os.path.exists(os.path.join(A, hn)):
            h = open(os.path.join(A, hn)).read()
            for sn in ('snapshot4', 'snapshot3', 'snapshot2', 'snapshot'): h = h.replace("os.path.join(HERE, '%s')" % sn, repr(LIVE))
            open(os.path.join(d, hn), 'w').write(h)
    for extra in ('x1lib.py',):
        if os.path.exists(os.path.join(A, extra)):
            open(os.path.join(d, extra), 'w').write(open(os.path.join(A, extra)).read().replace("os.path.join(HERE, 'snapshot')", repr(LIVE)))
    src = open(os.path.join(A, CD, c + '.py')).read()
    for sn in ('snapshot4', 'snapshot3', 'snapshot2', 'snapshot'): src = src.replace("'%s'" % sn, repr(LIVE))
    os.makedirs(os.path.join(d, 'cases')); open(os.path.join(d, 'cases', c + '.py'), 'w').write(src)
    for extra in os.listdir(os.path.join(A, CD)):
        if extra.endswith('.py') and extra != c + '.py' and not extra[0].isdigit() and not extra.startswith(('a', 'b', 'x')):
            shutil.copy(os.path.join(A, CD, extra), os.path.join(d, 'cases', extra))
    if os.path.isdir(os.path.join(A, 'rtl')): shutil.copytree(os.path.join(A, 'rtl'), os.path.join(d, 'rtl'))
    try:
        r = subprocess.run([sys.executable, c + '.py'], cwd=os.path.join(d, 'cases'), capture_output=True, text=True, timeout=600)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired: out = 'TIMEOUT (600 s)'
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), c + '.live.out'), 'w').write(out)
    print('==', c, '\n', '\n '.join(out.strip().splitlines()[-6:]), flush=True)
    shutil.rmtree(d, ignore_errors=True)

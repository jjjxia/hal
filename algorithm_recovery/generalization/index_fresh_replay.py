#!/usr/bin/env python3
"""Freeze an existing Index program, generate new pads, run native trace/replay.

Evaluation driver only. Does not recover, alter or regenerate the frozen program.
Use prepare OUTPUT then run OUTPUT. Both commands refuse stale/mutated identities.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
PROGRAM = ROOT / 'build/guarded_state_v1/controller/state_machine'
SOURCE = Path('/home/jiahao/HardwareRE/dynamicRE/harder_netlists/consumer_bitstreams/artifacts/netlists/indexcontroller_fpga_2_26_fw/recovered.json')
CELLS = Path('/usr/share/yosys/ice40/cells_sim.v')
CLI = ROOT / 'build/bin/hal_plugins/hal-recover'
SEED, EVENTS = 0xC220261101, 8192


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def snapshot():
    paths = sorted(p for p in PROGRAM.rglob('*') if p.is_file())
    files = {str(p.relative_to(PROGRAM)): sha(p) for p in paths}
    digest = hashlib.sha256()
    for name, identity in files.items():
        digest.update(name.encode() + b'\0' + bytes.fromhex(identity))
    return {'program': str(PROGRAM), 'program_files': files,
            'program_tree_sha256': digest.hexdigest(),
            'dependencies': {str(p): sha(p) for p in (SOURCE, SOURCE.with_suffix('.v'), CELLS, CLI)}}


def verify(manifest):
    if snapshot() != manifest['frozen']:
        raise RuntimeError('Frozen program or source/simulator/CLI identity changed')


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 'INDEX_FROZEN_PROGRAM_FRESH_REPLAY_V1', 'status': 'FROZEN',
                'frozen_at_utc': now(), 'frozen': snapshot(), 'driver_sha256': sha(__file__),
                'protocol_sha256': sha(Path(__file__).with_name('INDEX_FRESH_PROTOCOL.txt')),
                'seed': SEED, 'events': EVENTS, 'serial': True,
                'address_space_bytes': 16 * 1024**3, 'wall_timeout_seconds': 10800}
    save(out / 'manifest.json', manifest)  # deliberately before generating inputs
    module = next(iter(json.loads(SOURCE.read_text())['modules'].values()))
    bits = sorted(b for p in module['ports'].values() if p['direction'] != 'output' for b in p['bits'])
    assert len(bits) == len(set(bits)) == 80 and all(type(b) is int for b in bits)
    assert {2, 3, 47} <= set(bits)
    values = dict.fromkeys(bits, 0)
    rng = random.Random(SEED)
    events = []
    blocks = [dict(begin=1024*i, end_exclusive=1024*(i+1), name=name)
              for i, name in enumerate(('baseline', 'unequal_rates', 'clock3_pause', 'clock2_pause',
                                        'phase_offset', 'midrun_reset', 'fast_clocks', 'fast_data'))]
    for t in range(EVENTS):
        block = t // 1024
        if t >= 32:
            for b in bits:
                if b not in (2, 3, 47) and rng.randrange(8) < (6 if block == 7 else 1):
                    values[b] ^= 1
        values[47] = int(not (t < 32 or 5120 <= t < 5152))
        if block == 0: values[3], values[2] = t % 2, (t // 2) % 2
        elif block == 1: values[3], values[2] = (t // 2) % 2, (t // 3) % 2
        elif block == 2: values[3], values[2] = 0, t % 2
        elif block == 3: values[3], values[2] = t % 2, 1
        elif block == 4: values[3], values[2] = (t + 1) % 2, ((t + 2) // 5) % 2
        elif block == 5: values[3], values[2] = (t // 2) % 2, (t // 3) % 2
        elif block == 6: values[3], values[2] = t % 2, (t + 1) % 2
        else: values[3], values[2] = t % 2, (t // 2) % 2
        events.append([values[b] for b in bits])
    stimulus = {'schema': 'PHYSICAL_PAD_STIMULUS_V1', 'source_sha256': sha(SOURCE),
                'description': 'Frozen controller program fresh seeded pads; clock pauses/rates/phases and midrun reset. Binary automatic inout release; not a board-protocol claim.',
                'input_bits': bits, 'events': events, 'windows': {'fresh': list(range(EVENTS))}}
    save(out / 'stimulus.json', stimulus)
    manifest.update(status='PREPARED', stimulus_sha256=sha(out / 'stimulus.json'),
                    stimulus_created_at_utc=now(), blocks=blocks,
                    scope='Finite fresh external-pad replay of frozen code; known observations only, actual RAM INIT, explicit two-state unknown completions. No universal runtime or valid-board-protocol claim.')
    verify(manifest)
    save(out / 'manifest.json', manifest)
    print(json.dumps({'status': 'PREPARED', 'output': str(out), 'events': EVENTS,
                      'program_tree_sha256': manifest['frozen']['program_tree_sha256']}))


def limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (16 * 1024**3, 16 * 1024**3))


def run(out):
    manifest = json.loads((out / 'manifest.json').read_text())
    verify(manifest)
    if sha(out / 'stimulus.json') != manifest['stimulus_sha256']:
        raise RuntimeError('Stimulus changed after preparation')
    receipt_path = out / 'run.json'
    if receipt_path.exists():
        raise RuntimeError('Preserve previous run; use a new output directory')
    receipt = {'status': 'RUNNING', 'started_at_utc': now(), 'stages': [],
               'manifest_sha256': sha(out / 'manifest.json'), 'driver_sha256': sha(__file__)}
    save(receipt_path, receipt)
    env = dict(os.environ, HAL_BASE_PATH=str(ROOT/'build'), OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    env['LD_LIBRARY_PATH'] = str(ROOT/'build/lib') + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    start = time.monotonic()
    commands = [('trace', [str(CLI), 'trace', str(SOURCE), str(SOURCE.with_suffix('.v')), str(CELLS), str(out/'trace'), '--stimulus-json', str(out/'stimulus.json')]),
                ('replay', [str(CLI), 'replay-state', str(PROGRAM), str(out/'trace'), str(out/'replay')])]
    try:
        for name, command in commands:
            verify(manifest)
            row = {'name': name, 'command': command, 'started_at_utc': now(), 'status': 'RUNNING'}
            receipt['stages'].append(row)
            save(receipt_path, receipt)
            print('START ' + name, flush=True)
            with (out/(name+'.log')).open('w') as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=max(1, 10800-(time.monotonic()-start)), preexec_fn=limits)
            row.update(exit_code=result.returncode, ended_at_utc=now(), status='PASS' if result.returncode == 0 else 'FAIL')
            save(receipt_path, receipt)
            if result.returncode:
                raise RuntimeError(name + ' failed; preserve logs and frozen program')
        result = json.loads((out/'replay/replay.json').read_text())
        if result['events'] != EVENTS or result['status'] != 'SAVED_STATE_PROGRAM_ORIGINAL_GATE_REPLAY_PASS':
            raise RuntimeError('Replay did not establish the predeclared complete finite sequence')
        if result['training_window_identity_matched'] or result['unobserved_output_fields']:
            raise RuntimeError('Freshness or original complete observation contract failed')
        verify(manifest)
        receipt.update(status='PASS', program_unchanged=True, replay_sha256=sha(out/'replay/replay.json'),
                       counts=result['counts'], events=result['events'])
    except Exception as error:
        receipt.update(status='ERROR', error=str(error), exception_type=type(error).__name__)
    finally:
        receipt.update(ended_at_utc=now(), elapsed_seconds=time.monotonic()-start)
        save(receipt_path, receipt)
    print(json.dumps({'status': receipt['status'], 'output': str(out)}))
    return 0 if receipt['status'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'run'))
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    if args.mode == 'prepare':
        prepare(out)
        return 0
    return run(out)


if __name__ == '__main__':
    raise SystemExit(main())

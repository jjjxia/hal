#!/usr/bin/env python3
"""Development-only negative controls for C2 exact finite execution.

Usage: c2_semantic_controls.py CASE OUTPUT
CASE must be the controlled quantized_shared fixture. Recovery runs on the
original anonymous equations before oracle/trace loading. Mutations then affect
only in-memory recovered expressions or one supplied finite-prefix datum.
These are evaluator controls, never held-out DUT outcomes or recovery changes.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import sys
import traceback

import c2_score as score


def compact(result):
    return {key: value for key, value in result.items() if key != 'errors'} | {
        'mismatch_count': len(result.get('errors', [])),
        'first_mismatches': result.get('errors', [])[:8],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    case, out = args.case.resolve(), args.output.resolve()
    score.need(case.name == 'quantized_shared', 'Only the controlled quantized_shared fixture is authorized')
    score.need(not out.exists() or not any(out.iterdir()), 'output directory must be empty')
    out.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'C2_SEMANTIC_CONTROLS_V1', 'status': 'INCOMPLETE', 'controls': {},
              'scope': 'Development evaluator mutation probes; finite evidence only. No DUT failure or new recovery algorithm claim.'}
    score.write_json(out / 'result.json', report)
    try:
        invfold, identities = score.load_frozen()
        input_hashes = {name: score.sha(case / name) for name in ('recovery.json', 'oracle.json', 'trace.json')}
        recovery = json.loads((case / 'recovery.json').read_text())
        score.validate_recovery(recovery)
        model = invfold.Folded(copy.deepcopy(recovery['equations']), copy.deepcopy(recovery['meta']))
        period = model.period()
        score.need(period is not None, 'baseline period discovery failed')
        repetition = score.check_repetition(recovery, period)
        model.build()
        score.need(not model.report['errors'], 'baseline recovery reported errors')
        folder = out / 'baseline'
        folder.mkdir()
        graph = score.save_graph(folder, model, input_hashes['recovery.json'], 'DISCOVERED', repetition)
        baseline_graph_hash = score.sha(folder / 'graph.json')
        # Reference identities/values are unavailable until raw recovery is saved.
        oracle = json.loads((case / 'oracle.json').read_text())
        trace = json.loads((case / 'trace.json').read_text())
        score.need(oracle.get('family') == 'quantized' and oracle.get('variant') == 'shared'
                   and oracle.get('source') == 'known registered reference graph', 'not the controlled development fixture')
        structural = score.score_graph(graph, recovery, oracle)
        semantic = score.finite_semantics(invfold, model, recovery, trace)
        report['baseline'] = {'provenance_structural': compact(structural), 'finite_semantic': compact(semantic)}
        report['identities'] = {'inputs': input_hashes, 'frozen_recovery': identities,
                                'baseline_graph_sha256': baseline_graph_hash,
                                'control_script_sha256': score.sha(__file__), 'scorer_sha256': score.sha(score.__file__)}
        score.write_json(folder / 'semantic.json', semantic)
        score.need(structural['status'] == semantic['status'] == 'PASS', 'baseline must pass before negative controls')
        candidates = [(key, definition) for key, definition in sorted(model.nodes.items())
                      if model.eq[definition['T'], definition['tgt']].startswith('(bvashr ')]
        score.need(candidates, 'no standalone arithmetic-rounding node in fixture')
        key, definition = candidates[0]
        equation_key = definition['T'], definition['tgt']
        original = model.eq[equation_key]
        replacements = {
            'wrong_signed_shift': original.replace('bvashr', 'bvlshr', 1),
            'wrong_rounding_shift': re.sub(r'\(_ bv1 (\d+)\)', r'(_ bv2 \1)', original, count=1),
        }
        for name, replacement in replacements.items():
            score.need(replacement != original, 'mutation did not change expression: ' + name)
            mutant = copy.deepcopy(model)
            mutant.eq[equation_key] = replacement
            folder = out / name
            folder.mkdir()
            mutated_graph = score.save_graph(folder, mutant, input_hashes['recovery.json'],
                                             'POST_RECOVERY_EVALUATOR_MUTATION', repetition)
            # Changing an operator/constant leaves the original operand paths,
            # producers, and sample coordinates intact in these two controls.
            provenance = score.score_graph(mutated_graph, recovery, oracle)
            result = score.finite_semantics(invfold, mutant, recovery, trace)
            score.write_json(folder / 'semantic.json', result)
            report['controls'][name] = {
                'status': 'DETECTED' if result['status'] == 'FAIL' else 'MISSED',
                'changed_node': list(key), 'before': original, 'after': replacement,
                'exact_expression_identity': 'CHANGED',
                'provenance_structural': compact(provenance), 'finite_semantic': compact(result),
            }
            score.need(provenance['status'] == 'PASS', 'operand-preserving mutation changed provenance: ' + name)
        score.need(semantic['prefix_histories'], 'no actual finite prefix history was demanded')
        history = semantic['prefix_histories'][0]
        altered_trace = copy.deepcopy(trace)
        word, time, previous = history['word'], history['write_cycle'], history['value']
        score.need(time < semantic['run_start'], 'selected history is not before graph execution')
        # Corrupt one unique actual-prefix value. Future expected states and
        # every external stimulus remain byte-for-byte equal to the baseline.
        altered_trace['after'][word][time] = previous ^ 1
        result = score.finite_semantics(invfold, model, recovery, altered_trace)
        folder = out / 'wrong_initial_history'
        folder.mkdir()
        score.write_json(folder / 'semantic.json', result)
        report['controls']['wrong_initial_history'] = {
            'status': 'DETECTED' if result['status'] == 'FAIL' else 'MISSED',
            'changed_prefix_values': 1, 'word': word, 'write_cycle': time,
            'before': previous, 'after': previous ^ 1, 'exact_expression_identity': 'UNCHANGED',
            'provenance_structural': compact(structural), 'finite_semantic': compact(result),
        }
        report['same_edge_read'] = 'Covered by c2_score.fault_probes; not duplicated here.'
        score.need({name: score.sha(case / name) for name in input_hashes} == input_hashes, 'original case changed')
        score.need(score.sha(out / 'baseline/graph.json') == baseline_graph_hash, 'original recovered graph changed')
        score.need({name: score.sha(score.INV / name) for name in identities} == identities, 'frozen core changed')
        report['status'] = 'PASS' if all(row['status'] == 'DETECTED' for row in report['controls'].values()) else 'FAIL'
    except Exception as error:
        report.update(status='ERROR', error=str(error), exception_type=type(error).__name__)
        (out / 'error.txt').write_text(traceback.format_exc())
    score.write_json(out / 'result.json', report)
    print(json.dumps({'status': report['status'], 'output': str(out),
                      'controls': {name: row['status'] for name, row in report['controls'].items()}}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())

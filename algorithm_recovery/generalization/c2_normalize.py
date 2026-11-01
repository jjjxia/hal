#!/usr/bin/env python3
"""Controlled C2 evaluation: remove literal identity moves with checked history.

Usage: c2_normalize.py CASE_DIR SCORE_DIR OUTPUT_DIR
       [--compare OTHER_OUTPUT/core_signature.json ...]

SCORE_DIR contains the frozen scorer's result.json and p1_o0/{graph,score}.json.
Only the controlled registered graphs are scored by this adapter. This is not a
general optimizer and does not change frozen InvFold or the frozen raw scorer.
"""
import argparse
import collections
import copy
import importlib
import json
from pathlib import Path
import sys
import traceback

import c2_score as raw


def load(path):
    return json.loads(Path(path).read_text())


def source_time(graph, source, sample):
    return graph['origin'] + (sample - source[3]) * graph['period'] + source[2]


def literal_copies(graph):
    """No oracle: exact identity syntax, one leaf, and unchanged bit width."""
    nodes = {tuple(node['id']): node for node in graph['nodes']}
    aliases = {}
    for key, node in nodes.items():
        if key[0] != 'sig':
            continue
        term = raw.expand_lets(raw.parse_term(node['raw_smt2']))
        if not isinstance(term, str) or len(node['operands']) != 1:
            continue
        operand = node['operands'][0]
        if operand['path'] != [] or term.strip('|') != operand['symbol'] or operand['width'] != node['width']:
            continue
        source = operand['source']
        if source[0] not in ('sig', 'in'):
            continue
        if source[0] == 'sig':
            raw.need(tuple(source[:3]) in nodes, 'copy refers to absent result node')
            raw.need(nodes[tuple(source[:3])]['width'] == node['width'], 'copy source width mismatch')
        aliases[key] = list(source)
    # A copy cycle contains initialized state, so this small normalizer rejects
    # it rather than deleting it or inventing a state-free representation.
    for start in aliases:
        seen = set()
        key = start
        while key in aliases:
            raw.need(key not in seen, 'COPY_CYCLE_REJECTED: retain original raw initialized graph')
            seen.add(key)
            key = tuple(aliases[key][:3])
    return aliases


def check_prefix(graph, recovery, trace, conditions, run_start):
    """Check only demanded copy equalities, using actual finite prefix writes."""
    meta = recovery['meta']
    rows = []

    def value(source, sample):
        physical = source_time(graph, source, sample)
        raw.need(0 <= physical < run_start, 'copy history requires unavailable or post-start physical time')
        if source[0] == 'sig':
            word = source[1]
            raw.need(physical < len(meta['write_enable'][word]) and meta['write_enable'][word][physical] == 1,
                     'copy history is not an actual represented prefix write')
            result = trace['after'][word][physical]
            width = meta['widths'][word]
        else:
            raw.need(source[0] == 'in', 'unsupported copy history source')
            result = trace['inputs'][source[1]][physical]
            width = meta['ports']['in'][source[1]]
        raw.need(type(result) is int, 'unknown required copy history; no zero-fill permitted')
        return result & ((1 << width) - 1), physical

    for condition in conditions:
        key, sample, source = condition['copy'], condition['sample'], condition['source']
        actual, copy_time = value(list(key) + [0], sample)
        expected, parent_time = value(source, sample)
        rows.append(dict(condition, copy_write_cycle=copy_time, parent_physical_cycle=parent_time,
                         copy_value=actual, source_value=expected, equal=actual == expected))
    return {'status': 'PASS' if all(row['equal'] for row in rows) else 'FAIL', 'equalities': rows,
            'checked_equalities': len(rows), 'run_start': run_start,
            'scope': 'Finite boundary equalities required by literal-copy substitution, including cumulative chain delays. Values are actual prefix writes or inputs, never assumed zero; no universal history or schedule proof.'}


def normalize(graph, recovery, trace, run_start):
    aliases = literal_copies(graph)
    raw.need((run_start - graph['origin']) % graph['period'] == 0, 'run start is not a graph sample boundary')
    first_sample = (run_start - graph['origin']) // graph['period']
    result = copy.deepcopy(graph)
    result['schema'] = 'C2_LITERAL_COPY_NORMALIZED_GRAPH_V1'
    result['nodes'] = [node for node in result['nodes'] if tuple(node['id']) not in aliases]
    conditions = {}
    replacement_count = 0
    for node in result['nodes']:
        for operand in node['operands']:
            source = list(operand['source'])
            chain = []
            while tuple(source[:3]) in aliases:
                key = tuple(source[:3])
                parent = aliases[key]
                # After eliminating earlier links, the next move may require
                # a longer history than any one raw edge did. Check that full
                # enlarged interval, not just the original one-frame boundary.
                for sample in range(first_sample - source[3], first_sample):
                    conditions[key, sample] = {'copy': list(key), 'sample': sample, 'source': list(parent)}
                chain.append({'copy': list(key), 'accumulated_delay': source[3], 'parent': list(parent)})
                source = parent[:3] + [source[3] + parent[3]]
            if chain:
                operand['raw_source'] = operand['source']
                operand['source'] = source
                operand['removed_chain'] = chain
                replacement_count += 1
    prefix = check_prefix(graph, recovery, trace, [conditions[key] for key in sorted(conditions)], run_start)
    result['literal_copy_normalization'] = {
        'copies_removed': len(aliases), 'operand_occurrences_rewritten': replacement_count,
        'removed': [{'id': list(key), 'source': source, 'proof': 'let-expanded same-width single-leaf identity'}
                    for key, source in sorted(aliases.items())],
        'prefix_status': prefix['status'], 'required_prefix_equalities': len(conditions),
        'run_start': run_start, 'compute_functions_changed': 0, 'observation_nodes_removed': 0,
        'scope': 'Controlled evaluation only. Literal identity substitution and cumulative delays; adoption requires all listed real prefix equalities and the prior raw finite semantic PASS. No fitted identities or oracle-guided normalization.'}
    result['copies_removed'] = len(aliases)
    return result, prefix


def normalized_oracle(oracle, window):
    """Scoring-only transformation; never used to find or eliminate copies."""
    events = {}
    for event in oracle['events']:
        if event['target'].endswith(' - i_D'):
            key = event['logical'], event['sample']
            raw.need(key not in events, 'nonunique oracle operation/sample identity')
            events[key] = event

    def follow(producer):
        producer = list(producer)
        seen = set()
        while producer[0] == 'op':
            key = producer[1], producer[2]
            raw.need(key in events, 'oracle producer event absent')
            event = events[key]
            if event['kind'] != 'copy':
                return producer
            raw.need(key not in seen, 'cyclic oracle copy provenance')
            seen.add(key)
            raw.need(len(event['operands']) == 1 and event['operands'][0]['width'] == event['width'],
                     'declared oracle copy is not same-width/single-operand')
            producer = list(event['operands'][0]['producer'])
        return producer

    result = copy.deepcopy(oracle)
    result['events'] = []
    selected_times = set(window)
    for event in oracle['events']:
        if event['kind'] == 'copy':
            continue
        event = copy.deepcopy(event)
        # Prefix compute events supply identities/history, not scored equations.
        # In a legal copy-cut adapter their old physical operands may mention
        # omitted copy boundaries; do not demand those unscored inventories.
        if event['T'] in selected_times:
            for operand in event['operands']:
                operand['producer'] = follow(operand['producer'])
        result['events'].append(event)
    return result


def execute_saved(graph, recovery, trace, run_start, run_end):
    """Execute the actual saved normalized graph, with cached own writes."""
    raw.load_frozen()
    smt = importlib.import_module('smt2')
    period, origin = graph['period'], graph['origin']
    first, last = (run_start - origin) // period, (run_end - origin) // period
    raw.need(run_end > run_start and origin + last * period == run_end, 'invalid finite execution bounds')
    compiled = []
    for node in sorted(graph['nodes'], key=lambda n: (n['id'][0] == 'out', n['id'][2], n['id'][1])):
        widths = {row['symbol']: row['width'] for row in node['operands']}
        function, _, width = smt.compile_term(node['raw_smt2'], widths)
        raw.need(width == node['width'], 'normalized result width changed')
        compiled.append((node, function))
    values, histories = {}, {}
    counts = collections.Counter()
    errors = []
    meta = recovery['meta']

    def read(source, sample):
        kind, name, phase, delay = source
        producer_sample = sample - delay
        physical = source_time(graph, source, sample)
        if kind == 'in':
            raw.need(0 <= physical < len(trace['inputs'][name]), 'normalized input time outside trace')
            value = trace['inputs'][name][physical]
            raw.need(type(value) is int, 'unknown demanded input')
            return value & ((1 << meta['ports']['in'][name]) - 1)
        raw.need(kind == 'sig', 'unsupported normalized source')
        if producer_sample >= first:
            raw.need((name, phase, producer_sample) in values, 'normalized source missing or violates zero-delay execution order')
            return values[name, phase, producer_sample]
        raw.need(0 <= physical < run_start and meta['write_enable'][name][physical] == 1,
                 'normalized history is not an actual finite prefix write')
        value = trace['after'][name][physical]
        raw.need(type(value) is int, 'unknown normalized prefix history')
        histories[name, physical] = value
        return value & ((1 << meta['widths'][name]) - 1)

    for sample in range(first, last):
        for node, function in compiled:
            environment = {}
            for operand in node['operands']:
                value = read(operand['source'], sample)
                symbol = operand['symbol']
                raw.need(symbol not in environment or environment[symbol] == value, 'inconsistent duplicate normalized operand')
                environment[symbol] = value
            value = function(environment)
            kind, name, phase = node['id']
            physical = origin + sample * period + phase
            if kind == 'sig':
                values[name, phase, sample] = value
                expected = trace['after'][name][physical]
                label = 'retained_word'
            else:
                expected = trace['outputs'][name][physical]
                label = 'output'
            if expected is None:
                counts['unknown_' + label + '_observations'] += 1
                continue
            counts[label + '_observations'] += 1
            counts[label + '_bits'] += node['width']
            if value != expected & ((1 << node['width']) - 1):
                errors.append({'id': node['id'], 'T': physical, 'expected': expected, 'actual': value})
    enough = counts['retained_word_observations'] > 0 and counts['output_observations'] > 0
    return {'status': 'FAIL' if errors else 'PASS' if enough else 'INSUFFICIENT_OBSERVATIONS',
            'run_start': run_start, 'run_end_exclusive': run_end, 'counts': dict(counts), 'errors': errors,
            'prefix_histories': [{'word': w, 'write_cycle': t, 'value': v} for (w, t), v in sorted(histories.items())],
            'scope': 'Finite execution of saved normalized graph; only external inputs and real pre-run history writes supplied. No later reference word injections. Exact stored bit-vector functions retain rounding and overflow; no SMT or universal runtime claim.'}


def core_signature(graph, oracle):
    """Oracle labels only here: compare A/B sample graphs at declared output."""
    events = {(row['T'], row['target']): row for row in oracle['events']}
    core, outputs = [], []
    for node in graph['nodes']:
        event = events[node['T'], node['target']]
        if node['id'][0] == 'out' and node['id'][2] != graph['period'] - 1:
            continue
        mapped = {}
        sample = (node['T'] - graph['origin']) // graph['period']
        for operand in node['operands']:
            source = operand['source']
            physical = source_time(graph, source, sample)
            if source[0] == 'sig':
                producer = events[physical, source[1] + ' - i_D']
                label = ['op', producer['logical'], event['sample'] - producer['sample']]
            else:
                label = ['input', source[1], event['sample'] - (physical - oracle['origin']) // oracle['period']]
            mapped[tuple(operand['path'])] = label

        def replace(term, path=()):
            if path in mapped:
                return {'operand': mapped[path]}
            if isinstance(term, str):
                return term
            return [term[0]] + [replace(child, path + (i,)) for i, child in enumerate(term[1:])]

        row = {'logical': event['logical'], 'width': node['width'],
               'function': replace(raw.expand_lets(raw.parse_term(node['raw_smt2']))),
               'operands': [{'path': list(path), 'producer': label} for path, label in sorted(mapped.items())]}
        if node['id'][0] == 'sig':
            core.append(row)
        else:
            row['port'] = node['id'][1]
            outputs.append(row)
    raw.need({row['logical'] for row in core} == {'A', 'B'} and len(core) == 2, 'controlled normalized graph is not exactly logical A/B')
    return {'schema': 'C2_CONTROLLED_CORE_SIGNATURE_V1', 'family': oracle['family'],
            'signature': {'nodes': sorted(core, key=lambda x: x['logical']), 'sample_outputs': sorted(outputs, key=lambda x: x['port'])},
            'scope': 'Scoring labels only, never normalization inputs. Exact A/B functions and occurrence-labelled producer/sample delays; output observed at each implementation period minus one. Does not equate intermediate hardware-cycle outputs across different schedules.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('scores', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--compare', type=Path, action='append', default=[])
    args = parser.parse_args()
    case, scores, out = args.case.resolve(), args.scores.resolve(), args.output.resolve()
    raw.need(not out.exists() or not any(out.iterdir()), 'normalization output must be empty')
    out.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'C2_CONTROLLED_NORMALIZATION_V1', 'status': 'INCOMPLETE'}
    raw.write_json(out / 'result.json', report)
    try:
        receipt = load(scores / 'result.json')
        baseline = load(scores / 'p1_o0/score.json')
        graph_path = scores / 'p1_o0/graph.json'
        graph = load(graph_path)
        recovery, trace = load(case / 'recovery.json'), load(case / 'trace.json')
        raw.need(raw.sha(graph_path) == baseline['graph_sha256'], 'raw graph identity differs from scored graph')
        raw.need(raw.sha(case / 'recovery.json') == receipt['inputs']['recovery_sha256'] and
                 raw.sha(case / 'trace.json') == receipt['inputs']['trace_sha256'], 'raw scoring input identities changed')
        raw.need(raw.sha(raw.__file__) == receipt['inputs']['scorer_sha256'], 'frozen scorer changed after raw scoring')
        raw.need(baseline['recovery_status'] == 'PASS' and baseline['structural']['status'] == 'PASS' and
                 baseline['semantic']['status'] == 'PASS', 'baseline raw structural and finite semantic PASS required')
        raw.need(graph['origin_offset'] == 0 and graph['period_mode'] == 'DISCOVERED', 'normalization requires baseline discovered-period/origin-zero graph')
        run_start = baseline['semantic']['run_start']
        normalized, history = normalize(graph, recovery, trace, run_start)
        raw.write_json(out / 'prefix_history.json', history)
        report['prefix_history'] = {'status': history['status'], 'checked_equalities': history['checked_equalities']}
        report['inputs'] = {'raw_graph_sha256': raw.sha(graph_path), 'recovery_sha256': raw.sha(case / 'recovery.json'),
                            'trace_sha256': raw.sha(case / 'trace.json'), 'normalizer_sha256': raw.sha(__file__),
                            'raw_scorer_sha256': raw.sha(raw.__file__), 'raw_score_sha256': raw.sha(scores / 'p1_o0/score.json')}
        raw.need(history['status'] == 'PASS', 'required prefix-copy history equality failed; normalization not adopted')
        raw.write_json(out / 'normalized_graph.json', normalized)
        report['normalized_graph_sha256'] = raw.sha(out / 'normalized_graph.json')
        report['saved_before_oracle_load'] = True
        report['copies_removed'] = normalized['copies_removed']
        report['identity_check'] = normalized['literal_copy_normalization']
        if history['equalities']:
            first = history['equalities'][0]
            corrupted = copy.deepcopy(trace)
            corrupted['after'][first['copy'][1]][first['copy_write_cycle']] ^= 1
            wrong = check_prefix(graph, recovery, corrupted,
                                 [{k: row[k] for k in ('copy', 'sample', 'source')} for row in history['equalities']], run_start)
            report['wrong_prefix_history_control'] = {
                'status': 'DETECTED' if wrong['status'] == 'FAIL' else 'MISSED',
                'copy': first['copy'], 'write_cycle': first['copy_write_cycle'],
                'failed_equalities': sum(not row['equal'] for row in wrong['equalities']),
                'scope': 'Self-test corrupts one demanded actual prefix copy value; not a DUT failure.'}
        else:
            report['wrong_prefix_history_control'] = {'status': 'NOT_APPLICABLE_NO_REQUIRED_COPY_HISTORY'}

        # Normalization above has no oracle parameter and has already been saved.
        oracle = load(case / 'oracle.json')
        raw.need(raw.sha(case / 'oracle.json') == receipt['inputs']['oracle_sha256'], 'oracle identity changed after raw scoring')
        raw.need(oracle.get('source') == 'known registered reference graph' and oracle.get('family') in ('recurrences', 'quantized') and
                 oracle.get('logical_compute_nodes') == ['A', 'B'], 'this normalization evaluation is restricted to controlled A/B cases')
        expected = normalized_oracle(oracle, recovery['meta']['window'])
        raw.write_json(out / 'normalized_scoring_oracle.json', expected)
        saved = load(out / 'normalized_graph.json')
        report['structural'] = raw.score_graph(saved, recovery, expected)
        report['semantic'] = execute_saved(saved, recovery, trace, run_start, baseline['semantic']['run_end_exclusive'])
        signature = core_signature(saved, expected)
        raw.write_json(out / 'core_signature.json', signature)
        report['core_signature_sha256'] = raw.sha(out / 'core_signature.json')
        comparisons = []
        for path in args.compare:
            other = load(path)
            equal = other['family'] == signature['family'] and other['signature'] == signature['signature']
            comparisons.append({'file': str(path.resolve()), 'sha256': raw.sha(path), 'status': 'PASS' if equal else 'FAIL'})
        report['cross_case'] = comparisons
        passed = (report['structural']['status'] == 'PASS' and report['semantic']['status'] == 'PASS' and
                  report['wrong_prefix_history_control']['status'] != 'MISSED' and
                  all(row['status'] == 'PASS' for row in comparisons))
        report['status'] = 'PASS' if passed else 'FAIL'
        report['scope'] = 'Controlled-only literal-copy normalization after raw recovery. Syntactic identity plus the explicitly checked finite prefix equalities and finite raw/normalized execution; no SMT or universal whole-runtime certificate. Cross-case signatures compare A/B sample graphs and period-end observations only.'
        raw.need(raw.sha(graph_path) == report['inputs']['raw_graph_sha256'] and raw.sha(case / 'recovery.json') == report['inputs']['recovery_sha256'] and
                 raw.sha(case / 'trace.json') == report['inputs']['trace_sha256'], 'normalization input changed during evaluation')
        raw.write_json(out / 'result.json', report)
        print(json.dumps({'status': report['status'], 'copies_removed': report['copies_removed'],
                          'prefix_equalities': history['checked_equalities'], 'output': str(out)}))
        return 0 if passed else 1
    except Exception as error:
        report.update(status='ERROR', error=str(error), exception_type=type(error).__name__)
        raw.write_json(out / 'result.json', report)
        (out / 'error.txt').write_text(traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Controlled C2 evaluation: remove literal identity moves with checked history.

Usage: c2_normalize.py CASE_DIR SCORE_DIR OUTPUT_DIR
       [--variant pM_oO | --all-variants]
       [--compare OTHER_OUTPUT/core_signature.json ...]

SCORE_DIR contains the frozen scorer's result.json and pM_oO/{graph,score}.json.
The default single variant is p1_o0 and preserves the original output layout.
Only the controlled registered graphs are scored by this adapter. This is not a
general optimizer and does not change frozen InvFold or the frozen raw scorer.
"""
import argparse
import collections
import copy
import importlib
import json
from pathlib import Path
import re
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
    samples = [row['sample'] for row in rows]
    physical = [row[key] for row in rows for key in ('copy_write_cycle', 'parent_physical_cycle')]
    return {'status': 'PASS' if all(row['equal'] for row in rows) else 'FAIL', 'equalities': rows,
            'checked_equalities': len(rows), 'run_start': run_start,
            'first_required_sample': min(samples) if samples else None,
            'last_required_sample': max(samples) if samples else None,
            'earliest_required_physical_cycle': min(physical) if physical else None,
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
    max_delay, max_chain = 0, 0
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
                max_delay = max(max_delay, source[3])
                max_chain = max(max_chain, len(chain))
    prefix = check_prefix(graph, recovery, trace, [conditions[key] for key in sorted(conditions)], run_start)
    result['literal_copy_normalization'] = {
        'copies_removed': len(aliases), 'operand_occurrences_rewritten': replacement_count,
        'removed': [{'id': list(key), 'source': source, 'proof': 'let-expanded same-width single-leaf identity'}
                    for key, source in sorted(aliases.items())],
        'prefix_status': prefix['status'], 'required_prefix_equalities': len(conditions),
        'run_start': run_start, 'compute_functions_changed': 0, 'observation_nodes_removed': 0,
        'max_cumulative_copy_delay': max_delay, 'max_removed_chain_length': max_chain,
        'required_copy_sample_span': max((first_sample - row['sample'] for row in conditions.values()), default=0),
        'required_source_history_sample_span': max((first_sample - row['sample'] + row['source'][3]
                                                    for row in conditions.values()), default=0),
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
    """Scoring only: quotient consistent copies in the source sample coordinates."""
    events = {(row['T'], row['target']): row for row in oracle['events']}
    base_period, base_origin = oracle['period'], oracle['origin']
    raw.need(graph['period'] % base_period == 0, 'graph period is not a source-period multiple')
    factor = graph['period'] // base_period
    core, outputs = [], []
    for node in graph['nodes']:
        event = events[node['T'], node['target']]
        # Retiming changes graph phases, not the source's declared observation
        # time. Unfolding contains one such observation per source period.
        if node['id'][0] == 'out' and (node['T'] - base_origin) % base_period != base_period - 1:
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
    def quotient(rows, key_name, expected_keys):
        groups = collections.defaultdict(list)
        for row in rows:
            groups[row[key_name]].append(row)
        raw.need(set(groups) == set(expected_keys), 'missing or extra canonical signature identity')
        result = []
        for key in sorted(groups):
            group = groups[key]
            raw.need(len(group) == factor, 'wrong number of unfolded signature copies: ' + str(key))
            raw.need(all(row == group[0] for row in group), 'unfolded copies disagree before signature quotient: ' + str(key))
            result.append(group[0])
        return result

    ports = {node['id'][1] for node in graph['nodes'] if node['id'][0] == 'out'}
    core = quotient(core, 'logical', {'A', 'B'})
    outputs = quotient(outputs, 'port', ports)
    return {'schema': 'C2_CONTROLLED_CORE_SIGNATURE_V2', 'family': oracle['family'],
            'signature': {'nodes': core, 'sample_outputs': outputs},
            'representation': {'period': graph['period'], 'origin': graph['origin'],
                               'source_period': base_period, 'source_origin': base_origin,
                               'copies_per_identity_checked': factor,
                               'observation_physical_phase': base_period - 1},
            'scope': 'Scoring labels only, never normalization inputs. Exact A/B functions and occurrence-labelled delays in source sample coordinates. Every unfolded copy must agree before quotienting. Observations use the physical end of each source period; intermediate hardware-cycle outputs are checked separately by finite execution.'}


def raw_pass(row):
    return (row.get('recovery_status') == 'PASS' and row.get('structural', {}).get('status') == 'PASS'
            and row.get('semantic', {}).get('status') == 'PASS')


def prepare_variant(case, scores, out, variant, receipt, recovery, trace, report):
    """Transform and save one representation without opening the oracle."""
    baseline = load(scores / variant / 'score.json')
    raw.need(baseline == receipt['variants'][variant], 'variant score differs from parent raw receipt')
    raw.need(raw_pass(baseline), 'selected raw recovery, structural and finite semantic PASS required')
    graph_path = scores / variant / 'graph.json'
    graph = load(graph_path)
    raw.need(raw.sha(graph_path) == baseline['graph_sha256'], 'raw graph identity differs from scored graph')
    raw.need(graph['origin_offset'] == baseline['origin_offset'] and
             graph['period'] == receipt['discovered_period'] * baseline['period_multiple'], 'raw representation coordinates differ')
    raw.need(graph['period_mode'] == ('DISCOVERED' if baseline['period_multiple'] == 1 else 'SUPPLIED_REPRESENTATION_ONLY'),
             'unsupported raw period mode')
    run_start = baseline['semantic']['run_start']
    normalized, history = normalize(graph, recovery, trace, run_start)
    raw.write_json(out / 'prefix_history.json', history)
    report['prefix_history'] = {key: value for key, value in history.items() if key != 'equalities'}
    report['inputs'] = {'raw_graph_sha256': raw.sha(graph_path), 'recovery_sha256': raw.sha(case / 'recovery.json'),
                        'trace_sha256': raw.sha(case / 'trace.json'), 'normalizer_sha256': raw.sha(__file__),
                        'raw_scorer_sha256': raw.sha(raw.__file__), 'raw_score_sha256': raw.sha(scores / variant / 'score.json')}
    report['representation'] = {'variant': variant, 'period': graph['period'], 'origin': graph['origin'],
                                'origin_offset': graph['origin_offset'], 'period_mode': graph['period_mode'],
                                'run_start': run_start, 'run_end_exclusive': baseline['semantic']['run_end_exclusive']}
    raw.need(history['status'] == 'PASS', 'required prefix-copy history equality failed; normalization not adopted')
    raw.write_json(out / 'normalized_graph.json', normalized)
    report['normalized_graph_sha256'] = raw.sha(out / 'normalized_graph.json')
    report['saved_before_oracle_load'] = True
    report['copies_removed'] = normalized['copies_removed']
    report['identity_check'] = normalized['literal_copy_normalization']
    if history['equalities']:
        first = min(history['equalities'], key=lambda row: row['copy_write_cycle'])
        corrupted = copy.deepcopy(trace)
        corrupted['after'][first['copy'][1]][first['copy_write_cycle']] ^= 1
        wrong = check_prefix(graph, recovery, corrupted,
                             [{k: row[k] for k in ('copy', 'sample', 'source')} for row in history['equalities']], run_start)
        report['wrong_prefix_history_control'] = {
            'status': 'DETECTED' if wrong['status'] == 'FAIL' else 'MISSED',
            'copy': first['copy'], 'write_cycle': first['copy_write_cycle'],
            'run_start': run_start, 'age_physical_cycles': run_start - first['copy_write_cycle'],
            'base_period': receipt['discovered_period'],
            'complete_base_periods_before_start': (run_start - first['copy_write_cycle']) // receipt['discovered_period'],
            'failed_equalities': sum(not row['equal'] for row in wrong['equalities']),
            'scope': 'Self-test corrupts one earliest demanded actual prefix copy value; not a DUT failure.'}
    else:
        report['wrong_prefix_history_control'] = {'status': 'NOT_APPLICABLE_NO_REQUIRED_COPY_HISTORY'}
    return baseline


def score_variant(out, report, baseline, recovery, trace, expected, comparisons):
    """Oracle is used only after every selected transformed graph was saved."""
    raw.write_json(out / 'normalized_scoring_oracle.json', expected)
    raw.need(raw.sha(out / 'normalized_graph.json') == report['normalized_graph_sha256'], 'saved normalized graph changed')
    saved = load(out / 'normalized_graph.json')
    report['structural'] = raw.score_graph(saved, recovery, expected)
    report['semantic'] = execute_saved(saved, recovery, trace, baseline['semantic']['run_start'],
                                       baseline['semantic']['run_end_exclusive'])
    signature = core_signature(saved, expected)
    raw.write_json(out / 'core_signature.json', signature)
    report['core_signature_sha256'] = raw.sha(out / 'core_signature.json')
    report['cross_case'] = []
    for path in comparisons:
        other = load(path)
        equal = other['family'] == signature['family'] and other['signature'] == signature['signature']
        report['cross_case'].append({'file': str(path.resolve()), 'sha256': raw.sha(path), 'status': 'PASS' if equal else 'FAIL'})
    passed = (report['structural']['status'] == 'PASS' and report['semantic']['status'] == 'PASS'
              and report['wrong_prefix_history_control']['status'] != 'MISSED'
              and all(row['status'] == 'PASS' for row in report['cross_case']))
    report['status'] = 'PASS' if passed else 'FAIL'
    report['scope'] = 'Controlled literal-copy normalization after raw recovery. Each representation has its own checked finite prefix boundary. Exact identities, finite histories and raw/normalized execution only; no universal certificate. Canonical signatures compare A/B source samples and source-period-end observations.'
    return saved, signature


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('scores', type=Path)
    parser.add_argument('output', type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--variant', default='p1_o0')
    selection.add_argument('--all-variants', action='store_true')
    parser.add_argument('--compare', type=Path, action='append', default=[])
    args = parser.parse_args()
    case, scores, out = args.case.resolve(), args.scores.resolve(), args.output.resolve()
    raw.need(not out.exists() or not any(out.iterdir()), 'normalization output must be empty')
    out.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'C2_CONTROLLED_NORMALIZATION_V2', 'status': 'INCOMPLETE'}
    raw.write_json(out / 'result.json', report)
    try:
        receipt = load(scores / 'result.json')
        recovery, trace = load(case / 'recovery.json'), load(case / 'trace.json')
        raw.need(raw.sha(case / 'recovery.json') == receipt['inputs']['recovery_sha256'] and
                 raw.sha(case / 'trace.json') == receipt['inputs']['trace_sha256'], 'raw scoring input identities changed')
        raw.need(raw.sha(raw.__file__) == receipt['inputs']['scorer_sha256'], 'frozen scorer changed after raw scoring')
        _, identities = raw.load_frozen()
        raw.need(identities == receipt['inputs']['recovery_code'], 'raw recovery source identities changed')
        candidates = sorted(receipt['variants'], key=lambda key: tuple(map(int, re.fullmatch(r'p(\d+)_o(\d+)', key).groups())))
        if not args.all_variants:
            raw.need(args.variant in receipt['variants'], 'selected representation is absent from raw score receipt')
            candidates = [args.variant]
        skipped, rows, folders, prepared = {}, {}, {}, {}
        for variant in candidates:
            if args.all_variants and not raw_pass(receipt['variants'][variant]):
                row = receipt['variants'][variant]
                skipped[variant] = {'status': 'SKIPPED_RAW_NOT_PASS', 'recovery_status': row.get('recovery_status'),
                                    'structural_status': row.get('structural', {}).get('status'),
                                    'semantic_status': row.get('semantic', {}).get('status')}
                continue
            folder = out / variant if args.all_variants else out
            folder.mkdir(exist_ok=True)
            rows[variant] = {'schema': 'C2_CONTROLLED_NORMALIZATION_V2', 'status': 'INCOMPLETE'}
            folders[variant] = folder
            try:
                prepared[variant] = prepare_variant(case, scores, folder, variant, receipt, recovery, trace, rows[variant])
            except Exception as error:
                rows[variant].update(status='ERROR', error=str(error), exception_type=type(error).__name__)
                (folder / 'error.txt').write_text(traceback.format_exc())
            raw.write_json(folder / 'result.json', rows[variant])
        # All oracle-free transformations are complete, including failed receipts.
        oracle = load(case / 'oracle.json')
        raw.need(raw.sha(case / 'oracle.json') == receipt['inputs']['oracle_sha256'], 'oracle identity changed after raw scoring')
        standard = oracle.get('family') in ('recurrences', 'quantized')
        history = oracle.get('family') in ('history_rne8_h3', 'history_rne8_h5') and oracle.get('protocol') == 'C2_HISTORY_V1'
        raw.need(oracle.get('source') == 'known registered reference graph' and (standard or history)
                 and oracle.get('logical_compute_nodes') == ['A', 'B'], 'normalization evaluation is restricted to declared controlled A/B cases')
        expected = normalized_oracle(oracle, recovery['meta']['window'])
        graphs, signatures = {}, {}
        for variant, baseline in prepared.items():
            try:
                graphs[variant], signatures[variant] = score_variant(folders[variant], rows[variant], baseline,
                                                                      recovery, trace, expected, args.compare)
            except Exception as error:
                rows[variant].update(status='ERROR', error=str(error), exception_type=type(error).__name__)
                (folders[variant] / 'error.txt').write_text(traceback.format_exc())
            raw.write_json(folders[variant] / 'result.json', rows[variant])
        if args.all_variants:
            relations, signature_checks = {}, {}
            for variant, graph in graphs.items():
                multiple, origin = map(int, re.fullmatch(r'p(\d+)_o(\d+)', variant).groups())
                for mode, needed in [('retiming', f'p{multiple}_o0')] * bool(origin) + [('unfolding', f'p1_o{origin}')] * (multiple != 1):
                    relations[mode + '_' + variant] = (raw.relation_check(graphs[needed], graph, mode) if needed in graphs
                                                       else {'status': 'NOT_CHECKED_MISSING_BASE', 'required_variant': needed})
                    relations[mode + '_' + variant]['scope'] = (
                        'Frozen coordinate checker applied to saved normalized graphs after literal-copy removal. '
                        'Retiming/unfolding compare retained node functions and operand coordinates; each representation '
                        'has its own separately checked finite prefix histories and saved-graph execution.')
            if signatures:
                name = next(iter(signatures))
                canonical = signatures[name]
                for variant, signature in signatures.items():
                    equal = canonical['family'] == signature['family'] and canonical['signature'] == signature['signature']
                    signature_checks[variant] = {'status': 'PASS' if equal else 'FAIL', 'base_variant': name}
                raw.write_json(out / 'core_signature.json', canonical)
            report.update(variants=rows, skipped_variants=skipped, coordinate_checks=relations,
                          signature_checks=signature_checks, all_selected_graphs_saved_before_oracle_load=True)
            passed = (bool(rows) and all(row['status'] == 'PASS' for row in rows.values())
                      and all(row['status'] == 'PASS' for row in relations.values())
                      and all(row['status'] == 'PASS' for row in signature_checks.values()))
            report['status'] = 'PASS' if passed and not skipped else 'INCOMPLETE' if passed or not rows else 'FAIL'
            report['scope'] = 'Normalization of every saved PASS raw representation, with explicit skipped failures. No skipped representation is counted as PASS. Retiming/unfolding use the frozen coordinate checker on normalized graphs; canonical signature quotient is scoring-only.'
        else:
            report = rows[args.variant]
        for variant, row in rows.items():
            if 'inputs' in row:
                raw.need(raw.sha(scores / variant / 'graph.json') == row['inputs']['raw_graph_sha256'] and
                         raw.sha(scores / variant / 'score.json') == row['inputs']['raw_score_sha256'], 'raw variant changed during normalization')
        raw.need(raw.sha(case / 'recovery.json') == receipt['inputs']['recovery_sha256'] and
                 raw.sha(case / 'trace.json') == receipt['inputs']['trace_sha256'] and
                 raw.sha(case / 'oracle.json') == receipt['inputs']['oracle_sha256'], 'normalization inputs changed during evaluation')
        raw.write_json(out / 'result.json', report)
        print(json.dumps({'status': report['status'], 'variants_selected': len(rows),
                          'variants_skipped': len(skipped), 'output': str(out)}))
        return 0 if report['status'] == 'PASS' else 1
    except Exception as error:
        report.update(status='ERROR', error=str(error), exception_type=type(error).__name__)
        raw.write_json(out / 'result.json', report)
        (out / 'error.txt').write_text(traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())

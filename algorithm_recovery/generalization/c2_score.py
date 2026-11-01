#!/usr/bin/env python3
"""Score frozen InvFold raw graphs against separately supplied provenance.

The recovery phase reads recovery.json only and saves every proposed graph
before oracle.json or trace.json is opened. This is evaluation code, not a new
recovery algorithm. No fitted functions or copy elimination are adopted.

AST paths are zero-based operand-child paths, excluding the operator head,
after simultaneous SMT let bindings have been expanded. Repeated leaves count
separately. operand_occurrences is safe for adapters to reuse for paths only;
their producer identities must come from independent source provenance.
"""
import argparse
import collections
import copy
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import traceback


HERE = Path(__file__).resolve().parent
INV = HERE.parent / 'invfold'
FROZEN = {
    'invfold.py': 'a9ace86cd1c1c3cb4a85e1d59e749a3c68b92da46029bc2d1643f41901f64d5e',
    'smt2.py': '306e201804ec2d7c8c688f5521a370a49daf13b2014b338a16d4a50b42e60a65',
    'identify.py': 'bb31a1d076c0db3fea1c2e4bb873c9524874bf40fcc355326e87a69310622b6f',
}
TOKEN = re.compile(r'\s*(\(|\)|\|[^|]*\||[^\s()]+)')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def need(condition, message):
    if not condition:
        raise ValueError(message)


def parse_term(text):
    tokens = TOKEN.findall(text)
    position = 0

    def read():
        nonlocal position
        need(position < len(tokens), 'truncated SMT term')
        token = tokens[position]
        position += 1
        if token == '(':
            result = []
            while position < len(tokens) and tokens[position] != ')':
                result.append(read())
            need(position < len(tokens), 'unclosed SMT term')
            position += 1
            return result
        need(token != ')', 'unexpected closing parenthesis')
        return token

    result = read()
    need(position == len(tokens), 'trailing SMT tokens')
    return result


def expand_lets(term, environment=None):
    environment = {} if environment is None else environment
    if isinstance(term, str):
        return copy.deepcopy(environment.get(term, term))
    need(term, 'empty SMT expression')
    if term[0] == 'let':
        need(len(term) == 3, 'invalid SMT let')
        extended = dict(environment)
        # SMT let definitions see the outer scope, not earlier siblings.
        for name, value in term[1]:
            extended[name] = expand_lets(value, environment)
        return expand_lets(term[2], extended)
    return [term[0]] + [expand_lets(child, environment) for child in term[1:]]


def operand_occurrences(text, widths):
    """Return {path,symbol,width} for every free leaf, preserving duplicates."""
    result = []

    def visit(term, path):
        if isinstance(term, str):
            if term in ('true', 'false') or term.startswith(('#b', '#x')):
                return
            symbol = term.strip('|')
            need(symbol in widths, 'untyped SMT leaf: ' + symbol)
            result.append({'path': path, 'symbol': symbol, 'width': widths[symbol]})
            return
        if term[0] == '_' and str(term[1]).startswith('bv'):
            return
        for index, child in enumerate(term[1:]):
            visit(child, path + [index])

    visit(expand_lets(parse_term(text)), [])
    return result


def relative_term(text, time, symbols):
    """Exact let-expanded syntax with physical read times made relative."""
    def visit(term):
        if isinstance(term, str):
            symbol = term.strip('|')
            if symbol in symbols:
                base, cycle = symbol.rsplit('_', 1)
                return base + '@' + str(time - int(cycle))
            return term
        return [term[0]] + [visit(child) for child in term[1:]]
    return visit(expand_lets(parse_term(text)))


def widths_for(text, meta):
    result = {}
    for word, width in meta['widths'].items():
        for match in re.finditer(r'(?<![\w])' + re.escape(word) + r'_o_Q_(?:ordered_)?\d+\b', text):
            result[match.group()] = width
    for port, width in meta['ports']['in'].items():
        for match in re.finditer(r'(?<![\w])(?:top_module_)?' + re.escape(port) + r'_\d+\b', text):
            result[match.group()] = width
    return result


def validate_recovery(recovery):
    meta = recovery['meta']
    need(set(meta) == {'window', 'widths', 'ports', 'write_enable'},
         'C2 recovery metadata accepts only window/widths/ports/write_enable; no producer or extension hints')
    window = meta['window']
    need(window and window == list(range(window[0], window[-1] + 1)), 'noncontiguous recovery window')
    need(window[0] >= 0, 'negative physical window')
    need(set(meta['write_enable']) == set(meta['widths']), 'write inventory differs from word inventory')
    need(set(meta['ports']) == {'in', 'out'}, 'invalid port inventory')
    for word, width in meta['widths'].items():
        need(re.fullmatch(r'w\d+', word) and type(width) is int and width > 0, 'nonanonymous or invalid word')
        enables = meta['write_enable'][word]
        need(len(enables) > window[-1] and all(type(x) is int and x in (0, 1) for x in enables), 'invalid effective writes')
    for port, width in meta['ports']['in'].items():
        need(re.fullmatch(r'x\d+', port) and type(width) is int and width > 0, 'nonanonymous or invalid input')
    need(all(type(width) is int and width > 0 for width in meta['ports']['out'].values()), 'invalid output width')
    equations = {}
    for row in recovery['equations']:
        need(set(row) == {'T', 'target', 'smt2'}, 'equations must contain only T/target/smt2')
        key = (row['T'], row['target'])
        need(key not in equations and row['T'] in window, 'duplicate/out-of-window equation')
        operand_occurrences(row['smt2'], widths_for(row['smt2'], meta))
        equations[key] = row['smt2']
    targets = {w + ' - i_D' for w in meta['widths']} | {'top_module - ' + p for p in meta['ports']['out']}
    need({target for _, target in equations} == targets, 'missing/extra target inventory')
    for t in window:
        for target in targets:
            word = target.split(' - ')[0]
            if target.endswith(' - i_D') and not meta['write_enable'][word][t]:
                continue
            need((t, target) in equations, 'missing effective write or output equation')
    return equations


def check_repetition(recovery, period):
    """Finite exact syntax/write check independent of Folded.signature/period."""
    meta = recovery['meta']
    window = meta['window']
    need(type(period) is int and period > 0 and len(window) >= 2 * period,
         'insufficient window for independent requested-period repetition check')
    equations = {(row['T'], row['target']): row['smt2'] for row in recovery['equations']}
    targets = sorted({target for _, target in equations})
    signatures = {}
    for t in window:
        for target in targets:
            word = target.split(' - ')[0]
            if target.endswith(' - i_D') and not meta['write_enable'][word][t]:
                signatures[t, target] = ['hold']
            else:
                text = equations[t, target]
                signatures[t, target] = ['write', relative_term(text, t, widths_for(text, meta))]
    checked = 0
    for t in window[:-period]:
        for target in targets:
            need(signatures[t, target] == signatures[t + period, target],
                 'nonrepeating equation/write signature at ' + repr((t, target, period)))
            checked += 1
    return {'period': period, 'compared_signature_pairs': checked,
            'scope': 'Exact finite expression/write repetition; not a universal schedule certificate.'}


def load_frozen():
    actual = {name: sha(INV / name) for name in FROZEN}
    need(actual == FROZEN, 'frozen recovery source identity changed')
    sys.path.insert(0, str(INV))
    module = importlib.import_module('invfold')
    need(Path(module.__file__).resolve() == (INV / 'invfold.py').resolve(), 'wrong InvFold module imported')
    return module, actual


def save_graph(folder, model, recovery_hash, mode, repetition):
    nodes = []
    for kind, entries in (('sig', model.nodes), ('out', model.outputs)):
        for (name, phase), definition in sorted(entries.items()):
            widths = model.widths_of(definition)
            text = model.eq[definition['T'], definition['tgt']]
            occurrences = operand_occurrences(text, widths)
            need({x['symbol'] for x in occurrences} == set(definition['res']), 'AST/source inventory differs')
            for operand in occurrences:
                operand['source'] = list(definition['res'][operand['symbol']])
            width = model.W[name] if kind == 'sig' else model.meta['ports']['out'][name]
            _, _, compiled_width = model.compile(definition, widths)
            need(width == compiled_width, 'raw expression result width differs from declared target')
            nodes.append({'id': [kind, name, phase], 'T': definition['T'], 'target': definition['tgt'],
                          'width': width, 'raw_smt2': text, 'operands': occurrences,
                          'relative_expression': relative_term(text, definition['T'], widths)})
    graph = {'schema': 'C2_RAW_GRAPH_V1', 'recovery_sha256': recovery_hash,
             'period': model.P, 'origin': model.win[0] + model.origin, 'origin_offset': model.origin,
             'period_mode': mode, 'finite_repetition': repetition, 'nodes': nodes,
             'recovery_report': copy.deepcopy(model.report), 'copies_removed': 0,
             'scope': 'Frozen Folded.period/build raw graph; no fitted replacements, section hints or copy collapse.'}
    write_json(folder / 'graph.json', graph)
    return graph


def event_id(target, time, graph):
    kind = 'sig' if target.endswith(' - i_D') else 'out'
    name = target.split(' - ')[0 if kind == 'sig' else 1]
    return kind, name, (time - graph['origin']) % graph['period']


def score_graph(graph, recovery, oracle):
    """Compare a SAVED graph to independent event/operand provenance."""
    meta = recovery['meta']
    window = set(meta['window'])
    events = {}
    for event in oracle['events']:
        key = (event['T'], event['target'])
        need(key not in events, 'duplicate oracle event: ' + repr(key))
        need(event['width'] > 0 and isinstance(event['logical'], str), 'invalid oracle event identity')
        paths = [tuple(row['path']) for row in event['operands']]
        need(len(paths) == len(set(paths)), 'duplicate oracle operand path')
        events[key] = event
    expected = {key: row for key, row in events.items() if key[0] in window}
    nodes = {tuple(node['id']): node for node in graph['nodes']}
    need(len(nodes) == len(graph['nodes']), 'duplicate recovered graph node')
    expected_nodes = {event_id(target, t, graph) for t, target in expected}
    actual = {}
    for t in sorted(window):
        phase = (t - graph['origin']) % graph['period']
        for key, node in nodes.items():
            if key[2] == phase:
                actual[t, node['target']] = node
    errors = []
    counts = collections.Counter()
    kinds = collections.Counter()
    for key in sorted(set(expected) - set(actual)):
        errors.append({'kind': 'missing_event', 'event': list(key)})
    for key in sorted(set(actual) - set(expected)):
        errors.append({'kind': 'extra_event', 'event': list(key)})
    for key in sorted(set(expected) & set(actual)):
        event, node = expected[key], actual[key]
        time, target = key
        kinds[event['kind']] += 1
        counts['events_compared'] += 1
        if event['width'] != node['width']:
            errors.append({'kind': 'target_width', 'event': list(key), 'expected': event['width'], 'actual': node['width']})
        wanted = {tuple(row['path']): row for row in event['operands']}
        got = {tuple(row['path']): row for row in node['operands']}
        if len(got) != len(node['operands']):
            errors.append({'kind': 'duplicate_recovered_operand_path', 'event': list(key)})
        for path in sorted(set(wanted) - set(got)):
            errors.append({'kind': 'missing_operand', 'event': list(key), 'path': list(path)})
        for path in sorted(set(got) - set(wanted)):
            errors.append({'kind': 'extra_operand', 'event': list(key), 'path': list(path)})
        for path in sorted(set(wanted) & set(got)):
            want, operand = wanted[path], got[path]
            counts['operand_occurrences_compared'] += 1
            detail = {'event': list(key), 'path': list(path)}
            base, representative_cycle = operand['symbol'].rsplit('_', 1)
            symbol = base + '_' + str(int(representative_cycle) + time - node['T'])
            if symbol != want['symbol'] or operand['width'] != want['width']:
                errors.append(dict(detail, kind='operand_symbol_or_width', expected=[want['symbol'], want['width']], actual=[symbol, operand['width']]))
            source = operand['source']
            if len(source) != 4 or source[0] not in ('sig', 'in'):
                errors.append(dict(detail, kind='unsupported_source', actual=source))
                continue
            kind, name, phase, delay = source
            sample = (time - graph['origin']) // graph['period']
            physical = graph['origin'] + (sample - delay) * graph['period'] + phase
            if kind == 'sig':
                producer_event = events.get((physical, name + ' - i_D'))
                if producer_event is None:
                    errors.append(dict(detail, kind='missing_producer_event', physical=physical, source=source))
                    continue
                producer = ['op', producer_event['logical'], producer_event['sample']]
                if physical >= int(symbol.rsplit('_', 1)[1]):
                    errors.append(dict(detail, kind='not_strictly_earlier_write', physical=physical, symbol=symbol))
            else:
                producer = ['input', name, physical]
            expected_producer = want['producer']
            if producer[:2] != expected_producer[:2]:
                errors.append(dict(detail, kind='producer_identity', expected=expected_producer, actual=producer))
            else:
                counts['producer_identity_matches'] += 1
            if producer != expected_producer:
                errors.append(dict(detail, kind='producer_sample_or_time', expected=expected_producer, actual=producer))
            else:
                counts['producer_and_sample_matches'] += 1
            def logical_delay(value):
                if value[0] == 'op':
                    return event['sample'] - value[2]
                if value[0] == 'input':
                    return event['sample'] - (value[2] - oracle['origin']) // oracle['period']
                return None
            wanted_delay, actual_delay = logical_delay(expected_producer), logical_delay(producer)
            if wanted_delay != actual_delay:
                errors.append(dict(detail, kind='logical_sample_delay', expected=wanted_delay, actual=actual_delay))
            else:
                counts['logical_sample_delay_matches'] += 1
    missing_nodes = sorted(expected_nodes - set(nodes))
    extra_nodes = sorted(set(nodes) - expected_nodes)
    node_kinds = collections.defaultdict(set)
    node_labels = collections.defaultdict(set)
    logical_kinds = collections.defaultdict(set)
    for (t, target), row in expected.items():
        node_kinds[event_id(target, t, graph)].add(row['kind'])
        node_labels[event_id(target, t, graph)].add(row['logical'])
        logical_kinds[row['kind']].add(row['logical'])
    for key, labels in node_labels.items():
        if len(labels) > 1:
            errors.append({'kind': 'logical_identity_changes_across_samples', 'id': list(key), 'labels': sorted(labels)})
    return {'status': 'PASS' if not errors and not missing_nodes and not extra_nodes else 'FAIL',
            'expected_nodes': len(expected_nodes), 'recovered_nodes': len(nodes),
            'expected_nodes_by_kind': dict(collections.Counter(kind for kinds_ in node_kinds.values() for kind in kinds_)),
            'expected_logical_nodes_by_kind': {kind: len(labels) for kind, labels in logical_kinds.items()},
            'scored_node_labels': [{'id': list(key), 'logical_labels': sorted(labels)} for key, labels in sorted(node_labels.items())],
            'missing_nodes': [list(x) for x in missing_nodes], 'extra_nodes': [list(x) for x in extra_nodes],
            'expected_events': len(expected), 'recovered_events': len(actual), 'events_by_kind': dict(kinds),
            'counts': dict(counts), 'errors': errors, 'error_counts': dict(collections.Counter(e['kind'] for e in errors)),
            'scope': 'Raw event nodes and each let-expanded operand occurrence, including duplicates. Independent oracle event identities supply labels only during scoring. Logical sample coordinates are the oracle convention, not an inferred application transaction rate.'}


def finite_semantics(invfold, model, recovery, trace):
    meta = recovery['meta']
    need(trace.get('schema') == 'C2_TRACE_V1', 'unsupported trace schema')
    lengths = {len(values) for values in trace['inputs'].values()}
    lengths.update(len(values) for values in trace['after'].values())
    lengths.update(len(values) for values in trace['outputs'].values())
    need(len(lengths) == 1, 'trace columns have unequal lengths')
    length = lengths.pop()
    need(set(trace['inputs']) == set(meta['ports']['in']) and set(trace['after']) == set(meta['widths']), 'trace input/state inventory mismatch')
    need(set(trace['outputs']) == set(meta['ports']['out']), 'trace output inventory mismatch')
    origin, period = model.win[0] + model.origin, model.P
    first = max(0, (trace['compare_from'] - origin + period - 1) // period)
    last = (length - origin) // period
    need(first < last, 'no full initialized graph sample in trace')
    run_start = origin + first * period
    history = {}

    def external(port, t):
        need(0 <= t < length, 'input physical time outside saved trace')
        value = trace['inputs'][port][t]
        need(type(value) is int, 'unknown demanded external input')
        return value & ((1 << meta['ports']['in'][port]) - 1)

    def initial(word, t):
        need(0 <= t < run_start, 'history callback tried to inject current/future reference state')
        need(t < len(meta['write_enable'][word]) and meta['write_enable'][word][t] == 1, 'history is not an actual finite prefix write')
        value = trace['after'][word][t]
        need(type(value) is int, 'unknown demanded prefix history')
        history[word, t] = value
        return value & ((1 << meta['widths'][word]) - 1)

    values, outputs = invfold.run_graph(model, external, initial, first, last, outputs=True)
    counts = collections.Counter()
    errors = []
    for (word, phase, sample), actual in values.items():
        t = origin + sample * period + phase
        expected = trace['after'][word][t]
        if expected is None:
            counts['unknown_word_observations'] += 1
            continue
        counts['word_observations'] += 1
        counts['word_bits'] += meta['widths'][word]
        if actual != expected & ((1 << meta['widths'][word]) - 1):
            errors.append({'kind': 'word', 'word': word, 'T': t, 'expected': expected, 'actual': actual})
    for (port, t), actual in outputs.items():
        expected = trace['outputs'][port][t]
        if expected is None:
            counts['unknown_output_observations'] += 1
            continue
        counts['output_observations'] += 1
        counts['output_bits'] += meta['ports']['out'][port]
        if actual != expected & ((1 << meta['ports']['out'][port]) - 1):
            errors.append({'kind': 'output', 'port': port, 'T': t, 'expected': expected, 'actual': actual})
    enough = counts['word_observations'] > 0 and counts['output_observations'] > 0
    return {'status': 'FAIL' if errors else 'PASS' if enough else 'INSUFFICIENT_OBSERVATIONS',
            'run_start': run_start, 'run_end_exclusive': origin + last * period, 'graph_samples': last - first,
            'counts': dict(counts), 'errors': errors,
            'prefix_histories': [{'word': w, 'write_cycle': t, 'value': value} for (w, t), value in sorted(history.items())],
            'reference_scope': trace.get('reference_scope', 'unspecified'),
            'scope': 'Finite exact raw-graph execution; only external inputs and actual pre-run prefix writes are supplied. All subsequent logical values are generated by the graph. Unknown observations are counted, not proved; this is not SMT or a universal certificate.'}


def relation_check(base, variant, mode):
    """Check the exact coordinate formulas on saved raw graph occurrences."""
    source = {tuple(n['id']): n for n in base['nodes']}
    target = {tuple(n['id']): n for n in variant['nodes']}
    period = base['period']
    shift = variant['origin'] - base['origin']
    factor = variant['period'] // period
    need((mode == 'retiming' and factor == 1 and 0 <= shift < period) or
         (mode == 'unfolding' and shift == 0 and variant['period'] == factor * period and factor >= 1), 'invalid coordinate comparison')
    errors = []
    checks = 0
    expected_ids = set()
    for key, node in source.items():
        for consumer_copy in range(1 if mode == 'retiming' else factor):
            phase = (key[2] - shift) % period if mode == 'retiming' else consumer_copy * period + key[2]
            new_key = key[:2] + (phase,)
            expected_ids.add(new_key)
            actual = target.get(new_key)
            if actual is None:
                errors.append({'kind': 'missing_node', 'id': list(new_key)})
                continue
            if node['relative_expression'] != actual['relative_expression'] or node['width'] != actual['width']:
                errors.append({'kind': 'changed_function', 'id': list(new_key)})
            expected = {}
            for operand in node['operands']:
                kind, name, old_phase, delay = operand['source']
                if mode == 'retiming':
                    new_source = [kind, name, (old_phase - shift) % period,
                                  delay + int(old_phase < shift) - int(key[2] < shift)]
                else:
                    producer_copy = (consumer_copy - delay) % factor
                    new_source = [kind, name, producer_copy * period + old_phase, (producer_copy + delay) // factor]
                expected[tuple(operand['path'])] = new_source
            got = {tuple(operand['path']): operand['source'] for operand in actual['operands']}
            checks += len(expected)
            if expected != got:
                errors.append({'kind': 'operand_coordinate', 'id': list(new_key),
                               'expected': [{'path': list(p), 'source': s} for p, s in sorted(expected.items())],
                               'actual': actual['operands']})
    for extra in sorted(set(target) - expected_ids):
        errors.append({'kind': 'extra_node', 'id': list(extra)})
    return {'status': 'PASS' if not errors else 'FAIL', 'relation': mode, 'operand_occurrences': checks,
            'errors': errors, 'scope': 'Raw graph coordinate identity, before copy elimination; initialization is separately exercised using real physical prefix histories.'}


def fault_probes(graph, recovery, oracle):
    baseline = score_graph(graph, recovery, oracle)
    if baseline['status'] != 'PASS':
        return {'status': 'NOT_RUN_BASELINE_FAILED', 'scope': 'Scorer self-tests, not DUT results.'}
    probes = {}
    positions = [(i, j) for i, node in enumerate(graph['nodes']) for j, operand in enumerate(node['operands']) if operand['source'][0] == 'sig']
    events = {(row['T'], row['target']): row for row in oracle['events']}
    replacements = []
    simultaneous = []
    for i, j in positions:
        node = graph['nodes'][i]
        operand = node['operands'][j]
        source = operand['source']
        consumer_sample = (node['T'] - graph['origin']) // graph['period']
        read_time = int(operand['symbol'].rsplit('_', 1)[1])
        old_time = graph['origin'] + (consumer_sample - source[3]) * graph['period'] + source[2]
        old = events.get((old_time, source[1] + ' - i_D'))
        same = events.get((read_time, source[1] + ' - i_D'))
        if same is not None:
            same_source = ['sig', source[1], (read_time - graph['origin']) % graph['period'],
                           consumer_sample - (read_time - graph['origin']) // graph['period']]
            if same_source[3] >= 0:
                simultaneous.append((i, j, same_source))
        if old is None:
            continue
        for candidate in graph['nodes']:
            if candidate['id'][0] != 'sig':
                continue
            for delay in range(max(4, source[3] + 2)):
                _, word, phase = candidate['id']
                physical = graph['origin'] + (consumer_sample - delay) * graph['period'] + phase
                event = events.get((physical, word + ' - i_D'))
                if physical < read_time and event is not None and event['logical'] != old['logical']:
                    replacements.append((i, j, ['sig', word, phase, delay]))
                    break
            if replacements:
                break
        if replacements and simultaneous:
            break
    for name in ('wrong_producer', 'wrong_delay', 'same_edge_new_value'):
        if not positions:
            probes[name] = {'status': 'NOT_APPLICABLE_NO_REGISTER_READ'}
            continue
        mutant = copy.deepcopy(graph)
        i, j = positions[0]
        if name == 'wrong_producer':
            if not replacements:
                probes[name] = {'status': 'NOT_APPLICABLE_NO_OTHER_EXISTING_PRODUCER'}
                continue
            i, j, replacement = replacements[0]
            mutant['nodes'][i]['operands'][j]['source'] = replacement
        elif name == 'wrong_delay':
            mutant['nodes'][i]['operands'][j]['source'][3] += 1
        else:
            if not simultaneous:
                probes[name] = {'status': 'NOT_APPLICABLE_NO_SIMULTANEOUS_READ_WRITE'}
                continue
            i, j, replacement = simultaneous[0]
            mutant['nodes'][i]['operands'][j]['source'] = replacement
        result = score_graph(mutant, recovery, oracle)
        probes[name] = {'status': 'DETECTED' if result['status'] == 'FAIL' else 'MISSED',
                        'node': graph['nodes'][i]['id'], 'path': graph['nodes'][i]['operands'][j]['path'],
                        'original_source': graph['nodes'][i]['operands'][j]['source'],
                        'mutated_source': mutant['nodes'][i]['operands'][j]['source'],
                        'error_counts': result['error_counts']}
    duplicate = None
    for i, node in enumerate(graph['nodes']):
        symbols = collections.Counter(x['symbol'] for x in node['operands'])
        for j, operand in enumerate(node['operands']):
            if symbols[operand['symbol']] > 1:
                duplicate = i, j
                break
        if duplicate is not None:
            break
    if duplicate is None:
        probes['dropped_repeated_operand'] = {'status': 'NOT_APPLICABLE_NO_DUPLICATE'}
    else:
        mutant = copy.deepcopy(graph)
        i, j = duplicate
        removed = mutant['nodes'][i]['operands'].pop(j)
        result = score_graph(mutant, recovery, oracle)
        probes['dropped_repeated_operand'] = {'status': 'DETECTED' if result['status'] == 'FAIL' and result['error_counts'].get('missing_operand') else 'MISSED',
                                              'node': graph['nodes'][i]['id'], 'path': removed['path'], 'error_counts': result['error_counts']}
    return {'status': 'FAIL' if any(p['status'] == 'MISSED' for p in probes.values()) else 'PASS',
            'probes': probes, 'scope': 'Mutations of saved graph scoring fields exercise scorer rejection, separate from DUT recovery and semantic evidence.'}


def parse_numbers(text):
    values = [int(x) for x in text.split(',')]
    need(values and len(values) == len(set(values)) and all(x >= 0 for x in values), 'invalid/duplicate numeric option')
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--origins', default='all', help='all base-period origins, or comma-separated offsets')
    parser.add_argument('--multiples', default='1,2,3', help='comma-separated positive base-period multiples')
    args = parser.parse_args()
    out = args.output.resolve()
    need(not out.exists() or not any(out.iterdir()), 'output directory must be empty')
    out.mkdir(parents=True, exist_ok=True)
    case = args.case.resolve()
    report = {'schema': 'C2_SCORE_V1', 'status': 'INCOMPLETE', 'variants': {}, 'coordinate_checks': {},
              'copy_normalization': {'status': 'NOT_PERFORMED', 'scope': 'Raw identity moves retained. No unproved copy/history collapse.'}}
    write_json(out / 'result.json', report)
    try:
        invfold, identities = load_frozen()
        recovery = json.loads((case / 'recovery.json').read_text())
        validate_recovery(recovery)
        recovery_hash = sha(case / 'recovery.json')
        report['inputs'] = {'recovery_sha256': recovery_hash, 'recovery_code': identities, 'scorer_sha256': sha(__file__)}
        probe = invfold.Folded(copy.deepcopy(recovery['equations']), copy.deepcopy(recovery['meta']))
        period = probe.period()
        if period is None:
            report.update(status='NO_PERIOD', recovery_report=probe.report)
            write_json(out / 'result.json', report)
            return 1
        report['discovered_period'] = period
        origins = list(range(period)) if args.origins == 'all' else parse_numbers(args.origins)
        multiples = parse_numbers(args.multiples)
        need(all(x < period for x in origins) and all(x > 0 for x in multiples), 'origin/multiple outside allowed range')
        models, graphs = {}, {}
        # Strict information boundary: neither oracle nor trace has been opened.
        for multiple in multiples:
            for origin in origins:
                key = f'p{multiple}_o{origin}'
                folder = out / key
                folder.mkdir()
                row = {'origin_offset': origin, 'period_multiple': multiple}
                report['variants'][key] = row
                try:
                    repetition = check_repetition(recovery, period * multiple)
                    model = invfold.Folded(copy.deepcopy(recovery['equations']), copy.deepcopy(recovery['meta']), origin=origin)
                    if multiple == 1:
                        need(model.period() == period, 'discovered period changed across origins')
                    else:
                        # This legacy parameter is named "certified". Its use
                        # here is ONLY an explicitly supplied representation.
                        model.period(certified=period * multiple)
                        model.report['notes'] = [note for note in model.report['notes'] if 'from certificate' not in note]
                        model.report['notes'].append('period supplied for finite checked unfolding; no certificate asserted')
                    model.build()
                    graph = save_graph(folder, model, recovery_hash,
                                       'DISCOVERED' if multiple == 1 else 'SUPPLIED_REPRESENTATION_ONLY', repetition)
                    graphs[multiple, origin] = graph
                    row.update(recovery_status='PASS' if not model.report['errors'] else 'REPORTED_ERRORS',
                               graph_sha256=sha(folder / 'graph.json'), recovery_errors=model.report['errors'])
                    if not model.report['errors']:
                        models[multiple, origin] = model
                except Exception as error:
                    row.update(recovery_status='ERROR', error=str(error), exception_type=type(error).__name__)
                write_json(folder / 'recovery.json', row)
        report['raw_graphs_saved_before_oracle_load'] = True
        write_json(out / 'result.json', report)

        oracle = json.loads((case / 'oracle.json').read_text())
        trace = json.loads((case / 'trace.json').read_text()) if (case / 'trace.json').exists() else None
        report['inputs']['oracle_sha256'] = sha(case / 'oracle.json')
        report['inputs']['trace_sha256'] = sha(case / 'trace.json') if trace is not None else None
        report['reference_scope'] = oracle.get('reference_scope', 'source provenance; guarantee unspecified')
        for (multiple, origin), graph in graphs.items():
            key = f'p{multiple}_o{origin}'
            folder = out / key
            row = report['variants'][key]
            need(sha(folder / 'graph.json') == row['graph_sha256'], 'saved graph changed before scoring')
            graph = json.loads((folder / 'graph.json').read_text())
            graphs[multiple, origin] = graph
            try:
                row['structural'] = score_graph(graph, recovery, oracle)
            except Exception as error:
                row['structural'] = {'status': 'SCHEMA_ERROR', 'error': str(error), 'exception_type': type(error).__name__}
            if trace is None:
                row['semantic'] = {'status': 'NOT_AVAILABLE'}
            elif (multiple, origin) not in models:
                row['semantic'] = {'status': 'NOT_RUN_RECOVERY_ERRORS'}
            else:
                try:
                    row['semantic'] = finite_semantics(invfold, models[multiple, origin], recovery, trace)
                except Exception as error:
                    row['semantic'] = {'status': 'ERROR', 'error': str(error), 'exception_type': type(error).__name__}
            write_json(folder / 'score.json', row)
        for (multiple, origin), graph in graphs.items():
            if origin and (multiple, 0) in graphs:
                key = f'retiming_p{multiple}_o{origin}'
                report['coordinate_checks'][key] = relation_check(graphs[multiple, 0], graph, 'retiming')
            if multiple != 1 and (1, origin) in graphs:
                key = f'unfolding_p{multiple}_o{origin}'
                report['coordinate_checks'][key] = relation_check(graphs[1, origin], graph, 'unfolding')
        if (1, 0) in graphs:
            report['negative_scorer_controls'] = fault_probes(graphs[1, 0], recovery, oracle)
        else:
            report['negative_scorer_controls'] = {'status': 'NOT_RUN_NO_BASELINE_GRAPH'}
        variants_ok = all(row.get('recovery_status') == 'PASS' and row.get('structural', {}).get('status') == 'PASS' and
                          row.get('semantic', {}).get('status') == 'PASS' for row in report['variants'].values())
        relations_ok = all(row['status'] == 'PASS' for row in report['coordinate_checks'].values())
        controls_ok = report['negative_scorer_controls']['status'] != 'FAIL'
        report['status'] = 'PASS' if variants_ok and relations_ok and controls_ok else 'INCOMPLETE_OR_FAILED'
        report['scope'] = 'Separate structural and finite semantic evaluation of frozen raw InvFold. No new netlist/equation certificate, arithmetic-fit proof or universal copy normalization is claimed.'
        need(sha(case / 'recovery.json') == recovery_hash and {name: sha(INV / name) for name in FROZEN} == identities,
             'recovery input or frozen code changed during evaluation')
        need(sha(case / 'oracle.json') == report['inputs']['oracle_sha256'] and
             (trace is None or sha(case / 'trace.json') == report['inputs']['trace_sha256']), 'scoring inputs changed during evaluation')
        write_json(out / 'result.json', report)
        print(json.dumps({'status': report['status'], 'period': period, 'variants': len(report['variants']), 'output': str(out)}))
        return 0 if report['status'] == 'PASS' else 1
    except Exception as error:
        report.update(status='ERROR', error=str(error), exception_type=type(error).__name__)
        write_json(out / 'result.json', report)
        (out / 'error.txt').write_text(traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())

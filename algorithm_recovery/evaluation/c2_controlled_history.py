#!/usr/bin/env python3
"""Prepare controlled multi-sample copy-history and exact arithmetic cases.

This is a new development protocol, not a change to c2_controlled.py's v1 cases.
Run: python3 c2_controlled_history.py FRESH_OUTPUT_DIRECTORY
Preparation never invokes recovery, a last-write resolver or an SMT evaluator.
See C2_HISTORY_PROTOCOL.txt for the predeclared equations and coverage contract.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path


PERIOD = 4
PREFIX_SAMPLES = 24
WINDOW_SAMPLES = 96
POST_SAMPLES = 32
TARGETS = (0, 1, 2, 3, 126, 127, 128, 129, 130, 253, 254, 255)
REQUIRED_BINS = (
    'rne_positive_tie_kept', 'rne_positive_tie_incremented',
    'rne_negative_tie_kept', 'rne_negative_tie_incremented',
    'negative_odd_arithmetic_shift', 'zero', 'signed_minimum',
    'signed_maximum', 'exact_even_division', 'signed_subtraction_overflow_positive',
    'signed_subtraction_overflow_negative', 'signed_multiplication_overflow_positive',
    'signed_multiplication_overflow_negative',
)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signed8(word):
    word %= 256
    return word if word < 128 else word - 256


def rne2(word):
    """Independent integer definition: floor quotient/remainder, ties to even."""
    quotient, remainder = divmod(signed8(word), 2)
    if remainder and quotient % 2:
        quotient += 1
    return quotient % 256


def operation_a(previous, external):
    return rne2((previous - external) % 256)


def operation_b(current_a, previous_b):
    product = (3 * signed8(current_a)) % 256
    rounded = rne2((current_a - previous_b) % 256)
    return (product + rounded) % 256


def source_reference(depth):
    """Sample recurrence knows no physical storage, schedule resolver or SMT."""
    samples = PREFIX_SAMPLES + WINDOW_SAMPLES + POST_SAMPLES
    values_a, values_b, external, desired = [], [], [], []
    for n in range(samples):
        previous_a = values_a[n - depth] if n >= depth else 0
        previous_b = values_b[n - depth] if n >= depth else 0
        k = n - PREFIX_SAMPLES
        if 0 <= k < WINDOW_SAMPLES:
            target = TARGETS[k % len(TARGETS)]
            # Three events separated by the feedback delay force both signs
            # of subtraction overflow, independent of the warmup values.
            forced = {4: 127, 4 + depth: 128, 4 + 2 * depth: 127}
            target = forced.get(k, target)
            value = (previous_a - target) % 256
        else:
            target = None
            value = (37 * n + 19) % 256
        a = operation_a(previous_a, value)
        b = operation_b(a, previous_b)
        values_a.append(a)
        values_b.append(b)
        external.append(value)
        desired.append(target)
    return {'A': values_a, 'B': values_b, 'input': external, 'target': desired}


def rne_expression(value):
    """Bit wiring plus low-bit tie correction, unlike integer divmod above."""
    half = '(concat ((_ extract 7 7) %s) ((_ extract 7 1) %s))' % (value, value)
    increment = '(ite (= ((_ extract 1 0) %s) (_ bv3 2)) (_ bv1 8) (_ bv0 8))' % value
    return '(bvadd %s %s)' % (half, increment)


def expression(role, first, second):
    difference = '(bvsub %s %s)' % (first, second)
    rounded = rne_expression(difference)
    if role == 'A':
        return rounded
    return '(bvadd (bvmul (_ bv3 8) %s) %s)' % (first, rounded)


def make_case(out, depth, variant):
    # Syntactic occurrence enumeration is the ONLY imported scorer utility.
    # Expected producers below are propagated forward through the schedule.
    from c2_score import operand_occurrences

    out.mkdir(parents=True, exist_ok=False)
    shared = variant != 'separate_binding'
    cut = variant == 'copy_cuts'
    family = 'history_rne8_h%d' % depth
    result_a, result_b = 'w0', 'w0' if shared else 'w1'
    next_word = 1 if shared else 2
    a_chain = ['w%d' % (next_word + i) for i in range(depth)]
    b_chain = ['w%d' % (next_word + depth + i) for i in range(depth)]
    all_words = sorted(set([result_a, result_b] + a_chain + b_chain))
    active_words = sorted({result_a, result_b}) if cut else all_words
    source = source_reference(depth)
    cycles = len(source['A']) * PERIOD
    start, end = PREFIX_SAMPLES * PERIOD, (PREFIX_SAMPLES + WINDOW_SAMPLES) * PERIOD
    coverage_start = start + 4 * PERIOD
    window = list(range(start, end))
    inputs = [(73 * t + 19) % 256 for t in range(cycles)]
    for n, value in enumerate(source['input']):
        inputs[n * PERIOD] = value
    words = {w: 0 for w in all_words}
    tags = {w: ['initial', w, 0] for w in all_words}
    roots = {w: list(tag) for w, tag in tags.items()}
    before = {w: [] for w in all_words}
    after = {w: [] for w in all_words}
    tag_before = {w: [] for w in all_words}
    enables = {w: [] for w in active_words}
    outputs, equations, events, integer_checks, cut_checks = [], [], [], 0, 0
    coverage = {name: {'count': 0, 'first_witness': None} for name in REQUIRED_BINS}
    targets_seen = collections.Counter()

    def read(word, time):
        return '%s_o_Q_%d' % (word, time)

    def oracle_row(time, target, logical, sample, kind, text, producers):
        operands = []
        for occurrence in operand_occurrences(text, {symbol: 8 for symbol in producers}):
            operands.append(dict(occurrence, producer=list(producers[occurrence['symbol']])))
        return dict(T=time, target=target, logical=logical, sample=sample,
                    kind=kind, width=8, operands=operands)

    def hit(name, witness):
        row = coverage[name]
        row['count'] += 1
        if row['first_witness'] is None:
            row['first_witness'] = dict(witness)

    def arithmetic_coverage(time, role, first, second, value):
        if not coverage_start <= time < end:
            return
        difference = (first - second) % 256
        signed_difference = signed8(difference)
        witness = {'T': time, 'sample': time // PERIOD, 'operation': role,
                   'first': first, 'second': second, 'wrapped_difference': difference,
                   'signed_difference': signed_difference, 'result': value}
        if role == 'A':
            targets_seen[difference] += 1
            if difference == 0:
                hit('zero', witness)
            if difference == 128:
                hit('signed_minimum', witness)
            if difference == 127:
                hit('signed_maximum', witness)
            quotient, remainder = divmod(signed_difference, 2)
            if remainder:
                sign = 'positive' if signed_difference > 0 else 'negative'
                decision = 'incremented' if quotient % 2 else 'kept'
                hit('rne_%s_tie_%s' % (sign, decision), witness)
                if signed_difference < 0:
                    hit('negative_odd_arithmetic_shift', witness)
            else:
                hit('exact_even_division', witness)
            unwrapped = signed8(first) - signed8(second)
            witness['unwrapped_signed_subtraction'] = unwrapped
            if unwrapped > 127:
                hit('signed_subtraction_overflow_positive', witness)
            if unwrapped < -128:
                hit('signed_subtraction_overflow_negative', witness)
        else:
            product = 3 * signed8(first)
            witness['unwrapped_signed_product'] = product
            if product > 127:
                hit('signed_multiplication_overflow_positive', witness)
            if product < -128:
                hit('signed_multiplication_overflow_negative', witness)

    for time in range(cycles):
        phase, sample = time % PERIOD, time // PERIOD
        old = dict(words)
        old_tags = {w: list(tag) for w, tag in tags.items()}
        old_roots = {w: list(tag) for w, tag in roots.items()}
        for word in all_words:
            before[word].append(old[word])
            tag_before[word].append(old_tags[word])
        output_text = read(result_b, time)
        outputs.append(old[result_b])
        events.append(oracle_row(time, 'top_module - y0', 'observation', sample,
                                 'observation', output_text, {output_text: old_tags[result_b]}))
        if start <= time < end:
            equations.append(dict(T=time, target='top_module - y0', smt2=output_text))
        if phase == 0:
            actions = [(result_a, 'A', 'compute', a_chain[-1], 'x0')]
        elif phase == 2:
            actions = [(result_b, 'B', 'compute', result_a, b_chain[-1])]
        else:
            chain, result, prefix = (a_chain, result_a, 'A') if phase == 1 else (b_chain, result_b, 'B')
            actions = [(word, prefix + '_move_%d' % i, 'copy', result if i == 0 else chain[i - 1], None)
                       for i, word in enumerate(chain)]
        pending, terms = {}, {}
        for target, role, kind, first, second in actions:
            if kind == 'copy':
                text = read(first, time)
                producers = {text: old_tags[first]}
                value, root = old[first], old_roots[first]
            else:
                first_symbol = read(first, time)
                second_symbol = 'top_module_x0_%d' % time if second == 'x0' else read(second, time)
                first_value = old[first]
                second_value = inputs[time] if second == 'x0' else old[second]
                producers = {first_symbol: old_tags[first], second_symbol:
                             ['input', 'x0', time] if second == 'x0' else old_tags[second]}
                if cut and sample >= depth:
                    # A historical pre-edge read immediately AFTER a genuine
                    # retained compute write, before that result is overwritten.
                    historical = (sample - depth) * PERIOD + (1 if role == 'A' else 3)
                    historical_word = result_a if role == 'A' else result_b
                    previous_word = first if role == 'A' else second
                    previous_value = first_value if role == 'A' else second_value
                    assert before[historical_word][historical] == previous_value
                    assert tag_before[historical_word][historical] == old_roots[previous_word]
                    historical_symbol = read(historical_word, historical)
                    if role == 'A':
                        first_symbol = historical_symbol
                        producers = {first_symbol: tag_before[historical_word][historical],
                                     second_symbol: ['input', 'x0', time]}
                    else:
                        second_symbol = historical_symbol
                        producers = {first_symbol: old_tags[first],
                                     second_symbol: tag_before[historical_word][historical]}
                    cut_checks += 1
                text = expression(role, first_symbol, second_symbol)
                value = (operation_a if role == 'A' else operation_b)(first_value, second_value)
                assert value == source[role][sample], (family, variant, time, role, value, source[role][sample])
                if role == 'A' and source['target'][sample] is not None:
                    assert (first_value - second_value) % 256 == source['target'][sample]
                arithmetic_coverage(time, role, first_value, second_value, value)
                integer_checks += 1
                root = ['op', role, sample]
            assert target not in pending, 'multiple writes to one physical word on an edge'
            pending[target] = (value, ['op', role, sample], list(root))
            terms[target] = text
            if target in active_words:
                events.append(oracle_row(time, target + ' - i_D', role, sample, kind, text, producers))
        # Commit together: later copy stages must never see same-edge writes.
        for target, (value, tag, root) in pending.items():
            words[target], tags[target], roots[target] = value, tag, root
        for word in active_words:
            enables[word].append(int(word in pending))
            if start <= time < end:
                equations.append(dict(T=time, target=word + ' - i_D', smt2=terms.get(word, read(word, time))))
        for word in all_words:
            after[word].append(words[word])

    missing_bins = [name for name, row in coverage.items() if row['count'] == 0]
    missing_targets = [value for value in TARGETS if not targets_seen[value]]
    coverage_report = {
        'schema': 'C2_HISTORY_COVERAGE_V1',
        'status': 'PASS' if not missing_bins and not missing_targets else 'FAIL',
        'window': [start, end - 1], 'compare_from': start,
        'counted_interval_inclusive': [coverage_start, end - 1],
        'declared_raw_run_starts': list(range(start, start + PERIOD)),
        'required_bins': coverage, 'target_counts': dict(sorted(targets_seen.items())),
        'missing_bins': missing_bins, 'missing_targets': missing_targets,
        'scope': 'Actual scheduled integer operations inside the extraction window and after every declared representation run_start. Generator/reference coverage only; no recovery, SMT or universal correctness claim.',
    }
    save(out / 'coverage.json', coverage_report)
    if missing_bins or missing_targets:
        raise ValueError('predeclared arithmetic coverage missing: %r / %r' % (missing_bins, missing_targets))
    meta = dict(window=window, widths={w: 8 for w in active_words},
                ports={'in': {'x0': 8}, 'out': {'y0': 8}}, write_enable=enables)
    save(out / 'recovery.json', dict(equations=equations, meta=meta))
    reference_scope = ('Independent integer recurrence and pre-edge scheduled reference; exact finite constructed inputs, '
                       'not RTL, an SMT equivalence proof, or a new recovery implementation')
    save(out / 'oracle.json', dict(
        period=PERIOD, origin=0, protocol='C2_HISTORY_V1', family=family, variant=variant,
        source='known registered reference graph', reference_scope=reference_scope, events=events,
        logical_compute_nodes=['A', 'B'], history_depth=depth,
        reference_graph={'A': ['A[n-%d]' % depth, 'input at A slot'],
                         'B': ['A[n]', 'B[n-%d]' % depth]},
        binding={'A': result_a, 'B': result_b}, copies_per_branch=depth,
        copy_cut_history_checks=cut_checks,
        supplied_priors=['word boundaries', 'numeric order', 'effective writes', 'fixed physical protocol'],
        theorem_scope='registered fixed binding, explicit compute boundaries, simultaneous pre-edge copy stages; only literal identity cuts removed'))
    save(out / 'trace.json', dict(
        schema='C2_TRACE_V1', inputs={'x0': inputs},
        before={w: before[w] for w in active_words}, after={w: after[w] for w in active_words},
        outputs={'y0': outputs}, compare_from=start, reference_scope=reference_scope,
        initialization='All physical words initialized to zero; actual finite prefix executed; no periodic or fictitious history.',
        generator_sha256=sha(__file__)))
    manifest = dict(
        schema='C2_HISTORY_MANIFEST_V1', status='PREPARED_REFERENCE_CHECKED_RECOVERY_NOT_RUN',
        protocol='C2_HISTORY_V1', case=out.name, family=family, variant=variant, history_depth=depth,
        period=PERIOD, origin=0, cycles=cycles, window=[start, end - 1], compare_from=start,
        generator_sha256=sha(__file__), protocol_sha256=sha(Path(__file__).with_name('C2_HISTORY_PROTOCOL.txt')),
        expected_collapsed_feedback_delay=depth,
        expected_base_prefix_copy_equalities=0 if cut else depth * (depth + 1),
        source_graph_integer_checks=integer_checks, copy_cut_history_checks=cut_checks,
        stimulus_scope='Offline fixed vector targeted from the independent source recurrence; only physical input words enter execution.',
        graph_scope='One arithmetic graph at two history depths, not independent algorithms; six controlled development implementations.',
        prepared_hashes={name: sha(out / name) for name in ('recovery.json', 'oracle.json', 'trace.json', 'coverage.json')})
    save(out / 'manifest.json', manifest)
    return {key: manifest[key] for key in ('case', 'family', 'variant', 'history_depth', 'period',
                                          'source_graph_integer_checks', 'expected_base_prefix_copy_equalities')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    receipt = {'schema': 'C2_HISTORY_PREPARATION_V1', 'status': 'INCOMPLETE', 'cases': []}
    save(out / 'preparation.json', receipt)
    try:
        for depth in (3, 5):
            for variant in ('shared', 'separate_binding', 'copy_cuts'):
                name = 'history_rne8_h%d_%s' % (depth, variant)
                receipt['cases'].append(make_case(out / name, depth, variant))
                save(out / 'preparation.json', receipt)
        receipt['status'] = 'PASS_REFERENCE_PREPARATION_ONLY'
        save(out / 'cases.json', receipt['cases'])
        save(out / 'preparation.json', receipt)
        print(json.dumps(receipt, indent=2))
    except Exception as error:
        receipt.update(status='ERROR', error=str(error), exception_type=type(error).__name__)
        save(out / 'preparation.json', receipt)
        raise


if __name__ == '__main__':
    main()

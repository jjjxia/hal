#!/usr/bin/env python3
"""Known registered graphs -> legal storage schedules -> C2 evaluation inputs.

This is a test generator, not a recovery implementation. Logical identities and
forward provenance tokens go ONLY into oracle.json. The frozen InvFold receives
anonymous word equations, widths, enables and input/output phases.
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def signed(value, width=8):
    return value - (1 << width) if value & (1 << (width - 1)) else value


def make_case(out, family, variant):
    from c2_score import operand_occurrences
    width = 8
    # One-at-a-time binding/idle variants, and an explicitly paired copy-cut
    # variant. Every computational node keeps the same fixed result binding.
    shared = variant != 'separate_binding'
    chain = 3 if variant in ('copies', 'copy_cuts') else 1
    idle = 3 if variant == 'idle_slots' else 0
    cut = variant == 'copy_cuts'
    period = 2 * chain + 2 + idle
    a_slot, b_slot = 0, chain + 1
    result_a, result_b = 'w0', 'w0' if shared else 'w1'
    next_word = 1 if shared else 2
    a_chain = ['w%d' % (next_word + i) for i in range(chain)]
    b_chain = ['w%d' % (next_word + chain + i) for i in range(chain)]
    all_words = sorted(set([result_a, result_b] + a_chain + b_chain))
    active_words = sorted({result_a, result_b}) if cut else all_words
    cycles = 80 * period
    start = 8 * period
    window = list(range(start, start + 12 * period))
    rng = random.Random(0xC200 + (family == 'quantized'))
    inputs = [([0, 1, 127, 128, 255, 63, 192, 2][t] if t < 8 else rng.randrange(256)) for t in range(cycles)]
    words = {w: 0 for w in all_words}
    tags = {w: ['initial', w, 0] for w in all_words}
    before = {w: [] for w in all_words}
    after = {w: [] for w in all_words}
    tag_before = {w: [] for w in all_words}
    output_values = []
    enables = {w: [] for w in active_words}
    equations, events = [], []
    history_checks = 0
    graph_state_a = graph_state_b = 0
    logical_values = {}

    def read(w, t):
        return '%s_o_Q_%d' % (w, t)

    def expression(op, t, a, b):
        if family == 'recurrences':
            if op == 'A':
                return '(bvadd %s (bvmul (_ bv3 8) %s))' % (a, b)
            return '(bvadd %s (bvmul (_ bv5 8) %s))' % (a, b)
        if op == 'A':
            return '(bvashr (bvsub %s %s) (_ bv1 8))' % (a, b)
        # A occurs twice at different operand paths. Equal support sets alone
        # cannot detect removal or reassignment of either occurrence.
        return '(bvadd (bvashr (bvsub %s %s) (_ bv1 8)) (bvashr %s (_ bv2 8)))' % (a, b, a)

    def integer_operation(op, a, b):
        if family == 'recurrences':
            return (a + (3 if op == 'A' else 5) * b) & 255
        if op == 'A':
            return (signed((a - b) & 255) >> 1) & 255
        return ((signed((a - b) & 255) >> 1) + (signed(a) >> 2)) & 255

    def oracle_row(t, target, logical, sample, kind, text, sources):
        widths = {symbol: width for symbol in sources}
        operands = []
        for occurrence in operand_occurrences(text, widths):
            operands.append(dict(occurrence, producer=sources[occurrence['symbol']]))
        return dict(T=t, target=target, logical=logical, sample=sample, kind=kind,
                    width=width, operands=operands)

    for t in range(cycles):
        phase, n = t % period, t // period
        old, old_tags = dict(words), {k: list(v) for k, v in tags.items()}
        for w in all_words:
            before[w].append(old[w])
            tag_before[w].append(old_tags[w])
        output_values.append(old[result_b])
        output_text = read(result_b, t)
        events.append(oracle_row(t, 'top_module - y0', 'observation', n, 'observation',
                                 output_text, {output_text: old_tags[result_b]}))
        if t in window:
            equations.append(dict(T=t, target='top_module - y0', smt2=output_text))
        action = None
        if phase == a_slot:
            action = (result_a, 'A', 'compute', a_chain[-1], 'x0')
        elif 1 <= phase <= chain:
            i = phase - 1
            action = (a_chain[i], 'A_move_%d' % i, 'copy', result_a if i == 0 else a_chain[i-1], None)
        elif phase == b_slot:
            action = (result_b, 'B', 'compute', result_a, b_chain[-1])
        elif b_slot + 1 <= phase <= b_slot + chain:
            i = phase - b_slot - 1
            action = (b_chain[i], 'B_move_%d' % i, 'copy', result_b if i == 0 else b_chain[i-1], None)
        for w in active_words:
            enables[w].append(int(action is not None and action[0] == w))
        if action:
            target, role, kind, a, b = action
            if kind == 'copy':
                text = read(a, t)
                sources = {text: old_tags[a]}
                value = old[a]
            else:
                sa, sb = read(a, t), 'top_module_x0_%d' % t if b == 'x0' else read(b, t)
                va, vb = old[a], inputs[t] if b == 'x0' else old[b]
                sources = {sa: old_tags[a], sb: ['input', 'x0', t] if b == 'x0' else old_tags[b]}
                if cut and t >= start:
                    # Remove ONLY identity storage boundaries. The retained
                    # computational result is read at a real historical frame.
                    if role == 'A':
                        historical = t - period + a_slot + 1
                        sa = read(result_a, historical)
                        sources = {sa: tag_before[result_a][historical], sb: ['input', 'x0', t]}
                        assert before[result_a][historical] == va
                    else:
                        historical = t - phase - period + b_slot + 1
                        sb = read(result_b, historical)
                        sources = {sa: old_tags[a], sb: tag_before[result_b][historical]}
                        assert before[result_b][historical] == vb
                    history_checks += 1
                text = expression(role, t, sa, sb)
                value = integer_operation(role, va, vb)
                # Independent sample-level graph evaluation does not inspect
                # physical register identities or recover a last-write table.
                if role == 'A':
                    expected = integer_operation('A', graph_state_a, inputs[t])
                    graph_state_a = expected
                else:
                    expected = integer_operation('B', graph_state_a, graph_state_b)
                    graph_state_b = expected
                assert value == expected, (family, variant, t, role, value, expected)
                logical_values[(role, n)] = value
            words[target] = value
            # Copy identities stay explicit in the raw oracle, even though the
            # sample-level graph uses only A/B. No normalized oracle is supplied
            # to the recovery algorithm.
            tags[target] = ['op', role, n]
            if target in active_words:
                events.append(oracle_row(t, target + ' - i_D', role, n, kind, text, sources))
        for w in active_words:
            if t in window:
                if action and action[0] == w:
                    term = text
                else:
                    term = read(w, t)  # held D is irrelevant, but still explicit
                equations.append(dict(T=t, target=w + ' - i_D', smt2=term))
        for w in all_words:
            after[w].append(words[w])

    # Earlier oracle events for cut histories must retain the actual producing
    # compute identities. Removed-copy events are omitted from expected output.
    meta = dict(window=window, widths={w: width for w in active_words},
                ports={'in': {'x0': width}, 'out': {'y0': width}}, write_enable=enables)
    out.mkdir(parents=True, exist_ok=False)
    save(out/'recovery.json', dict(equations=equations, meta=meta))
    save(out/'oracle.json', dict(period=period, origin=0, events=events,
        family=family, variant=variant, source='known registered reference graph',
        logical_compute_nodes=['A','B'], supplied_priors=['word boundaries','numeric order','effective writes'],
        reference_graph={'A': ['A[n-1]','input at A slot'], 'B': ['A[n]','B[n-1]']},
        binding={'A': result_a, 'B': result_b}, copies_per_branch=chain,
        copy_cut_history_checks=history_checks,
        theorem_scope='registered fixed binding; explicit result boundaries; only identity cuts removed'))
    save(out/'trace.json', dict(schema='C2_TRACE_V1', inputs={'x0': inputs},
        before={w: before[w] for w in active_words}, after={w: after[w] for w in active_words},
        outputs={'y0': output_values}, compare_from=start+3*period,
        reference_scope='Independent integer sample graph plus physical scheduled simulation; finite test, not RTL or universal proof',
        initialization='all physical words zero; finite executed prefix; no fictitious periodic history',
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    return dict(case=out.name, period=period, family=family, variant=variant,
                source_graph_integer_checks=len(logical_values), copy_cut_history_checks=history_checks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for family in ('recurrences','quantized'):
        for variant in ('shared','separate_binding','idle_slots','copies','copy_cuts'):
            rows.append(make_case(args.output/(family+'_'+variant),family,variant))
    save(args.output/'cases.json',rows)
    print(json.dumps(rows,indent=2))


if __name__ == '__main__':
    main()

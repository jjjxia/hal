# DANA (HAL word recovery) on time-multiplexed datapaths — failure analysis (2026-10-30)

## Observation (E1 design: two biquads on one shared multiply-accumulate unit)
White-box scoring (score_dana.py: FDRE -> RTL register through its Q net or the output port its OBUF drives):
acc, st0, y2 exactly one word; w and st2 15 of 16 bits in one word; st1, st3, y1 cut into 15 three-bit words
{y1[i], st1[i], st3[i]} across registers; slot split. (Correction: an earlier note said "DANA 2/8"; that scoring
missed the output registers behind OBUFs and counted 15/16 as wrong.)

## Mechanism (from the netlist, hal/: CE/R pins and consumers)
- y1 and st1 share the clock enable (written in slot 7); st3 has a DIFFERENT enable (slot 15): DANA merged flip-flops
  with different control signals, so the control-signal pass was overridden.
- What y1[i], st1[i], st3[i] share: they enter the shared operand multiplexer of the multiplier at bit position i
  (selected in slots 8, 2/6, 10/14). DANA's successor/predecessor passes group flip-flops that feed the same
  successor structure; a time-multiplexed operand multiplexer makes "same bit position on the shared resource"
  look like "same word".
- acc has CE tied to 1 (its hold is in the D logic), so control-pin grouping cannot separate time-multiplexed
  registers in general anyway.

## Controlled variant V1 (v1_extra_consumers.v): st1 and st3 also drive their own output ports
Same three-bit cross-register words. Hypothesis "only consumer is the shared multiplexer" is REFUTED: extra distinct
consumers do not prevent the merge; the shared-multiplexer successor dominates.

## Controlled variant V2 (v2_no_sharing.v): one operand multiplexer, multiplier and accumulator per filter
Not completed: module identification ran out of memory (bad_alloc at a 6 GB cap; killed at ~6.1 GB RSS with only
2 GB swap active). Needs the 34 GB swap (sudo swapon /swapfile). Expected if the mechanism is right: every register
one word.

## Related evidence in the Klix et al. artifact
Lattice FFT DANA word 45 merges two registers (drii, diri) of a butterfly into one 86-bit word
(research_loop/.../claude/algrw/RESULT.md). Same class of error (registers of a shared structure merged);
whether through the same mechanism is NOT checked.

## Time-aware alternative (hal/e1b.py): 8 of 8 registers on the same design (rules developed on it; dev set).

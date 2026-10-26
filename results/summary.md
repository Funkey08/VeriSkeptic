# Results

| Run | Mode | Problems | Samples | pass@1 | 95% CI | pass@5 | Avg LLM calls | Avg design versions | $ / problem | Total $ |
|---|---|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite_baseline_medium | A. Single call | 152 | 1 | **86.2%** | 80.9–91.4 | n/a | 1.0 | 1.0 | $0.004 | $0.67 |
| gemini-3.5-flash-lite_self_medium | C. Self-verified (own testbench) | 152 | 1 | **87.5%** | 82.2–92.1 | n/a | 5.8 | 1.2 | $0.013 | $2.03 |
| gemini-3.5-flash-lite_oracle_medium | D. Oracle feedback (upper bound) | 152 | 1 | **97.4%** | 94.7–99.3 | n/a | 2.2 | 1.2 | $0.007 | $1.04 |

## Gain over single-call baseline (paired by problem)

| Run | Baseline | Problems compared | Δ pass@1 (pp) | 95% CI (pp) | Fixed / broken vs baseline |
|---|---|---|---|---|---|
| gemini-3.5-flash-lite_self_medium | gemini-3.5-flash-lite_baseline_medium | 152 | +1.3 | -3.9 to +6.6 | 9 / 7 |
| gemini-3.5-flash-lite_oracle_medium | gemini-3.5-flash-lite_baseline_medium | 152 | +11.2 | +5.9 to +17.1 | 19 / 2 |

## Gain from iteration (first design vs. submitted, same run)

| Run | First design | Submitted | Δ (pp) | Fixed / regressed | McNemar p |
|---|---|---|---|---|---|
| gemini-3.5-flash-lite_self_medium | 80.9% | 87.5% | +6.6 | 10 / 0 | 0.00195 |
| gemini-3.5-flash-lite_oracle_medium | 86.2% | 97.4% | +11.2 | 17 / 0 | 1.53e-05 |

## Self-verification reliability

How far can the agent trust its own testbench? *False confidence* = its own testbench passed on the submitted design, but the hidden reference testbench failed.

| Run | Self-test passed | ...and hidden passed | False confidence | Hidden pass when self-test did NOT pass |
|---|---|---|---|---|
| gemini-3.5-flash-lite_self_medium | 152/152 | 133 | 19/152 (12.5%) | 0/0 |

## pass@1 by problem category

| Run | combinational | fsm | sequential |
|---|---|---|---|
| gemini-3.5-flash-lite_baseline_medium | 96.3% (n=82) | 56.7% (n=30) | 87.5% (n=40) |
| gemini-3.5-flash-lite_self_medium | 95.1% (n=82) | 70.0% (n=30) | 85.0% (n=40) |
| gemini-3.5-flash-lite_oracle_medium | 97.6% (n=82) | 96.7% (n=30) | 97.5% (n=40) |

## Final status breakdown

| Run | pass | compile_error | sim_fail | timeout | no_result | no_design | end reasons |
|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite_baseline_medium | 131 | 7 | 14 | 0 | 0 | 0 | end_turn: 152 |
| gemini-3.5-flash-lite_self_medium | 133 | 0 | 19 | 0 | 0 | 0 | submit: 152 |
| gemini-3.5-flash-lite_oracle_medium | 148 | 0 | 4 | 0 | 0 | 0 | submit: 152 |

## Problems fixed by iteration

Runs where the first design failed the hidden tests but the submitted one passed.

- **gemini-3.5-flash-lite_self_medium**: fixed 10, regressed 0
  - e.g. Prob021_mux256to1v, Prob060_m2014_q4k, Prob089_ece241_2014_q5a, Prob116_m2014_q3, Prob119_fsm3, Prob123_bugs_addsubz, Prob136_m2014_q6, Prob137_fsm_serial, Prob138_2012_q2fsm, Prob144_conwaylife
- **gemini-3.5-flash-lite_oracle_medium**: fixed 17, regressed 0
  - e.g. Prob034_dff8, Prob053_m2014_q4d, Prob062_bugs_mux2, Prob066_edgecapture, Prob104_mt2015_muxdff, Prob116_m2014_q3, Prob119_fsm3, Prob124_rule110, Prob136_m2014_q6, Prob137_fsm_serial

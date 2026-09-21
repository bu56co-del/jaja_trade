# Strict band re-entry: definition correction

Run35596700305 at f64cacdb72a0d1503f41a6c177de46b0b984f35f had a semantic omission:
its band crossing rule did not reject a jump beyond the opposite band. Twelve
executed trade-scenario records, involving two distinct entry times with
strategy/cost/path repeats, exposed the problem. The accounting audit passed,
but that is not proof of conformance to the intended strict re-entry setup.
That run is retained and superseded, not deleted or presented as another
independent sample.

The original written protocol required recovery INTO the bands. Both production
and the separate reference now enforce lower+1e-12 <= close <= upper-1e-12.
Two synthetic regression tests cover the mirrored overshoot cases. The corrected
run keeps every other parameter, dataset, primary candidate, exit, cost and risk
limit unchanged. The protocol identifier is unchanged because this repairs its
implementation, not its intended rules; the execution commit identifies the fix.

No new candidates, result-dependent fee reduction, new capital or relaxed losses.
The full2700-case replay replaces the definition-incomplete result for reporting.

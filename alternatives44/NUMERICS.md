
## Numerical audit correction before reporting performance

Full-data independent signal comparison exposed a mathematically unchanged
40-bar width whose two algebraic variance formulas differed at roughly1e-24.
Expansion therefore requires a normalized-width increase greater than1e-12,
the documented feature comparison precision; numeric noise is not a signal.
The identical deadband is covered by tests in both implementations. This is
not a fitted market threshold. The exact source commit and code hashes pin this arithmetic correction. Aggregate reporting
also fixes an unbound win-count variable, with a complete2700-row synthetic
reporting test. Neither correction changes fees, account limits or input data.

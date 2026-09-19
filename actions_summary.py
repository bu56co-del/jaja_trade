"""Publish only this run's validated summary; never infer P&L from job success."""
import hashlib
import json
import os
from pathlib import Path
import re

COMPLETE = 'COMPLETED_EXPLORATORY_SCENARIO_BACKTEST'


def summarize(root: Path, env: dict) -> dict:
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    provenance = {k: env.get(k, '') for k in (
        'GITHUB_REPOSITORY', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'RUNNER_OS')}
    provenance.update(network=env.get('NETWORK', 'mainnet'),
                      tests_outcome=env.get('TEST_OUTCOME', 'unknown'),
                      backtest_outcome=env.get('AUDIT_OUTCOME', 'unknown'))
    (evidence / 'github-provenance.json').write_text(json.dumps(provenance, indent=2))
    output = evidence / 'experiment'
    try:
        latest = json.loads((output / 'LATEST_ATTEMPT.json').read_text())
        attempt = latest['attempt_id']
        if not isinstance(attempt, str) or not re.fullmatch('[a-f0-9]{32}', attempt):
            raise ValueError('Invalid attempt identifier')
        path = output / 'attempts' / attempt / 'SUMMARY.json'
        raw = path.read_bytes()
        if len(raw) > 2_000_000:
            raise ValueError('Oversized summary')
        result = json.loads(raw)
        if result['attempt_id'] != attempt or result['network'] != provenance['network']:
            raise ValueError('Attempt/network mismatch')
        if latest['status'] != result['status']:
            raise ValueError('Attempt status mismatch')
        provenance['summary_sha256'] = hashlib.sha256(raw).hexdigest()
        if result['status'] == COMPLETE:
            if provenance['tests_outcome'] != 'success' or provenance['backtest_outcome'] != 'success':
                raise ValueError('Workflow did not successfully finish both tests and backtest')
            if result['scenario_runs'] != 192 or result['trade_ledger_check'] != 'PASSED':
                raise ValueError('Suite or independent accounting validation incomplete')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'status': 'WORKFLOW_INCOMPLETE', 'historical_model_result': None,
                  'network': provenance['network'], 'reason': str(exc),
                  'future_profitability': 'NOT_ESTABLISHED'}
    result['github_execution'] = provenance
    (evidence / 'RESULT.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    if result['status'] == COMPLETE:
        keys = ('historical_model_result', 'evidence_assessment', 'network', 'period_start', 'period_end',
                'candles', 'scenario_runs', 'original_strategy', 'original_baseline', 'original_cost_stress',
                'original_final_segment', 'candidate_counts_full_window', 'trade_ledger_check',
                'dataset_sha256', 'provenance', 'future_profitability', 'github_execution')
        headline = 'Historical model result: ' + result['historical_model_result']
        body = {k: result[k] for k in keys}
    else:
        headline = 'No profit/loss conclusion: ' + result['status']
        body = result
    # Disable workflow-command parsing while printing any provider-derived error text.
    marker = 'paperlab_' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    print('::stop-commands::' + marker)
    text = '# ' + headline + '\n\n```json\n' + json.dumps(body, indent=2, ensure_ascii=False) + '\n```\n\n'
    text += ('Public-data paper backtest only. No real trades. Costs/path/depth are modelling assumptions; '
             'historical funding rates use an oracle-price proxy. Short data window is not evidence of future income.\n')
    print(text)
    print('::' + marker + '::')
    (evidence / 'RESULT.md').write_text(text, encoding='utf-8')
    if env.get('GITHUB_STEP_SUMMARY'):
        with Path(env['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as handle:
            handle.write(text)
    return result


if __name__ == '__main__':
    summarize(Path(__file__).resolve().parent, dict(os.environ))

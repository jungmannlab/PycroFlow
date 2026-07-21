"""Mine run logs for actual step durations to improve the estimates.

:mod:`PycroFlow.protocols.timing` predicts how long each Run Sequence step
takes; those predictions drive the GUI's progress bars and ETA. Every executed
step also writes what it *actually* took to the run log (see
``AbstractSystemHandler._log_step_timing``), tagged with
:data:`~PycroFlow.protocols.timing.STEP_TIMING_TAG` and carrying a JSON
payload::

    ... | INFO -> STEP_TIMING {"system": "fluid", "step": 3, "type": "inject",
                               "actual_s": 41.2, "estimate_s": 33.0,
                               "volume": 500, "velocity": 1800}

This module reads those records back and summarises estimate-vs-actual per
step type, so a handful of real runs tells you which terms of the model are
off and by how much.

Run it over the logs an acquisition left behind::

    python -m PycroFlow.protocols.timing_analysis path/to/pycroflow.log ...

Since the run's logs are written into the acquisition folder, that is the
same folder as the images.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from PycroFlow.protocols.timing import STEP_TIMING_TAG, format_duration


def parse_step_timings(*sources):
    """Read step-timing records out of one or more run logs.

    Parameters
    ----------
    *sources : str or Path
        Log file paths. A directory is scanned for ``*.log`` files.

    Returns
    -------
    list of dict
        One record per timed step, in file order. Malformed or untagged
        lines are skipped (logs contain plenty of other traffic).
    """
    records = []
    for source in sources:
        path = Path(source)
        files = sorted(path.glob('*.log')) if path.is_dir() else [path]
        for fname in files:
            try:
                text = fname.read_text(errors='replace')
            except OSError:
                continue
            for line in text.splitlines():
                _, tag, payload = line.partition(STEP_TIMING_TAG)
                if not tag:
                    continue
                try:
                    record = json.loads(payload.strip())
                except ValueError:
                    continue
                if isinstance(record, dict):
                    record.setdefault('source', str(fname))
                    records.append(record)
    return records


def summarize(records):
    """Aggregate timing records per (system, step type).

    Returns
    -------
    dict
        ``{(system, type): stats}`` where ``stats`` holds ``n``, the mean /
        median measured and estimated seconds, the total error, and
        ``ratio`` — median actual / median estimate. A ratio above 1 means
        the estimator runs fast (steps take longer than predicted); ``None``
        when nothing was estimated for that type.
    """
    groups = {}
    for record in records:
        actual = record.get('actual_s')
        if actual is None:
            continue
        key = (record.get('system'), record.get('type'))
        groups.setdefault(key, []).append(
            (float(actual), float(record.get('estimate_s') or 0.0)))

    out = {}
    for key, pairs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        actuals = [a for a, _ in pairs]
        estimates = [e for _, e in pairs]
        med_est = statistics.median(estimates)
        out[key] = {
            'n': len(pairs),
            'actual_mean': statistics.mean(actuals),
            'actual_median': statistics.median(actuals),
            'estimate_mean': statistics.mean(estimates),
            'estimate_median': med_est,
            'total_actual': sum(actuals),
            'total_estimate': sum(estimates),
            'ratio': (statistics.median(actuals) / med_est
                      if med_est > 0 else None),
        }
    return out


def _fmt(seconds):
    """Duration for the table: keep sub-minute resolution, then compact."""
    if seconds < 60:
        return '{:.1f}s'.format(seconds)
    return format_duration(seconds)


def format_summary(summary):
    """Render :func:`summarize` output as a readable table."""
    header = "{:<7} {:<16} {:>4} {:>11} {:>11} {:>7}".format(
        'system', 'step type', 'n', 'actual~med', 'est~med', 'ratio')
    lines = [header, '-' * len(header)]
    total_actual = total_estimate = 0.0
    for (system, type_), stat in summary.items():
        ratio = stat['ratio']
        lines.append("{:<7} {:<16} {:>4} {:>11} {:>11} {:>7}".format(
            str(system), str(type_), stat['n'],
            _fmt(stat['actual_median']), _fmt(stat['estimate_median']),
            '{:.2f}'.format(ratio) if ratio is not None else '-'))
        total_actual += stat['total_actual']
        total_estimate += stat['total_estimate']
    lines.append('-' * len(header))
    lines.append("total measured {} vs estimated {}{}".format(
        format_duration(total_actual), format_duration(total_estimate),
        '' if total_estimate <= 0 else
        '  (ratio {:.2f})'.format(total_actual / total_estimate)))
    return '\n'.join(lines)


def main(argv=None):
    """CLI: summarise the step timings in the given logs / folders."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Summarise measured vs estimated Run Sequence step "
                    "durations from PycroFlow run logs.")
    parser.add_argument(
        'logs', nargs='+',
        help="log files, or folders to scan for *.log (e.g. an "
             "acquisition folder)")
    args = parser.parse_args(argv)

    records = parse_step_timings(*args.logs)
    if not records:
        print("No {} records found in {}".format(
            STEP_TIMING_TAG, ', '.join(args.logs)))
        return 1
    print("{} timed steps from {} log(s)\n".format(
        len(records), len({r.get('source') for r in records})))
    print(format_summary(summarize(records)))
    return 0


if __name__ == '__main__':   # pragma: no cover - CLI entry
    raise SystemExit(main())

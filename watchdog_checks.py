"""Validate the published quality report without accepting missing evidence."""


def quality_problems(report):
    if not isinstance(report, dict):
        raise ValueError('质量报告不是对象')
    summary = report.get('summary')
    if not isinstance(summary, dict):
        raise ValueError('质量报告缺少 summary')
    failures = summary.get('fail')
    if type(failures) is not int or failures < 0:
        raise ValueError('质量报告缺少有效的失败计数')
    degraded = report.get('degraded')
    if type(degraded) is not bool:
        raise ValueError('质量报告缺少有效的降级状态')
    checks = report.get('checks')
    if not isinstance(checks, list) or not checks:
        raise ValueError('质量报告缺少检查明细')
    statuses = []
    for check in checks:
        if not isinstance(check, dict) or check.get('status') not in ('pass', 'warn', 'fail'):
            raise ValueError('质量报告检查状态无效')
        statuses.append(check['status'])
    if statuses.count('fail') != failures:
        raise ValueError('质量报告失败计数与明细不一致')
    problems = []
    if failures:
        problems.append('线上质量报告有 %d 项校验失败' % failures)
    if degraded:
        problems.append('数据仍为降级态：%s' % report.get('reason', '未提供原因'))
    return problems

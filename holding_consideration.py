"""Value actual cash and all successor shares after a corporate action.

Values are per dollar invested at the holding-period start. Ordinary cash
dividend reinvestment remains in each supplied total-return growth factor.
Cash consideration is held as cash; it is never reinvested into the parent.
"""
import math

POLICY = 'cash_and_all_successor_shares'


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name+' must be an observed finite number')
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(name+' has an invalid value')
    return value


def distribution_value(pre_event_growth, native_before, cash_per_share, components):
    """Value one verified distribution, retaining every component.

    Each component supplies shares received per old share, native entry price
    and its subsequent total-return growth. A component with another event can
    supply its recursively calculated growth; no price-vintage mixing occurs
    here. Missing component evidence raises instead of reweighting survivors.
    """
    growth = _number(pre_event_growth, 'pre-event total-return growth', positive=True)
    native = _number(native_before, 'pre-event native price', positive=True)
    consideration = _number(cash_per_share, 'cash consideration')
    for component in components:
        shares = _number(component['shares'], 'successor share ratio', positive=True)
        entry = _number(component['native_entry'], 'successor native entry price', positive=True)
        after = _number(component['growth'], 'successor total-return growth')
        consideration += shares*entry*after
    value = growth/native*consideration
    if not math.isfinite(value):
        raise ValueError('non-finite distributed holding value')
    return value

"""Inclusive publication-date limits independent of editable album tags."""
from dataclasses import dataclass
from datetime import date
import calendar
import re


def parse_bound(value, end=False):
    value = (value or '').strip()
    if not value:
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}', value):
            year, month = map(int, value.split('-'))
            return date(year, month, calendar.monthrange(year, month)[1] if end else 1)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value)
    except (ValueError, OverflowError):
        raise ValueError('日期請填 YYYY-MM 或 YYYY-MM-DD，例如 2026-09；留白表示不限') from None


@dataclass(frozen=True)
class DateRange:
    start: str = ''
    end: str = ''

    def bounds(self):
        start, end = parse_bound(self.start), parse_bound(self.end, end=True)
        if start and end and start > end:
            raise ValueError('開始日期不可晚於結束日期')
        return start, end

    @property
    def active(self):
        start, end = self.bounds()
        return bool(start or end)

    def contains(self, value):
        start, end = self.bounds()
        if not start and not end:
            return True
        if not value or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('無法確認影片發表日期，已保留失敗項目供重試，未下載音訊')
        day = date.fromisoformat(value)
        return (not start or start <= day) and (not end or day <= end)


class OutsideDateRange(Exception):
    """Normal skip, not a download error."""

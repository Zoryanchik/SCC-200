"""
Time utilities for transit operations

Handles:
- Seconds since midnight conversions
- ISO 8601 duration parsing
- Time arithmetic with day boundaries
- Operational day calculations
- Delay/cancellation handling
"""

import re
from datetime import datetime, timedelta


def seconds_since_midnight(time_str: str) -> int:
    """
    Convert HH:MM:SS to seconds since midnight
    
    Args:
        time_str: Time in format "HH:MM:SS" or "H:MM:SS"
    
    Returns:
        Seconds since midnight (0-86400)
    
    Examples:
        >>> seconds_since_midnight("00:00:00")
        0
        >>> seconds_since_midnight("12:30:45")
        45045
        >>> seconds_since_midnight("23:59:59")
        86399
    """
    parts = time_str.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2])
    
    return hours * 3600 + minutes * 60 + seconds


def seconds_to_time(seconds: int) -> str:
    """
    Convert seconds since midnight to HH:MM:SS
    
    Args:
        seconds: Seconds since midnight
    
    Returns:
        Time string in format "HH:MM:SS"
    
    Examples:
        >>> seconds_to_time(0)
        "00:00:00"
        >>> seconds_to_time(45045)
        "12:30:45"
        >>> seconds_to_time(86399)
        "23:59:59"
    """
    # Handle times > 86400 (next day)
    seconds = seconds % 86400
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_iso8601_duration(duration_str: str) -> int:
    """
    Parse ISO 8601 duration to seconds
    
    Format: PT[n]H[n]M[n]S
    Examples: PT1H30M45S, PT45M, PT30S
    
    Args:
        duration_str: ISO 8601 duration string
    
    Returns:
        Duration in seconds
    
    Examples:
        >>> parse_iso8601_duration("PT1H30M45S")
        5445
        >>> parse_iso8601_duration("PT30S")
        30
        >>> parse_iso8601_duration("PT2H")
        7200
    """
    # Pattern: PT followed by optional H, M, S components
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str)
    
    if not match:
        return 0
    
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    
    return hours * 3600 + minutes * 60 + seconds


def add_delay(departure_time: int, delay_seconds: int) -> int:
    """
    Add delay to departure time
    
    Args:
        departure_time: Seconds since midnight
        delay_seconds: Delay in seconds (positive = late, negative = early)
    
    Returns:
        Adjusted departure time in seconds
    
    Examples:
        >>> add_delay(43200, 300)  # Noon + 5 min
        43500
    """
    return departure_time + delay_seconds


def get_next_day_time(current_time: int) -> tuple:
    """
    Determine if time crosses into next day
    
    Args:
        current_time: Seconds since midnight
    
    Returns:
        (adjusted_time, day_offset) where day_offset is 0 or 1
    
    Examples:
        >>> get_next_day_time(43200)  # Noon
        (43200, 0)
        >>> get_next_day_time(86400)  # Exactly midnight next day
        (0, 1)
        >>> get_next_day_time(90000)  # Past midnight
        (3600, 1)
    """
    day_offset = current_time // 86400
    adjusted_time = current_time % 86400
    
    return adjusted_time, day_offset


def is_operational_day(date: str, operational_days: str) -> bool:
    """
    Check if date falls on operational day
    
    Args:
        date: Date in format "YYYY-MM-DD"
        operational_days: Days as "SMTWRFX" where:
                         S=Sunday, M=Monday, T=Tuesday, W=Wednesday
                         R=Thursday, F=Friday, X=Saturday
    
    Returns:
        True if service operates on this day
    
    Examples:
        >>> is_operational_day("2026-01-31", "MTWRFX")  # Saturday
        True
        >>> is_operational_day("2026-02-01", "MTWRFX")  # Sunday
        False
    """
    day_map = {
        'S': 6,  # Sunday
        'M': 0,  # Monday
        'T': 1,  # Tuesday
        'W': 2,  # Wednesday
        'R': 3,  # Thursday
        'F': 4,  # Friday
        'X': 5,  # Saturday
    }
    
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    weekday = date_obj.weekday()  # 0=Monday, 6=Sunday
    
    # Convert Python weekday (0-6) to calendar weekday (0=Monday, 6=Sunday)
    if weekday == 6:
        python_day = 'S'
    else:
        # M=0, T=1, ..., X=5 (for Mon-Sat)
        python_day = list('MTWRFX')[weekday]
    
    return python_day in operational_days


def three_day_window(date: str, mode: str = 'center') -> list:
    """
    Return a list of three ISO date strings representing a 3-day window.

    Args:
        date: center (or start) date in YYYY-MM-DD
        mode: 'center' (D-1, D, D+1) or 'start' (D, D+1, D+2)

    Returns:
        List of three dates as strings in YYYY-MM-DD order.

    Examples:
        >>> three_day_window('2026-02-05')
        ['2026-02-04', '2026-02-05', '2026-02-06']
        >>> three_day_window('2026-02-05', mode='start')
        ['2026-02-05', '2026-02-06', '2026-02-07']
    """
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except Exception:
        raise ValueError("date must be YYYY-MM-DD")

    if mode not in ('center', 'start'):
        raise ValueError("mode must be 'center' or 'start'")

    if mode == 'center':
        days = [d + timedelta(days=offset) for offset in (-1, 0, 1)]
    else:
        days = [d + timedelta(days=offset) for offset in (0, 1, 2)]

    return [day.strftime("%Y-%m-%d") for day in days]


def operates_in_3day_window(date: str, operational_days: str, mode: str = 'center') -> bool:
    """
    Return True if a service operating pattern (operational_days) is active on any of the
    three days in the window around `date`.

    Args:
        date: date string YYYY-MM-DD
        operational_days: pattern string like 'MTWRFXS' (see is_operational_day)
        mode: 'center' or 'start' (see three_day_window)

    Returns:
        True if service operates on at least one of the three days.

    Examples:
        >>> operates_in_3day_window('2026-02-05', 'MTWRF')
        True
    """
    window = three_day_window(date, mode=mode)
    for d in window:
        if is_operational_day(d, operational_days):
            return True
    return False


def time_between_stops(arrival_time: int, departure_time: int) -> int:
    """
    Calculate dwell time between arrival and departure
    
    Args:
        arrival_time: Arrival time in seconds since midnight
        departure_time: Departure time in seconds since midnight
    
    Returns:
        Dwell time in seconds
    
    Examples:
        >>> time_between_stops(43200, 43230)  # 5 min stop
        30
    """
    if departure_time >= arrival_time:
        return departure_time - arrival_time
    else:
        # Crosses midnight
        return (86400 - arrival_time) + departure_time


def calculate_travel_time(departure: int, arrival: int) -> int:
    """
    Calculate travel time between two stops
    
    Handles crossing midnight boundary
    
    Args:
        departure: Departure time in seconds since midnight
        arrival: Arrival time in seconds since midnight
    
    Returns:
        Travel time in seconds
    
    Examples:
        >>> calculate_travel_time(43200, 43800)  # 10 min trip
        600
        >>> calculate_travel_time(82800, 3600)  # Crosses midnight
        7200
    """
    if arrival >= departure:
        return arrival - departure
    else:
        # Crosses midnight
        return (86400 - departure) + arrival


def format_duration(seconds: int) -> str:
    """
    Format seconds as human-readable duration
    
    Args:
        seconds: Duration in seconds
    
    Returns:
        Formatted string (e.g., "1h 30m 45s")
    
    Examples:
        >>> format_duration(5445)
        "1h 30m 45s"
        >>> format_duration(45)
        "45s"
        >>> format_duration(3600)
        "1h"
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0:
        parts.append(f"{secs}s")
    
    return " ".join(parts) if parts else "0s"


class TimeRange:
    """Helper class for time range operations"""
    
    def __init__(self, start: int, end: int):
        """
        Initialize time range
        
        Args:
            start: Start time in seconds since midnight
            end: End time in seconds since midnight
        """
        self.start = start
        self.end = end
    
    def contains(self, time: int) -> bool:
        """Check if time falls within range"""
        if self.end >= self.start:
            return self.start <= time <= self.end
        else:
            # Wraps midnight
            return time >= self.start or time <= self.end
    
    def overlap(self, other: 'TimeRange') -> bool:
        """Check if ranges overlap"""
        if self.contains(other.start) or self.contains(other.end):
            return True
        if other.contains(self.start) or other.contains(self.end):
            return True
        return False
    
    def __str__(self):
        return f"{seconds_to_time(self.start)} - {seconds_to_time(self.end)}"
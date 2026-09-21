"""Fair Value Gap detection for SMC trading."""

import pandas as pd
from loguru import logger


class FVGDetector:
    """
    Detects Fair Value Gaps (FVGs) based on ICT methodology.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.fvg_min_pips = self.config.get("fvg_min_pips", 2.0)
        self.fvg_max_age = self.config.get("fvg_max_age_hours", 12)
        self.pip_size = self.config.get("pip_size", 0.0001)
        logger.info("FVGDetector initialized")

    def find_fvgs(self, df):
        """Find Fair Value Gaps using three-candle pattern."""
        fvgs = []

        for i in range(1, len(df) - 1):
            prev_high = df["high"].iloc[i - 1]
            prev_low = df["low"].iloc[i - 1]
            next_high = df["high"].iloc[i + 1]
            next_low = df["low"].iloc[i + 1]

            # Bullish FVG: next low above previous high (gap)
            if next_low > prev_high:
                gap_low = prev_high
                gap_high = next_low
                gap_size = gap_high - gap_low
                gap_pips = gap_size / self.pip_size
                if gap_pips >= self.fvg_min_pips:
                    fvgs.append(
                        {
                            "type": "BULLISH",
                            "zone": [gap_low, gap_high],
                            "time": df.index[i],
                            "gap_size": gap_size,
                            "strength": gap_pips / 10,
                        }
                    )

            # Bearish FVG: next high below previous low (gap)
            elif next_high < prev_low:
                gap_low = next_high
                gap_high = prev_low
                gap_size = gap_high - gap_low
                gap_pips = gap_size / self.pip_size
                if gap_pips >= self.fvg_min_pips:
                    fvgs.append(
                        {
                            "type": "BEARISH",
                            "zone": [gap_low, gap_high],
                            "time": df.index[i],
                            "gap_size": gap_size,
                            "strength": gap_pips / 10,
                        }
                    )

        return fvgs

    def filter_by_age(self, fvgs, current_time):
        """Remove FVGs older than max age."""
        filtered = []
        for fvg in fvgs:
            age_hours = (current_time - fvg["time"]).total_seconds() / 3600
            if age_hours <= self.fvg_max_age:
                filtered.append(fvg)
        return filtered

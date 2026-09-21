"""Point of Interest Manager - combines and scores all SMC patterns."""

from loguru import logger
from src.smc.order_blocks import OrderBlockDetector
from src.smc.fair_value_gaps import FVGDetector


class POIManager:
    """
    Manages all Points of Interest (Order Blocks, FVGs) and selects the best ones.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.ob_detector = OrderBlockDetector(config)
        self.fvg_detector = FVGDetector(config)
        self.active_pois = {"BULLISH": [], "BEARISH": []}
        logger.info("POIManager initialized")

    def update(self, df):
        """Update POIs with new data."""
        # Calculate ATR
        atr = self.ob_detector.calculate_atr(df)

        # Find new patterns
        order_blocks = self.ob_detector.find_order_blocks(df, atr)
        fvgs = self.fvg_detector.find_fvgs(df)

        # Combine and categorize
        all_pois = order_blocks + fvgs

        # Clear and rebuild active POIs
        self.active_pois = {"BULLISH": [], "BEARISH": []}
        for poi in all_pois:
            self.active_pois[poi["type"]].append(poi)

        # Filter by age (OBs vs FVGs)
        current_time = df.index[-1]
        # Separate by origin (OB vs FVG) using available keys
        bullish_obs = [p for p in self.active_pois["BULLISH"] if "impulse_time" in p]
        bearish_obs = [p for p in self.active_pois["BEARISH"] if "impulse_time" in p]
        bullish_fvgs = [p for p in self.active_pois["BULLISH"] if "gap_size" in p]
        bearish_fvgs = [p for p in self.active_pois["BEARISH"] if "gap_size" in p]

        bullish_obs = self.ob_detector.filter_by_age(bullish_obs, current_time)
        bearish_obs = self.ob_detector.filter_by_age(bearish_obs, current_time)
        bullish_fvgs = self.fvg_detector.filter_by_age(bullish_fvgs, current_time)
        bearish_fvgs = self.fvg_detector.filter_by_age(bearish_fvgs, current_time)

        self.active_pois["BULLISH"] = bullish_obs + bullish_fvgs
        self.active_pois["BEARISH"] = bearish_obs + bearish_fvgs

        logger.debug(
            f"Updated POIs: {len(self.active_pois['BULLISH'])} bullish, {len(self.active_pois['BEARISH'])} bearish"
        )

    def get_best_poi(self, direction):
        """Get the highest-scoring POI for a given direction."""
        pois = self.active_pois.get(direction, [])
        if not pois:
            return None

        # Score by strength
        return max(pois, key=lambda x: x.get("strength", 1))

    def is_price_in_zone(self, price, zone):
        """Check if price is within a zone."""
        return zone[0] <= price <= zone[1]

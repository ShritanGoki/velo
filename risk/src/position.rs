//! Net position and P&L tracking. See docs/risk-engine-design-doc.md §4.
//!
//! Every observed fill is treated as a trade from the "incoming" (aggressor)
//! side's perspective - i.e. as if the resting side of every trade were an
//! external counterparty, even when (as in Milestone 3's single-participant
//! synthetic session) it technically wasn't. This is a documented
//! simplification: with real counterparties in Milestones 5-6, resting
//! liquidity genuinely will belong to someone else, and this model becomes
//! exactly correct without any change - it's the honest way to track "our"
//! exposure in a solo synthetic session where the alternative (netting every
//! fill against itself as a wash trade) would make position tracking
//! trivially and permanently zero, defeating the point of this milestone.

use crate::protocol::Side;

#[derive(Debug, Default)]
pub struct PositionTracker {
    pub net_position: i64,
    pub realized_pnl_ticks: i64,
    pub last_trade_price_ticks: i64,
    avg_entry_price_ticks: f64,
}

impl PositionTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn on_fill(&mut self, incoming_side: Side, quantity: u32, price_ticks: i64) {
        let signed_qty: i64 = match incoming_side {
            Side::Buy => quantity as i64,
            Side::Sell => -(quantity as i64),
        };

        self.last_trade_price_ticks = price_ticks;

        let same_direction = self.net_position == 0 || self.net_position.signum() == signed_qty.signum();

        if same_direction {
            let old_abs = self.net_position.unsigned_abs() as f64;
            let add_abs = signed_qty.unsigned_abs() as f64;
            self.avg_entry_price_ticks =
                (self.avg_entry_price_ticks * old_abs + price_ticks as f64 * add_abs) / (old_abs + add_abs);
            self.net_position += signed_qty;
        } else {
            let direction = self.net_position.signum();
            let closing_qty = signed_qty.abs().min(self.net_position.abs());
            self.realized_pnl_ticks += direction * closing_qty * (price_ticks - self.avg_entry_price_ticks as i64);

            self.net_position += signed_qty;

            if self.net_position != 0 && self.net_position.signum() != direction {
                // Flipped through flat to the opposite side: the remainder
                // opens a fresh position at this fill's price.
                self.avg_entry_price_ticks = price_ticks as f64;
            }
        }
    }

    pub fn unrealized_pnl_ticks(&self) -> i64 {
        self.net_position * (self.last_trade_price_ticks - self.avg_entry_price_ticks as i64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opening_a_long_position_has_no_realized_pnl() {
        let mut tracker = PositionTracker::new();
        tracker.on_fill(Side::Buy, 10, 100);
        assert_eq!(tracker.net_position, 10);
        assert_eq!(tracker.realized_pnl_ticks, 0);
    }

    #[test]
    fn partial_close_realizes_pnl_on_the_closed_portion_only() {
        let mut tracker = PositionTracker::new();
        tracker.on_fill(Side::Buy, 10, 100);   // long 10 @ 100
        tracker.on_fill(Side::Sell, 4, 105);   // sell 4 @ 105: realize 4 * 5 = 20

        assert_eq!(tracker.net_position, 6);
        assert_eq!(tracker.realized_pnl_ticks, 20);
    }

    #[test]
    fn flipping_through_flat_opens_a_fresh_position() {
        let mut tracker = PositionTracker::new();
        tracker.on_fill(Side::Buy, 10, 100);   // long 10 @ 100
        tracker.on_fill(Side::Sell, 15, 110);  // realize on 10, flip to short 5 @ 110

        assert_eq!(tracker.net_position, -5);
        assert_eq!(tracker.realized_pnl_ticks, 10 * (110 - 100));

        tracker.on_fill(Side::Buy, 5, 108);    // close the short: profit since price fell from 110
        assert_eq!(tracker.net_position, 0);
        assert_eq!(tracker.realized_pnl_ticks, 10 * (110 - 100) + 5 * (110 - 108));
    }

    #[test]
    fn unrealized_pnl_tracks_mark_against_average_entry() {
        let mut tracker = PositionTracker::new();
        tracker.on_fill(Side::Buy, 10, 100);
        tracker.on_fill(Side::Buy, 10, 110); // avg entry now 105
        assert_eq!(tracker.net_position, 20);

        tracker.last_trade_price_ticks = 120; // pretend a later, unrelated mark
        assert_eq!(tracker.unrealized_pnl_ticks(), 20 * (120 - 105));
    }
}

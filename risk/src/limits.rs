//! Risk limit enforcement. See docs/risk-engine-design-doc.md §5.
//!
//! One limit for this milestone: max absolute position. On breach, the
//! action is to flatten via a single corrective market order sized to bring
//! net_position back to exactly zero - not to enumerate and cancel specific
//! resting orders, since the risk engine only has fill visibility (§3.2 of
//! the design doc), not full order-book visibility, so it can't reliably
//! tell which ids are still resting.

use crate::protocol::Side;

pub struct RiskLimits {
    pub max_abs_position: i64,
}

#[derive(Debug, PartialEq, Eq)]
pub struct FlattenAction {
    pub side: Side,
    pub quantity: u32,
}

/// Returns the flattening action needed if net_position breaches the limit,
/// or None if it's within bounds.
pub fn check_breach(limits: &RiskLimits, net_position: i64) -> Option<FlattenAction> {
    if net_position.abs() <= limits.max_abs_position {
        return None;
    }

    let side = if net_position > 0 { Side::Sell } else { Side::Buy };
    Some(FlattenAction { side, quantity: net_position.unsigned_abs() as u32 })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn within_limit_is_not_a_breach() {
        let limits = RiskLimits { max_abs_position: 100 };
        assert_eq!(check_breach(&limits, 50), None);
        assert_eq!(check_breach(&limits, -100), None);
    }

    #[test]
    fn long_breach_flattens_with_a_sell() {
        let limits = RiskLimits { max_abs_position: 100 };
        assert_eq!(check_breach(&limits, 105), Some(FlattenAction { side: Side::Sell, quantity: 105 }));
    }

    #[test]
    fn short_breach_flattens_with_a_buy() {
        let limits = RiskLimits { max_abs_position: 100 };
        assert_eq!(check_breach(&limits, -130), Some(FlattenAction { side: Side::Buy, quantity: 130 }));
    }
}

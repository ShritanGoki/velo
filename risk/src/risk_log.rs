//! Append-only JSON Lines risk state log. See docs/risk-engine-design-doc.md §6.
//!
//! One JSON object per line, no shared schema/array wrapper - each line is
//! independently parseable, which is what a later Milestone 10 dashboard
//! (or just `jq`) wants to tail/replay without any migration concerns.
//! Hand-rolled rather than pulling in serde_json: every field here is a
//! plain number, so a dependency would buy nothing but a longer build.

use std::fs::{File, OpenOptions};
use std::io::{self, Write};

pub struct RiskLog {
    file: File,
}

impl RiskLog {
    pub fn create(path: &str) -> io::Result<Self> {
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(RiskLog { file })
    }

    pub fn position_update(
        &mut self,
        net_position: i64,
        realized_pnl_ticks: i64,
        unrealized_pnl_ticks: i64,
        ts: f64,
    ) -> io::Result<()> {
        writeln!(
            self.file,
            r#"{{"event": "position_update", "net_position": {net_position}, "realized_pnl_ticks": {realized_pnl_ticks}, "unrealized_pnl_ticks": {unrealized_pnl_ticks}, "ts": {ts}}}"#
        )
    }

    pub fn halt(&mut self, net_position: i64, max_abs_position: i64, order_id: u64, ts: f64) -> io::Result<()> {
        writeln!(
            self.file,
            r#"{{"event": "halt", "net_position": {net_position}, "max_abs_position": {max_abs_position}, "action": "flatten_market_order", "order_id": {order_id}, "ts": {ts}}}"#
        )
    }
}

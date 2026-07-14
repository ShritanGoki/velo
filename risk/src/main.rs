//! risk_engine: subscribes to the matching engine's fill stream, tracks
//! position/P&L for the monitored account, and flattens on a configured
//! exposure breach. See docs/risk-engine-design-doc.md.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::{SystemTime, UNIX_EPOCH};

use risk_engine::limits::{check_breach, RiskLimits};
use risk_engine::position::PositionTracker;
use risk_engine::protocol::{self, ClientOrderMsg, MsgType, OrderType};
use risk_engine::risk_log::RiskLog;

// Order ids starting here are reserved for this engine's own flattening
// orders, kept out of the strategy's range (which starts at 1, see
// strategy/orderbook_client/strategy.py) purely by convention - see
// docs/risk-engine-design-doc.md §8 for why this isn't an enforced
// invariant and what a real system would do instead.
const FLATTEN_ORDER_ID_BASE: u64 = 1_000_000_000;

struct Args {
    host: String,
    port: u16,
    max_abs_position: i64,
    log_path: String,
}

fn parse_args() -> Args {
    let mut host = "127.0.0.1".to_string();
    let mut port: u16 = 9999;
    let mut max_abs_position: i64 = 100;
    let mut log_path = "risk_log.jsonl".to_string();

    let mut args = std::env::args().skip(1);
    while let Some(flag) = args.next() {
        let value = args.next().unwrap_or_else(|| panic!("missing value for {flag}"));
        match flag.as_str() {
            "--host" => host = value,
            "--port" => port = value.parse().expect("--port must be a u16"),
            "--max-abs-position" => max_abs_position = value.parse().expect("--max-abs-position must be an integer"),
            "--log" => log_path = value,
            other => panic!("unknown argument {other}"),
        }
    }

    Args { host, port, max_abs_position, log_path }
}

fn now_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64()
}

fn read_exact_or_none(stream: &mut TcpStream, buf: &mut [u8]) -> Option<()> {
    stream.read_exact(buf).ok()
}

fn main() {
    let args = parse_args();
    let limits = RiskLimits { max_abs_position: args.max_abs_position };
    let mut position = PositionTracker::new();
    let mut log = RiskLog::create(&args.log_path).expect("failed to open risk log");
    let mut next_flatten_id = FLATTEN_ORDER_ID_BASE;

    let mut stream = TcpStream::connect((args.host.as_str(), args.port)).expect("failed to connect to orderbook_server");
    stream.write_all(&protocol::encode_subscribe_fills()).expect("failed to send SubscribeFills");

    println!("risk_engine subscribed to fills on {}:{} (max_abs_position={})", args.host, args.port, args.max_abs_position);

    loop {
        let mut tag_buf = [0u8; 1];
        if read_exact_or_none(&mut stream, &mut tag_buf).is_none() {
            println!("risk_engine: server closed the connection, exiting");
            break;
        }

        let Some(size) = protocol::payload_size(tag_buf[0]) else {
            eprintln!("risk_engine: unknown message tag {:#x}, exiting", tag_buf[0]);
            break;
        };

        let mut payload = vec![0u8; size];
        if size > 0 && read_exact_or_none(&mut stream, &mut payload).is_none() {
            println!("risk_engine: connection closed mid-message, exiting");
            break;
        }

        if tag_buf[0] == MsgType::Fill as u8 {
            let fill = protocol::decode_fill(&payload);
            position.on_fill(fill.incoming_side(), fill.quantity, fill.price_ticks);

            let ts = now_secs();
            log.position_update(position.net_position, position.realized_pnl_ticks,
                                 position.unrealized_pnl_ticks(), ts)
                .expect("failed to write position_update");

            if let Some(action) = check_breach(&limits, position.net_position) {
                let order_id = next_flatten_id;
                next_flatten_id += 1;

                let flatten_msg = ClientOrderMsg {
                    client_order_id: order_id,
                    side: action.side,
                    order_type: OrderType::Market,
                    price_ticks: 0,
                    quantity: action.quantity,
                };
                stream.write_all(&protocol::encode_new_order(&flatten_msg))
                    .expect("failed to send flatten order");

                log.halt(position.net_position, limits.max_abs_position, order_id, ts)
                    .expect("failed to write halt event");

                println!(
                    "risk_engine: BREACH net_position={} limit={} -> flatten order #{} ({:?} {})",
                    position.net_position, limits.max_abs_position, order_id, action.side, action.quantity
                );
            }
        }
        // Acks (including for our own flatten orders) aren't otherwise
        // consumed - their effect shows up as a subsequent Fill, which is
        // what actually updates position.
    }
}

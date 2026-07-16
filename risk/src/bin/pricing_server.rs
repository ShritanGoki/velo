//! pricing_server: exposes Milestone 7's `price_scalar` Black-Scholes
//! pricer over a small, dedicated socket protocol, so the Python
//! delta-hedging strategy (Milestone 8) can price its options book
//! without needing FFI bindings. See docs/delta-hedging-design-doc.md §3.
//!
//! Uses price_scalar, not price_batch_simd: each request here is one
//! option, never a batch, and Milestone 7's own benchmark (see
//! docs/options-pricing-design-doc.md §6.1) already found scalar faster
//! than SIMD on this hardware - this is applying that finding, not
//! reaching for the fancier-sounding option out of habit.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;

use risk_engine::pricing::{price_scalar, OptionParams};
use risk_engine::pricing_protocol::{
    decode_request, encode_response, PriceResponse, PRICE_REQUEST_SIZE, PRICE_REQUEST_TAG,
};

// ES: 0.25 index points per tick - same convention as
// strategy/orderbook_client/ticks.py, converted here at the one boundary
// that needs a real price rather than a tick count.
const TICK_SIZE: f64 = 0.25;

fn handle_connection(mut stream: TcpStream) {
    loop {
        let mut tag = [0u8; 1];
        if stream.read_exact(&mut tag).is_err() {
            return; // client disconnected
        }
        if tag[0] != PRICE_REQUEST_TAG {
            eprintln!("pricing_server: unknown tag {:#x}, closing connection", tag[0]);
            return;
        }

        let mut payload = vec![0u8; PRICE_REQUEST_SIZE];
        if stream.read_exact(&mut payload).is_err() {
            return;
        }

        let req = decode_request(&payload);
        let params = OptionParams {
            spot: req.spot_ticks as f64 * TICK_SIZE,
            strike: req.strike_ticks as f64 * TICK_SIZE,
            rate: req.rate,
            vol: req.vol,
            time_to_expiry: req.time_to_expiry,
            is_call: req.is_call,
        };
        let greeks = price_scalar(&params);

        let response = PriceResponse {
            price: greeks.price,
            delta: greeks.delta,
            gamma: greeks.gamma,
            vega: greeks.vega,
            theta: greeks.theta,
            rho: greeks.rho,
        };
        if stream.write_all(&encode_response(&response)).is_err() {
            return;
        }
    }
}

fn main() {
    let port: u16 = std::env::args().nth(1).and_then(|s| s.parse().ok()).unwrap_or(9998);
    let listener = TcpListener::bind(("127.0.0.1", port)).expect("failed to bind pricing_server");
    println!("pricing_server listening on port {port}");

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                thread::spawn(move || handle_connection(stream));
            }
            Err(e) => eprintln!("pricing_server: accept error: {e}"),
        }
    }
}

//! Milestone 8 test case #1: pricing_server round-trip against the real
//! compiled binary, over a real socket. See docs/delta-hedging-design-doc.md §8.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use risk_engine::pricing::{price_scalar, OptionParams};
use risk_engine::pricing_protocol::{
    decode_response, encode_request, PriceRequest, PRICE_RESPONSE_SIZE, PRICE_RESPONSE_TAG,
};

struct ServerProcess {
    child: Child,
}

impl ServerProcess {
    fn start(port: u16) -> ServerProcess {
        let bin = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target").join("debug").join("pricing_server");
        assert!(bin.exists(), "run `cargo build --bins` before this test");

        let child = Command::new(bin)
            .arg(port.to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("failed to spawn pricing_server");

        for _ in 0..200 {
            if TcpStream::connect(("127.0.0.1", port)).is_ok() {
                return ServerProcess { child };
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        panic!("pricing_server never started listening");
    }
}

impl Drop for ServerProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[test]
fn round_trip_matches_price_scalar_directly() {
    let port = 19801;
    let _server = ServerProcess::start(port);
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();

    let req = PriceRequest {
        spot_ticks: 30412,   // 7603.00
        strike_ticks: 30800, // 7700.00
        rate: 0.03,
        vol: 0.22,
        time_to_expiry: 0.25,
        is_call: true,
    };
    stream.write_all(&encode_request(&req)).unwrap();

    let mut tag = [0u8; 1];
    stream.read_exact(&mut tag).unwrap();
    assert_eq!(tag[0], PRICE_RESPONSE_TAG);

    let mut payload = vec![0u8; PRICE_RESPONSE_SIZE];
    stream.read_exact(&mut payload).unwrap();
    let response = decode_response(&payload);

    let expected = price_scalar(&OptionParams {
        spot: req.spot_ticks as f64 * 0.25,
        strike: req.strike_ticks as f64 * 0.25,
        rate: req.rate,
        vol: req.vol,
        time_to_expiry: req.time_to_expiry,
        is_call: req.is_call,
    });

    assert!((response.price - expected.price).abs() < 1e-9);
    assert!((response.delta - expected.delta).abs() < 1e-9);
    assert!((response.gamma - expected.gamma).abs() < 1e-9);
    assert!((response.vega - expected.vega).abs() < 1e-9);
    assert!((response.theta - expected.theta).abs() < 1e-9);
    assert!((response.rho - expected.rho).abs() < 1e-9);
}

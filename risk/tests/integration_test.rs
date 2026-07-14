//! Integration tests for risk_engine's read loop, run against a real
//! orderbook_server subprocess - see docs/risk-engine-design-doc.md §9,
//! test cases #2, #4, #5, #6.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use risk_engine::protocol::{self, ClientOrderMsg, MsgType, OrderType, Side};

fn engine_build_dir() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("engine").join("build")
}

struct ServerProcess {
    child: Child,
}

impl ServerProcess {
    fn start(port: u16) -> ServerProcess {
        let bin = engine_build_dir().join("orderbook_server");
        assert!(bin.exists(), "{:?} not built - run `make server` in engine/ first", bin);

        let child = Command::new(bin)
            .arg(port.to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("failed to spawn orderbook_server");

        for _ in 0..200 {
            if TcpStream::connect(("127.0.0.1", port)).is_ok() {
                return ServerProcess { child };
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        panic!("orderbook_server never started listening");
    }
}

impl Drop for ServerProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn send_new_order(stream: &mut TcpStream, id: u64, side: Side, order_type: OrderType, price: i64, qty: u32) {
    let msg = ClientOrderMsg { client_order_id: id, side, order_type, price_ticks: price, quantity: qty };
    stream.write_all(&protocol::encode_new_order(&msg)).unwrap();
}

fn recv_one(stream: &mut TcpStream) -> (u8, Vec<u8>) {
    let mut tag = [0u8; 1];
    stream.read_exact(&mut tag).unwrap();
    let size = protocol::payload_size(tag[0]).expect("unknown tag");
    let mut payload = vec![0u8; size];
    if size > 0 {
        stream.read_exact(&mut payload).unwrap();
    }
    (tag[0], payload)
}

fn drain_acks_and_fills(stream: &mut TcpStream, count: usize) -> Vec<(u8, Vec<u8>)> {
    (0..count).map(|_| recv_one(stream)).collect()
}

#[test]
fn subscriber_receives_fill_it_was_not_a_counterparty_to() {
    let port = 19701;
    let _server = ServerProcess::start(port);

    let mut trader = TcpStream::connect(("127.0.0.1", port)).unwrap();
    let mut subscriber = TcpStream::connect(("127.0.0.1", port)).unwrap();
    subscriber.write_all(&protocol::encode_subscribe_fills()).unwrap();

    send_new_order(&mut trader, 1, Side::Sell, OrderType::Limit, 100, 50);
    recv_one(&mut trader); // ack

    send_new_order(&mut trader, 2, Side::Buy, OrderType::Limit, 100, 50);
    drain_acks_and_fills(&mut trader, 2); // ack + fill for the trader itself

    let (tag, payload) = recv_one(&mut subscriber);
    assert_eq!(tag, MsgType::Fill as u8);
    let fill = protocol::decode_fill(&payload);
    assert_eq!(fill.resting_order_id, 1);
    assert_eq!(fill.incoming_order_id, 2);
}

fn spawn_risk_engine(port: u16, max_abs_position: i64, log_path: &std::path::Path) -> Child {
    let risk_bin = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("target").join("debug").join("risk_engine");
    assert!(risk_bin.exists(), "run `cargo build` before the integration tests");

    Command::new(risk_bin)
        .args([
            "--host", "127.0.0.1",
            "--port", &port.to_string(),
            "--max-abs-position", &max_abs_position.to_string(),
            "--log", log_path.to_str().unwrap(),
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("failed to spawn risk_engine")
}

#[test]
fn breach_triggers_a_flatten_order_and_a_halt_log_line() {
    let port = 19702;
    let _server = ServerProcess::start(port);

    let log_path = std::env::temp_dir().join(format!("risk_log_test_{port}.jsonl"));
    let _ = std::fs::remove_file(&log_path);

    let mut risk_child = spawn_risk_engine(port, /*max_abs_position=*/20, &log_path);
    // Wait for the risk_engine's startup line so we know it has subscribed
    // before the trader starts crossing the spread.
    let stdout = risk_child.stdout.take().unwrap();
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    assert!(line.contains("subscribed"), "unexpected risk_engine startup output: {line}");

    let mut trader = TcpStream::connect(("127.0.0.1", port)).unwrap();

    // Rest a big sell, then buy through it in a way that pushes net
    // position (from the aggressor's/incoming side's perspective) past the
    // configured limit of 20.
    send_new_order(&mut trader, 1, Side::Sell, OrderType::Limit, 100, 100);
    recv_one(&mut trader); // ack

    send_new_order(&mut trader, 2, Side::Buy, OrderType::Limit, 100, 30); // breaches: net_position=30 > 20
    drain_acks_and_fills(&mut trader, 2); // ack + fill

    // Give the risk_engine a moment to process the fill and react.
    std::thread::sleep(Duration::from_millis(200));

    let log_contents = std::fs::read_to_string(&log_path).unwrap();
    assert!(log_contents.contains("\"event\": \"halt\""), "expected a halt line in {log_contents}");
    assert!(log_contents.contains("\"action\": \"flatten_market_order\""));

    let _ = risk_child.kill();
    let _ = risk_child.wait();
    let _ = std::fs::remove_file(&log_path);
}

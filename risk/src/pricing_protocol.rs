//! Wire protocol for `pricing_server` — a small, separate protocol from
//! `engine/include/orderbook/protocol.hpp`'s order-matching one (see
//! docs/delta-hedging-design-doc.md §5): pure synchronous request/
//! response, no Ack/Cancel semantics, since there's nothing to
//! acknowledge or cancel here. Same framing convention as the order
//! protocol (1-byte tag, fixed-size big-endian payload) for consistency,
//! but its own tag namespace and its own port — a genuinely different
//! service, not an extension of order matching.
//!
//! Prices are ticks (matching this project's tick-everywhere convention,
//! e.g. `strategy/orderbook_client/ticks.py`), converted to real prices
//! at the one boundary that needs them (pricing_server's request
//! handler), same rule as everywhere else: floats only at a conversion
//! boundary, ticks as the lingua franca on the wire.

pub const PRICE_REQUEST_TAG: u8 = 0x20;
pub const PRICE_RESPONSE_TAG: u8 = 0x21;

pub const PRICE_REQUEST_SIZE: usize = 8 + 8 + 8 + 8 + 8 + 1; // 41
pub const PRICE_RESPONSE_SIZE: usize = 8 * 6; // 48

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PriceRequest {
    pub spot_ticks: i64,
    pub strike_ticks: i64,
    pub rate: f64,
    pub vol: f64,
    pub time_to_expiry: f64,
    pub is_call: bool,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PriceResponse {
    pub price: f64,
    pub delta: f64,
    pub gamma: f64,
    pub vega: f64,
    pub theta: f64,
    pub rho: f64,
}

pub fn encode_request(msg: &PriceRequest) -> Vec<u8> {
    let mut buf = Vec::with_capacity(1 + PRICE_REQUEST_SIZE);
    buf.push(PRICE_REQUEST_TAG);
    buf.extend_from_slice(&msg.spot_ticks.to_be_bytes());
    buf.extend_from_slice(&msg.strike_ticks.to_be_bytes());
    buf.extend_from_slice(&msg.rate.to_be_bytes());
    buf.extend_from_slice(&msg.vol.to_be_bytes());
    buf.extend_from_slice(&msg.time_to_expiry.to_be_bytes());
    buf.push(u8::from(msg.is_call));
    buf
}

pub fn decode_request(payload: &[u8]) -> PriceRequest {
    PriceRequest {
        spot_ticks: i64::from_be_bytes(payload[0..8].try_into().unwrap()),
        strike_ticks: i64::from_be_bytes(payload[8..16].try_into().unwrap()),
        rate: f64::from_be_bytes(payload[16..24].try_into().unwrap()),
        vol: f64::from_be_bytes(payload[24..32].try_into().unwrap()),
        time_to_expiry: f64::from_be_bytes(payload[32..40].try_into().unwrap()),
        is_call: payload[40] != 0,
    }
}

pub fn encode_response(msg: &PriceResponse) -> Vec<u8> {
    let mut buf = Vec::with_capacity(1 + PRICE_RESPONSE_SIZE);
    buf.push(PRICE_RESPONSE_TAG);
    for v in [msg.price, msg.delta, msg.gamma, msg.vega, msg.theta, msg.rho] {
        buf.extend_from_slice(&v.to_be_bytes());
    }
    buf
}

pub fn decode_response(payload: &[u8]) -> PriceResponse {
    let get = |i: usize| f64::from_be_bytes(payload[i * 8..i * 8 + 8].try_into().unwrap());
    PriceResponse { price: get(0), delta: get(1), gamma: get(2), vega: get(3), theta: get(4), rho: get(5) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_round_trip() {
        let req = PriceRequest {
            spot_ticks: 30412,
            strike_ticks: 30800,
            rate: 0.03,
            vol: 0.22,
            time_to_expiry: 0.25,
            is_call: true,
        };
        let encoded = encode_request(&req);
        assert_eq!(encoded[0], PRICE_REQUEST_TAG);
        assert_eq!(encoded.len(), 1 + PRICE_REQUEST_SIZE);
        assert_eq!(decode_request(&encoded[1..]), req);
    }

    #[test]
    fn response_round_trip() {
        let resp = PriceResponse { price: 12.34, delta: 0.55, gamma: 0.01, vega: 8.2, theta: -3.1, rho: 4.4 };
        let encoded = encode_response(&resp);
        assert_eq!(encoded[0], PRICE_RESPONSE_TAG);
        assert_eq!(encoded.len(), 1 + PRICE_RESPONSE_SIZE);
        assert_eq!(decode_response(&encoded[1..]), resp);
    }
}

//! Binary wire protocol, mirroring engine/include/orderbook/protocol.hpp.
//!
//! See docs/socket-service-design-doc.md §4 for the framing rules this
//! implements a third time (after C++ and Python): 1-byte type tag, then a
//! fixed-size big-endian payload. Kept in lockstep with the other two by
//! round-trip tests against the same fixed byte layouts.
//!
//! This mirrors the full protocol for parity with the C++/Python
//! implementations, even though risk_engine (this crate's only binary)
//! doesn't exercise every message type yet - hence the blanket allow below
//! rather than piecemeal #[allow]s on each currently-unused item.
#![allow(dead_code)]

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum MsgType {
    NewOrder = 0x01,
    Cancel = 0x02,
    SubscribeFills = 0x03,
    Fill = 0x10,
    Ack = 0x11,
}

impl MsgType {
    fn from_tag(tag: u8) -> Option<MsgType> {
        match tag {
            0x01 => Some(MsgType::NewOrder),
            0x02 => Some(MsgType::Cancel),
            0x03 => Some(MsgType::SubscribeFills),
            0x10 => Some(MsgType::Fill),
            0x11 => Some(MsgType::Ack),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Side {
    Buy = 0,
    Sell = 1,
}

impl Side {
    pub fn from_u8(v: u8) -> Side {
        if v == 0 { Side::Buy } else { Side::Sell }
    }

    pub fn opposite(self) -> Side {
        match self {
            Side::Buy => Side::Sell,
            Side::Sell => Side::Buy,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum OrderType {
    Limit = 0,
    Market = 1,
}

pub const NEW_ORDER_SIZE: usize = 8 + 1 + 1 + 8 + 4; // 22
pub const CANCEL_SIZE: usize = 8;
pub const SUBSCRIBE_FILLS_SIZE: usize = 0;
pub const FILL_SIZE: usize = 8 + 8 + 1 + 8 + 4 + 8; // 37
pub const ACK_SIZE: usize = 8 + 1 + 1; // 10

/// Fixed payload size for a tag; None if the tag is unknown (caller should
/// treat that as a protocol violation). SubscribeFills legitimately has a
/// size of 0, which is why this isn't a plain size_t sentinel.
pub fn payload_size(tag: u8) -> Option<usize> {
    match MsgType::from_tag(tag)? {
        MsgType::NewOrder => Some(NEW_ORDER_SIZE),
        MsgType::Cancel => Some(CANCEL_SIZE),
        MsgType::SubscribeFills => Some(SUBSCRIBE_FILLS_SIZE),
        MsgType::Fill => Some(FILL_SIZE),
        MsgType::Ack => Some(ACK_SIZE),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ClientOrderMsg {
    pub client_order_id: u64,
    pub side: Side,
    pub order_type: OrderType,
    pub price_ticks: i64,
    pub quantity: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FillEvent {
    pub resting_order_id: u64,
    pub incoming_order_id: u64,
    pub resting_side: Side,
    pub price_ticks: i64,
    pub quantity: u32,
    pub timestamp: u64,
}

impl FillEvent {
    pub fn incoming_side(&self) -> Side {
        self.resting_side.opposite()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AckEvent {
    pub client_order_id: u64,
    pub request_type: u8,
    pub status: u8,
}

pub fn encode_new_order(msg: &ClientOrderMsg) -> Vec<u8> {
    let mut buf = Vec::with_capacity(1 + NEW_ORDER_SIZE);
    buf.push(MsgType::NewOrder as u8);
    buf.extend_from_slice(&msg.client_order_id.to_be_bytes());
    buf.push(msg.side as u8);
    buf.push(msg.order_type as u8);
    buf.extend_from_slice(&msg.price_ticks.to_be_bytes());
    buf.extend_from_slice(&msg.quantity.to_be_bytes());
    buf
}

pub fn decode_new_order(payload: &[u8]) -> ClientOrderMsg {
    ClientOrderMsg {
        client_order_id: u64::from_be_bytes(payload[0..8].try_into().unwrap()),
        side: Side::from_u8(payload[8]),
        order_type: if payload[9] == 0 { OrderType::Limit } else { OrderType::Market },
        price_ticks: i64::from_be_bytes(payload[10..18].try_into().unwrap()),
        quantity: u32::from_be_bytes(payload[18..22].try_into().unwrap()),
    }
}

pub fn encode_cancel(client_order_id: u64) -> Vec<u8> {
    let mut buf = Vec::with_capacity(1 + CANCEL_SIZE);
    buf.push(MsgType::Cancel as u8);
    buf.extend_from_slice(&client_order_id.to_be_bytes());
    buf
}

pub fn encode_subscribe_fills() -> Vec<u8> {
    vec![MsgType::SubscribeFills as u8]
}

pub fn decode_fill(payload: &[u8]) -> FillEvent {
    FillEvent {
        resting_order_id: u64::from_be_bytes(payload[0..8].try_into().unwrap()),
        incoming_order_id: u64::from_be_bytes(payload[8..16].try_into().unwrap()),
        resting_side: Side::from_u8(payload[16]),
        price_ticks: i64::from_be_bytes(payload[17..25].try_into().unwrap()),
        quantity: u32::from_be_bytes(payload[25..29].try_into().unwrap()),
        timestamp: u64::from_be_bytes(payload[29..37].try_into().unwrap()),
    }
}

pub fn decode_ack(payload: &[u8]) -> AckEvent {
    AckEvent {
        client_order_id: u64::from_be_bytes(payload[0..8].try_into().unwrap()),
        request_type: payload[8],
        status: payload[9],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_order_round_trip() {
        let msg = ClientOrderMsg {
            client_order_id: 0x0102030405060708,
            side: Side::Sell,
            order_type: OrderType::Limit,
            price_ticks: -0x1122334455667788,
            quantity: 0xAABBCCDD,
        };
        let encoded = encode_new_order(&msg);
        assert_eq!(encoded[0], MsgType::NewOrder as u8);
        // Big-endian: first payload byte is the MSB of client_order_id.
        assert_eq!(encoded[1], 0x01);
        assert_eq!(encoded[8], 0x08);

        let decoded = decode_new_order(&encoded[1..]);
        assert_eq!(decoded, msg);
    }

    #[test]
    fn fill_round_trip() {
        let fill = FillEvent {
            resting_order_id: 1,
            incoming_order_id: 2,
            resting_side: Side::Sell,
            price_ticks: -500,
            quantity: 60,
            timestamp: 999999,
        };
        let mut buf = Vec::new();
        buf.extend_from_slice(&fill.resting_order_id.to_be_bytes());
        buf.extend_from_slice(&fill.incoming_order_id.to_be_bytes());
        buf.push(fill.resting_side as u8);
        buf.extend_from_slice(&fill.price_ticks.to_be_bytes());
        buf.extend_from_slice(&fill.quantity.to_be_bytes());
        buf.extend_from_slice(&fill.timestamp.to_be_bytes());

        let decoded = decode_fill(&buf);
        assert_eq!(decoded, fill);
        assert_eq!(decoded.incoming_side(), Side::Buy);
    }

    #[test]
    fn payload_sizes_match_engine_layout() {
        assert_eq!(payload_size(MsgType::NewOrder as u8), Some(22));
        assert_eq!(payload_size(MsgType::Cancel as u8), Some(8));
        assert_eq!(payload_size(MsgType::SubscribeFills as u8), Some(0));
        assert_eq!(payload_size(MsgType::Fill as u8), Some(37));
        assert_eq!(payload_size(MsgType::Ack as u8), Some(10));
        assert_eq!(payload_size(0xFF), None);
    }
}

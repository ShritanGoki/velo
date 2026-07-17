//! FIX-style message parser. See docs/fix-parser-design-doc.md.
//!
//! A deliberately narrow subset of FIX 4.2: enough of `NewOrderSingle`
//! (`35=D`) to carry the same economic content this project's own
//! `NewOrder` wire message does (client order id, symbol, side,
//! quantity, order type, price), plus the two pieces of built-in error
//! detection FIX has that this project's own binary protocol doesn't
//! need (§4 of the design doc): `BodyLength` and `CheckSum` validation.
//!
//! Two parsers share the same field-splitting/validation logic
//! (`validate_and_parse_fields`) and differ only in the one thing this
//! module benchmarks: how each stores the parsed values. `parse_naive`
//! allocates an owned `String` per field into a `HashMap`; `parse_zero_copy`
//! only ever borrows `&str` slices from the input buffer. Keeping
//! everything else identical between the two is deliberate - it isolates
//! the one variable actually being measured, the same experimental
//! discipline Milestone 7 used to compare scalar vs. SIMD pricing.

use std::collections::HashMap;

pub const SOH: char = '\u{1}';

pub const TAG_BEGIN_STRING: u32 = 8;
pub const TAG_BODY_LENGTH: u32 = 9;
pub const TAG_MSG_TYPE: u32 = 35;
pub const TAG_CL_ORD_ID: u32 = 11;
pub const TAG_SYMBOL: u32 = 55;
pub const TAG_SIDE: u32 = 54;
pub const TAG_ORDER_QTY: u32 = 38;
pub const TAG_ORD_TYPE: u32 = 40;
pub const TAG_PRICE: u32 = 44;
pub const TAG_CHECKSUM: u32 = 10;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FixParseError {
    NotUtf8,
    EmptyMessage,
    MissingDelimiter,
    InvalidTag(String),
    MissingRequiredTag(u32),
    MalformedChecksumField,
    MalformedField(u32),
    ChecksumMismatch { expected: u8, computed: u8 },
    BodyLengthMismatch { declared: usize, actual: usize },
    UnexpectedMsgType(String),
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FixField<'a> {
    pub tag: u32,
    pub value: &'a str,
}

/// Shared validation core: SOH-splits the message, parses tags,
/// validates `CheckSum` and `BodyLength`, and confirms the required
/// `NewOrderSingle` tags are present (§6 of the design doc). Returns the
/// raw `(tag, &str)` pairs - callers decide how to store them.
fn validate_and_parse_fields(raw: &[u8]) -> Result<Vec<(u32, &str)>, FixParseError> {
    let text = std::str::from_utf8(raw).map_err(|_| FixParseError::NotUtf8)?;
    if text.is_empty() {
        return Err(FixParseError::EmptyMessage);
    }

    let soh_positions: Vec<usize> = text.match_indices(SOH).map(|(i, _)| i).collect();
    if soh_positions.len() < 3 {
        return Err(FixParseError::MissingDelimiter);
    }

    // ---- CheckSum: sum of every byte up to and including the SOH
    // immediately preceding the "10=" field. ----
    let marker = format!("{SOH}10=");
    let marker_pos = text.rfind(&marker).ok_or(FixParseError::MissingRequiredTag(TAG_CHECKSUM))?;
    let computed: u8 = text.as_bytes()[..=marker_pos].iter().fold(0u8, |acc, &b| acc.wrapping_add(b));

    let value_start = marker_pos + marker.len();
    if text.len() < value_start + 3 {
        return Err(FixParseError::MalformedChecksumField);
    }
    let checksum_str = &text[value_start..value_start + 3];
    let declared_checksum: u8 =
        checksum_str.parse::<u32>().map_err(|_| FixParseError::MalformedChecksumField)? as u8;
    if declared_checksum != computed {
        return Err(FixParseError::ChecksumMismatch { expected: declared_checksum, computed });
    }

    // ---- Fields ----
    let mut fields = Vec::new();
    for field in text.split(SOH) {
        if field.is_empty() {
            continue;
        }
        let (tag_str, value) = field.split_once('=').ok_or(FixParseError::MissingDelimiter)?;
        let tag = tag_str.parse::<u32>().map_err(|_| FixParseError::InvalidTag(tag_str.to_string()))?;
        fields.push((tag, value));
    }

    if fields.len() < 2 || fields[0].0 != TAG_BEGIN_STRING {
        return Err(FixParseError::MissingRequiredTag(TAG_BEGIN_STRING));
    }
    if fields[1].0 != TAG_BODY_LENGTH {
        return Err(FixParseError::MissingRequiredTag(TAG_BODY_LENGTH));
    }

    // ---- BodyLength: byte count from just after the "9=<len>" field's
    // SOH through and including the SOH just before "10=". ----
    let body_start = soh_positions[1] + 1;
    let declared_body_len: usize =
        fields[1].1.parse().map_err(|_| FixParseError::MalformedField(TAG_BODY_LENGTH))?;
    let actual_body_len = marker_pos - body_start + 1;
    if declared_body_len != actual_body_len {
        return Err(FixParseError::BodyLengthMismatch { declared: declared_body_len, actual: actual_body_len });
    }

    // ---- Required NewOrderSingle tags ----
    let get = |tag: u32| fields.iter().find(|(t, _)| *t == tag).map(|(_, v)| *v);

    let msg_type = get(TAG_MSG_TYPE).ok_or(FixParseError::MissingRequiredTag(TAG_MSG_TYPE))?;
    if msg_type != "D" {
        return Err(FixParseError::UnexpectedMsgType(msg_type.to_string()));
    }
    for required in [TAG_CL_ORD_ID, TAG_SYMBOL, TAG_SIDE, TAG_ORDER_QTY, TAG_ORD_TYPE] {
        get(required).ok_or(FixParseError::MissingRequiredTag(required))?;
    }
    if get(TAG_ORD_TYPE) == Some("2") && get(TAG_PRICE).is_none() {
        return Err(FixParseError::MissingRequiredTag(TAG_PRICE));
    }

    Ok(fields)
}

/// Allocates an owned `String` per field into a `HashMap` - the
/// straightforward, "just get it working" implementation.
pub fn parse_naive(raw: &[u8]) -> Result<HashMap<u32, String>, FixParseError> {
    let fields = validate_and_parse_fields(raw)?;
    Ok(fields.into_iter().map(|(tag, value)| (tag, value.to_string())).collect())
}

/// Identical validation; every field value is a `&str` slice borrowing
/// directly from `raw` - no per-field allocation.
pub fn parse_zero_copy(raw: &[u8]) -> Result<Vec<FixField<'_>>, FixParseError> {
    let fields = validate_and_parse_fields(raw)?;
    Ok(fields.into_iter().map(|(tag, value)| FixField { tag, value }).collect())
}

// ---------------------------------------------------------------------
// Benchmark/test fixture builder - not a first-class encoding API
// (docs/fix-parser-design-doc.md §9 explicitly defers that).
// ---------------------------------------------------------------------

pub struct NewOrderSingleFields<'a> {
    pub seq_num: u32,
    pub cl_ord_id: &'a str,
    pub symbol: &'a str,
    pub side: char,     // '1' buy, '2' sell
    pub order_qty: u32,
    pub ord_type: char, // '1' market, '2' limit
    pub price: Option<f64>,
}

pub fn build_new_order_single(fields: &NewOrderSingleFields) -> Vec<u8> {
    let mut body = String::new();
    body.push_str("35=D");
    body.push(SOH);
    body.push_str("49=SENDER");
    body.push(SOH);
    body.push_str("56=TARGET");
    body.push(SOH);
    body.push_str(&format!("34={}", fields.seq_num));
    body.push(SOH);
    body.push_str("52=20260101-00:00:00");
    body.push(SOH);
    body.push_str(&format!("11={}", fields.cl_ord_id));
    body.push(SOH);
    body.push_str(&format!("55={}", fields.symbol));
    body.push(SOH);
    body.push_str(&format!("54={}", fields.side));
    body.push(SOH);
    body.push_str(&format!("38={}", fields.order_qty));
    body.push(SOH);
    body.push_str(&format!("40={}", fields.ord_type));
    body.push(SOH);
    if let Some(price) = fields.price {
        body.push_str(&format!("44={price:.2}"));
        body.push(SOH);
    }
    body.push_str("59=0");
    body.push(SOH);

    let mut message = format!("8=FIX.4.2{SOH}9={}{SOH}", body.len());
    message.push_str(&body);

    let checksum: u8 = message.as_bytes().iter().fold(0u8, |acc, &b| acc.wrapping_add(b));
    message.push_str(&format!("10={checksum:03}"));
    message.push(SOH);

    message.into_bytes()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_fields() -> NewOrderSingleFields<'static> {
        NewOrderSingleFields {
            seq_num: 1,
            cl_ord_id: "ORDER1",
            symbol: "ESZ5",
            side: '1',
            order_qty: 10,
            ord_type: '2',
            price: Some(4800.0),
        }
    }

    #[test]
    fn well_formed_message_round_trips_through_both_parsers() {
        let raw = build_new_order_single(&sample_fields());

        let naive = parse_naive(&raw).expect("naive parse should succeed");
        assert_eq!(naive.get(&TAG_CL_ORD_ID).map(String::as_str), Some("ORDER1"));
        assert_eq!(naive.get(&TAG_SYMBOL).map(String::as_str), Some("ESZ5"));
        assert_eq!(naive.get(&TAG_SIDE).map(String::as_str), Some("1"));
        assert_eq!(naive.get(&TAG_ORDER_QTY).map(String::as_str), Some("10"));
        assert_eq!(naive.get(&TAG_PRICE).map(String::as_str), Some("4800.00"));

        let zero_copy = parse_zero_copy(&raw).expect("zero-copy parse should succeed");
        let get = |tag: u32| zero_copy.iter().find(|f| f.tag == tag).map(|f| f.value);
        assert_eq!(get(TAG_CL_ORD_ID), Some("ORDER1"));
        assert_eq!(get(TAG_SYMBOL), Some("ESZ5"));
        assert_eq!(get(TAG_PRICE), Some("4800.00"));
    }

    #[test]
    fn corrupted_checksum_is_rejected_by_both_parsers() {
        let mut raw = build_new_order_single(&sample_fields());
        // Flip a byte inside the symbol field (not touching BodyLength or
        // CheckSum themselves), which changes the computed checksum
        // without changing the byte count - isolates a checksum failure
        // from a body-length failure.
        let pos = raw.windows(4).position(|w| w == b"ESZ5").unwrap();
        raw[pos] = b'X';

        assert!(matches!(parse_naive(&raw), Err(FixParseError::ChecksumMismatch { .. })));
        assert!(matches!(parse_zero_copy(&raw), Err(FixParseError::ChecksumMismatch { .. })));
    }

    #[test]
    fn tampered_body_length_is_rejected() {
        let raw = build_new_order_single(&sample_fields());
        let text = String::from_utf8(raw).unwrap();

        // Overwrite the declared BodyLength value (tag 9) with a wrong
        // one, leaving the actual body untouched.
        let soh_positions: Vec<usize> = text.match_indices(SOH).map(|(i, _)| i).collect();
        let declared_start = soh_positions[0] + 1 + 2; // skip past "\x019="
        let declared_end = soh_positions[1];
        let mut corrupted = text.clone();
        corrupted.replace_range(declared_start..declared_end, "9999");

        // The BodyLength digits themselves fall within the summed
        // checksum region, so changing them also invalidates the
        // checksum trailer; recompute and overwrite it here so this test
        // isolates a pure BodyLength mismatch rather than tripping the
        // (unrelated, and checked first) checksum validation instead.
        let marker = format!("{SOH}10=");
        let marker_pos = corrupted.rfind(&marker).unwrap();
        let new_checksum: u8 =
            corrupted.as_bytes()[..=marker_pos].iter().fold(0u8, |acc, &b| acc.wrapping_add(b));
        let value_start = marker_pos + marker.len();
        corrupted.replace_range(value_start..value_start + 3, &format!("{new_checksum:03}"));

        let raw_corrupted = corrupted.into_bytes();
        assert!(matches!(parse_naive(&raw_corrupted), Err(FixParseError::BodyLengthMismatch { .. })));
    }

    #[test]
    fn missing_delimiter_is_rejected_not_panicked() {
        let raw = b"8=FIX.4.29=1235=D".to_vec(); // no SOH anywhere
        assert!(matches!(parse_naive(&raw), Err(FixParseError::MissingDelimiter)));
    }

    #[test]
    fn missing_required_tag_is_rejected() {
        // A message with a valid checksum/bodylength but no ClOrdID (11).
        let mut body = String::new();
        body.push_str("35=D");
        body.push(SOH);
        body.push_str("55=ESZ5");
        body.push(SOH);
        body.push_str("54=1");
        body.push(SOH);
        body.push_str("38=10");
        body.push(SOH);
        body.push_str("40=1");
        body.push(SOH);

        let mut message = format!("8=FIX.4.2{SOH}9={}{SOH}", body.len());
        message.push_str(&body);
        let checksum: u8 = message.as_bytes().iter().fold(0u8, |acc, &b| acc.wrapping_add(b));
        message.push_str(&format!("10={checksum:03}"));
        message.push(SOH);

        assert_eq!(parse_naive(message.as_bytes()), Err(FixParseError::MissingRequiredTag(TAG_CL_ORD_ID)));
    }

    #[test]
    fn naive_and_zero_copy_agree_across_generated_messages() {
        for (seq, side, ord_type, price) in [
            (1u32, '1', '2', Some(4800.0)),
            (2, '2', '2', Some(4795.25)),
            (3, '1', '1', None),
            (4, '2', '1', None),
        ] {
            let fields = NewOrderSingleFields {
                seq_num: seq,
                cl_ord_id: "ORDER",
                symbol: "ESZ5",
                side,
                order_qty: 25,
                ord_type,
                price,
            };
            let raw = build_new_order_single(&fields);

            let naive = parse_naive(&raw).unwrap();
            let zero_copy = parse_zero_copy(&raw).unwrap();

            assert_eq!(naive.len(), zero_copy.len());
            for field in &zero_copy {
                assert_eq!(naive.get(&field.tag).map(String::as_str), Some(field.value));
            }
        }
    }
}

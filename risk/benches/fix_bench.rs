//! Criterion benchmark comparing the naive (allocating) FIX parser
//! against the zero-copy one - the Milestone 9 half of this project's
//! benchmark discipline, same pattern as benches/pricing_bench.rs.
//! See docs/fix-parser-design-doc.md §5.

use criterion::{black_box, criterion_group, criterion_main, Criterion};
use risk_engine::fix::{build_new_order_single, parse_naive, parse_zero_copy, NewOrderSingleFields};

const BATCH_SIZE: usize = 4096;

fn make_batch(count: usize) -> Vec<Vec<u8>> {
    (0..count)
        .map(|i| {
            build_new_order_single(&NewOrderSingleFields {
                seq_num: i as u32,
                cl_ord_id: "ORDER",
                symbol: "ESZ5",
                side: if i % 2 == 0 { '1' } else { '2' },
                order_qty: 10 + (i % 50) as u32,
                ord_type: '2',
                price: Some(4800.0 + (i % 40) as f64 * 0.25),
            })
        })
        .collect()
}

fn bench_naive(c: &mut Criterion) {
    let batch = make_batch(BATCH_SIZE);
    c.bench_function("fix_parse_naive_4096_messages", |b| {
        b.iter(|| {
            for msg in &batch {
                black_box(parse_naive(black_box(msg)).unwrap());
            }
        })
    });
}

fn bench_zero_copy(c: &mut Criterion) {
    let batch = make_batch(BATCH_SIZE);
    c.bench_function("fix_parse_zero_copy_4096_messages", |b| {
        b.iter(|| {
            for msg in &batch {
                black_box(parse_zero_copy(black_box(msg)).unwrap());
            }
        })
    });
}

criterion_group!(benches, bench_naive, bench_zero_copy);
criterion_main!(benches);

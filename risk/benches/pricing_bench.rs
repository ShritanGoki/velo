//! Criterion benchmark comparing naive Rust scalar pricing against the
//! SIMD batch pricer - the Rust half of the three-way comparison in
//! docs/options-pricing-design-doc.md §6 (the Python naive baseline
//! lives separately in bench/naive_black_scholes.py, since Criterion
//! only benchmarks Rust).
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use risk_engine::pricing::{price_batch_simd, price_scalar, OptionParams};

const BATCH_SIZE: usize = 4096;

fn make_batch(count: usize) -> Vec<OptionParams> {
    (0..count)
        .map(|i| OptionParams {
            spot: 80.0 + (i % 40) as f64,
            strike: 100.0,
            rate: 0.03,
            vol: 0.2,
            time_to_expiry: 0.25 + (i % 8) as f64 * 0.125,
            is_call: i % 2 == 0,
        })
        .collect()
}

fn bench_naive_scalar(c: &mut Criterion) {
    let batch = make_batch(BATCH_SIZE);
    c.bench_function("naive_rust_scalar_4096_options", |b| {
        b.iter(|| {
            for p in &batch {
                black_box(price_scalar(black_box(p)));
            }
        })
    });
}

fn bench_simd_batch(c: &mut Criterion) {
    let batch = make_batch(BATCH_SIZE);
    c.bench_function("simd_rust_batch_4096_options", |b| {
        b.iter(|| black_box(price_batch_simd(black_box(&batch))))
    });
}

criterion_group!(benches, bench_naive_scalar, bench_simd_batch);
criterion_main!(benches);

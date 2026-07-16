//! Vectorized Black-Scholes option pricing + Greeks. See
//! docs/options-pricing-design-doc.md.
//!
//! Uses a hand-rolled polynomial approximation of the normal CDF (§4 of
//! the design doc) instead of `erf()`, specifically because that choice
//! is what makes the batch pricer vectorizable at all: `erf` is an
//! inherently scalar, branchy special function with no portable SIMD
//! form, while a polynomial is just arithmetic, which vectorizes
//! trivially across lanes.
//!
//! Call/put is handled uniformly via the standard "phi trick"
//! (phi = +1 for a call, -1 for a put) rather than branching, so the
//! SIMD batch pricer (§5) never needs to mix calls and puts within a
//! lane group differently - see price_lane_simd's use of `phi`.

use wide::f64x4;

const SQRT_2PI: f64 = 2.506_628_274_631_000_2;

#[derive(Debug, Clone, Copy)]
pub struct OptionParams {
    pub spot: f64,
    pub strike: f64,
    pub rate: f64,
    pub vol: f64,
    pub time_to_expiry: f64,
    pub is_call: bool,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Greeks {
    pub price: f64,
    pub delta: f64,
    pub gamma: f64,
    pub vega: f64,
    pub theta: f64,
    pub rho: f64,
}

// ---------------------------------------------------------------------
// Scalar (naive) pricer
// ---------------------------------------------------------------------

fn norm_pdf(x: f64) -> f64 {
    (-0.5 * x * x).exp() / SQRT_2PI
}

/// Abramowitz & Stegun 7.1.26 rational approximation of the standard
/// normal CDF. Documented max error ~1.5e-7 - negligible for pricing,
/// and, critically, branch-free arithmetic once the sign is factored
/// out via `sign * (cdf_abs - 0.5)` rather than an if/else on `x`'s sign.
fn norm_cdf(x: f64) -> f64 {
    let sign = if x < 0.0 { -1.0 } else { 1.0 };
    let x_abs = x.abs();

    const P: f64 = 0.231_641_9;
    const B1: f64 = 0.319_381_530;
    const B2: f64 = -0.356_563_782;
    const B3: f64 = 1.781_477_937;
    const B4: f64 = -1.821_255_978;
    const B5: f64 = 1.330_274_429;

    let t = 1.0 / (1.0 + P * x_abs);
    let poly = t * (B1 + t * (B2 + t * (B3 + t * (B4 + t * B5))));
    let cdf_abs = 1.0 - norm_pdf(x_abs) * poly;

    0.5 + sign * (cdf_abs - 0.5)
}

pub fn price_scalar(p: &OptionParams) -> Greeks {
    let phi = if p.is_call { 1.0 } else { -1.0 };
    let sqrt_t = p.time_to_expiry.sqrt();
    let vol_sqrt_t = p.vol * sqrt_t;

    let d1 = ((p.spot / p.strike).ln() + (p.rate + 0.5 * p.vol * p.vol) * p.time_to_expiry) / vol_sqrt_t;
    let d2 = d1 - vol_sqrt_t;

    let discount = (-p.rate * p.time_to_expiry).exp();
    let n_phi_d1 = norm_cdf(phi * d1);
    let n_phi_d2 = norm_cdf(phi * d2);
    let pdf_d1 = norm_pdf(d1);

    Greeks {
        price: phi * (p.spot * n_phi_d1 - p.strike * discount * n_phi_d2),
        delta: phi * n_phi_d1,
        gamma: pdf_d1 / (p.spot * vol_sqrt_t),
        vega: p.spot * pdf_d1 * sqrt_t,
        theta: -(p.spot * pdf_d1 * p.vol) / (2.0 * sqrt_t) - phi * p.rate * p.strike * discount * n_phi_d2,
        rho: phi * p.strike * p.time_to_expiry * discount * n_phi_d2,
    }
}

// ---------------------------------------------------------------------
// SIMD batch pricer
// ---------------------------------------------------------------------

fn norm_pdf_simd(x: f64x4) -> f64x4 {
    (x * x * f64x4::splat(-0.5)).exp() * f64x4::splat(1.0 / SQRT_2PI)
}

fn norm_cdf_simd(x: f64x4) -> f64x4 {
    let neg_mask = x.simd_lt(f64x4::splat(0.0));
    let sign = neg_mask.blend(f64x4::splat(-1.0), f64x4::splat(1.0));
    let x_abs = x.abs();

    let p = f64x4::splat(0.231_641_9);
    let b1 = f64x4::splat(0.319_381_530);
    let b2 = f64x4::splat(-0.356_563_782);
    let b3 = f64x4::splat(1.781_477_937);
    let b4 = f64x4::splat(-1.821_255_978);
    let b5 = f64x4::splat(1.330_274_429);

    let t = f64x4::splat(1.0) / (f64x4::splat(1.0) + p * x_abs);
    let poly = t * (b1 + t * (b2 + t * (b3 + t * (b4 + t * b5))));
    let cdf_abs = f64x4::splat(1.0) - norm_pdf_simd(x_abs) * poly;

    f64x4::splat(0.5) + sign * (cdf_abs - f64x4::splat(0.5))
}

/// Same formulas as price_scalar, run once across 4 lanes instead of
/// once per option. `phi` is packed per-lane (+1.0/-1.0), so a single
/// lane group can freely mix calls and puts with no branching.
#[allow(clippy::too_many_arguments)]
fn price_lane_simd(
    spot: f64x4, strike: f64x4, rate: f64x4, vol: f64x4, t: f64x4, phi: f64x4,
) -> [f64x4; 6] {
    let sqrt_t = t.sqrt();
    let vol_sqrt_t = vol * sqrt_t;

    let d1 = ((spot / strike).ln() + (rate + f64x4::splat(0.5) * vol * vol) * t) / vol_sqrt_t;
    let d2 = d1 - vol_sqrt_t;

    let discount = (rate * t * f64x4::splat(-1.0)).exp();
    let n_phi_d1 = norm_cdf_simd(phi * d1);
    let n_phi_d2 = norm_cdf_simd(phi * d2);
    let pdf_d1 = norm_pdf_simd(d1);

    let price = phi * (spot * n_phi_d1 - strike * discount * n_phi_d2);
    let delta = phi * n_phi_d1;
    let gamma = pdf_d1 / (spot * vol_sqrt_t);
    let vega = spot * pdf_d1 * sqrt_t;
    let theta =
        (spot * pdf_d1 * vol) / (f64x4::splat(2.0) * sqrt_t) * f64x4::splat(-1.0) - phi * rate * strike * discount * n_phi_d2;
    let rho = phi * strike * t * discount * n_phi_d2;

    [price, delta, gamma, vega, theta, rho]
}

/// Prices a batch of options 4 at a time. The whole batch is transposed
/// from array-of-structs (`&[OptionParams]`) into struct-of-arrays once
/// up front - not per 4-element chunk - since a small per-chunk gather
/// loop (indexing into `OptionParams` fields one lane at a time) turned
/// out to cost more than the vectorized math saved (see
/// docs/options-pricing-design-doc.md §6's benchmark writeup for the
/// measured before/after). Padding to a multiple of 4 repeats the last
/// real element into unused lanes - safe (no NaN/Inf), and those lanes'
/// results are simply never read back out.
pub fn price_batch_simd(params: &[OptionParams]) -> Vec<Greeks> {
    let n = params.len();
    if n == 0 {
        return Vec::new();
    }
    let padded_len = n.div_ceil(4) * 4;

    let mut spot = vec![0.0; padded_len];
    let mut strike = vec![0.0; padded_len];
    let mut rate = vec![0.0; padded_len];
    let mut vol = vec![0.0; padded_len];
    let mut t = vec![0.0; padded_len];
    let mut phi = vec![0.0; padded_len];

    for (i, p) in params.iter().enumerate() {
        spot[i] = p.spot;
        strike[i] = p.strike;
        rate[i] = p.rate;
        vol[i] = p.vol;
        t[i] = p.time_to_expiry;
        phi[i] = if p.is_call { 1.0 } else { -1.0 };
    }
    for i in n..padded_len {
        let last = n - 1;
        spot[i] = spot[last];
        strike[i] = strike[last];
        rate[i] = rate[last];
        vol[i] = vol[last];
        t[i] = t[last];
        phi[i] = phi[last];
    }

    let mut out_price = vec![0.0; padded_len];
    let mut out_delta = vec![0.0; padded_len];
    let mut out_gamma = vec![0.0; padded_len];
    let mut out_vega = vec![0.0; padded_len];
    let mut out_theta = vec![0.0; padded_len];
    let mut out_rho = vec![0.0; padded_len];

    for i in (0..padded_len).step_by(4) {
        let [price, delta, gamma, vega, theta, rho] = price_lane_simd(
            f64x4::new(spot[i..i + 4].try_into().unwrap()),
            f64x4::new(strike[i..i + 4].try_into().unwrap()),
            f64x4::new(rate[i..i + 4].try_into().unwrap()),
            f64x4::new(vol[i..i + 4].try_into().unwrap()),
            f64x4::new(t[i..i + 4].try_into().unwrap()),
            f64x4::new(phi[i..i + 4].try_into().unwrap()),
        );
        out_price[i..i + 4].copy_from_slice(&price.to_array());
        out_delta[i..i + 4].copy_from_slice(&delta.to_array());
        out_gamma[i..i + 4].copy_from_slice(&gamma.to_array());
        out_vega[i..i + 4].copy_from_slice(&vega.to_array());
        out_theta[i..i + 4].copy_from_slice(&theta.to_array());
        out_rho[i..i + 4].copy_from_slice(&rho.to_array());
    }

    (0..n)
        .map(|i| Greeks {
            price: out_price[i], delta: out_delta[i], gamma: out_gamma[i],
            vega: out_vega[i], theta: out_theta[i], rho: out_rho[i],
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx_eq(a: f64, b: f64, tol: f64) -> bool {
        (a - b).abs() <= tol
    }

    #[test]
    fn scalar_matches_known_textbook_value() {
        // S=100, K=100, r=5%, sigma=20%, T=1y -> call ~= 10.4506 (Hull).
        let p = OptionParams { spot: 100.0, strike: 100.0, rate: 0.05, vol: 0.2, time_to_expiry: 1.0, is_call: true };
        let g = price_scalar(&p);
        assert!(approx_eq(g.price, 10.4506, 1e-3), "got {}", g.price);
    }

    #[test]
    fn put_call_parity_holds_across_random_params() {
        // C - P = S - K*e^(-rT), for the SAME underlying parameters -
        // must hold exactly regardless of which specific numbers are
        // plugged in, unlike matching one reference table entry.
        let cases = [
            (100.0, 100.0, 0.05, 0.2, 1.0),
            (50.0, 55.0, 0.02, 0.35, 0.5),
            (200.0, 180.0, 0.1, 0.15, 2.0),
            (75.0, 75.0, 0.0, 0.4, 0.25),
        ];

        for (spot, strike, rate, vol, t) in cases {
            let call = price_scalar(&OptionParams { spot, strike, rate, vol, time_to_expiry: t, is_call: true });
            let put = price_scalar(&OptionParams { spot, strike, rate, vol, time_to_expiry: t, is_call: false });

            let expected = spot - strike * (-rate * t).exp();
            assert!(approx_eq(call.price - put.price, expected, 1e-6),
                    "parity failed for {:?}: call={} put={}", (spot, strike, rate, vol, t), call.price, put.price);
        }
    }

    #[test]
    fn greeks_satisfy_basic_sanity_bounds() {
        let mut prev_call_delta = f64::NEG_INFINITY;
        for spot in [50.0, 75.0, 100.0, 125.0, 150.0] {
            let call = price_scalar(&OptionParams { spot, strike: 100.0, rate: 0.05, vol: 0.2, time_to_expiry: 1.0, is_call: true });
            let put = price_scalar(&OptionParams { spot, strike: 100.0, rate: 0.05, vol: 0.2, time_to_expiry: 1.0, is_call: false });

            assert!((0.0..=1.0).contains(&call.delta), "call delta out of bounds: {}", call.delta);
            assert!((-1.0..=0.0).contains(&put.delta), "put delta out of bounds: {}", put.delta);
            assert!(call.gamma >= 0.0, "gamma should be non-negative: {}", call.gamma);
            assert!(call.vega >= 0.0, "vega should be non-negative: {}", call.vega);
            assert!(call.delta > prev_call_delta, "call delta should increase with spot");
            prev_call_delta = call.delta;
        }
    }

    #[test]
    fn batch_simd_matches_scalar_including_a_non_multiple_of_four_batch() {
        let mut params = Vec::new();
        for i in 0..13u32 {
            // 13, not a multiple of 4, deliberately exercises the padded
            // remainder-chunk path.
            let spot = 80.0 + i as f64 * 3.0;
            params.push(OptionParams {
                spot, strike: 100.0, rate: 0.03, vol: 0.25,
                time_to_expiry: 0.5 + i as f64 * 0.1, is_call: i % 2 == 0,
            });
        }

        let scalar_results: Vec<Greeks> = params.iter().map(price_scalar).collect();
        let batch_results = price_batch_simd(&params);

        assert_eq!(scalar_results.len(), batch_results.len());
        for (s, b) in scalar_results.iter().zip(batch_results.iter()) {
            assert!(approx_eq(s.price, b.price, 1e-9), "price mismatch: {} vs {}", s.price, b.price);
            assert!(approx_eq(s.delta, b.delta, 1e-9));
            assert!(approx_eq(s.gamma, b.gamma, 1e-9));
            assert!(approx_eq(s.vega, b.vega, 1e-9));
            assert!(approx_eq(s.theta, b.theta, 1e-9));
            assert!(approx_eq(s.rho, b.rho, 1e-9));
        }
    }

    #[test]
    fn norm_cdf_matches_exact_erf_based_reference() {
        // libm's erf is used ONLY here, as ground truth for this one
        // test - never in the pricer itself (see this module's doc
        // comment on why: erf can't vectorize).
        fn exact_norm_cdf(x: f64) -> f64 {
            0.5 * (1.0 + libm::erf(x / std::f64::consts::SQRT_2))
        }

        for x in [-4.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 4.0] {
            let approx = norm_cdf(x);
            let exact = exact_norm_cdf(x);
            assert!(approx_eq(approx, exact, 2e-7), "x={}: approx={} exact={}", x, approx, exact);
        }
    }
}

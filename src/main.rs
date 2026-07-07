use stocknews::{alpaca::run, config::Settings};
use tracing_subscriber::prelude::*;

#[tokio::main]
async fn main() -> stocknews::alpaca::AppResult<()> {
    // Sentry's reqwest client and our own reqwest/tungstenite clients pull in
    // different rustls crypto provider defaults (ring vs aws-lc-rs), so
    // rustls can no longer auto-select one. Install one explicitly before
    // any TLS connection is attempted.
    rustls::crypto::ring::default_provider()
        .install_default()
        .map_err(|_| "failed to install rustls crypto provider")?;

    let settings = Settings::from_env()?;

    let _sentry_guard = (!settings.sentry_dsn.is_empty()).then(|| {
        sentry::init(sentry::ClientOptions {
            dsn: settings.sentry_dsn.parse().ok(),
            debug: settings.sentry_debug,
            release: sentry::release_name!(),
            traces_sample_rate: 0.0,
            ..Default::default()
        })
    });

    tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer())
        .with(sentry::integrations::tracing::layer())
        .init();

    tokio::select! {
        () = run(&settings) => Ok(()),
        signal = tokio::signal::ctrl_c() => signal.map_err(Into::into),
    }
}

use stocknews::{alpaca::run, config::Settings};
use tracing_subscriber::prelude::*;

#[tokio::main]
async fn main() -> stocknews::alpaca::AppResult<()> {
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

    tracing::info!(
        git_sha = env!("GIT_SHA"),
        build_date = env!("BUILD_DATE"),
        "starting stocknews"
    );

    tokio::select! {
        () = run(&settings) => Ok(()),
        signal = tokio::signal::ctrl_c() => signal.map_err(Into::into),
    }
}

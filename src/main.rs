use stocknews::{alpaca::run, config::Settings};

#[tokio::main]
async fn main() -> stocknews::alpaca::AppResult<()> {
    tracing_subscriber::fmt::init();
    let settings = Settings::from_env()?;

    tokio::select! {
        () = run(&settings) => Ok(()),
        signal = tokio::signal::ctrl_c() => signal.map_err(Into::into),
    }
}

use stocknews::{alpaca::stream_news, config::Settings};

#[tokio::main]
async fn main() -> stocknews::alpaca::AppResult<()> {
    tracing_subscriber::fmt::init();
    let settings = Settings::from_env()?;

    tokio::select! {
        result = stream_news(&settings) => result,
        signal = tokio::signal::ctrl_c() => {
            signal?;
            Ok(())
        }
    }
}

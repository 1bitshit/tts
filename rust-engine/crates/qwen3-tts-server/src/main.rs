mod reference;
use axum::{
    Json, Router,
    body::Body,
    extract::State,
    http::{StatusCode, header},
    response::Response,
    routing::{get, post},
};
use clap::Parser;
use futures_util::StreamExt;
use qwen3_tts_core::{SAMPLE_RATE, backend::TtsBackend, markup, request::SpeechRequest};
use reference::ReferenceBackend;
use serde_json::{Value, json};
use std::{net::SocketAddr, sync::Arc};
use tower_http::{cors::CorsLayer, trace::TraceLayer};

#[derive(Parser)]
struct Args {
    #[arg(long, env = "RUST_TTS_LISTEN", default_value = "127.0.0.1:8030")]
    listen: SocketAddr,
    #[arg(long, env = "C_TTS_UPSTREAM", default_value = "http://127.0.0.1:8020")]
    upstream: String,
    #[arg(long, env = "RUST_TTS_CONNECT_TIMEOUT_SECONDS", default_value_t = 5)]
    connect_timeout_seconds: u64,
    #[arg(long, env = "RUST_TTS_REQUEST_TIMEOUT_SECONDS", default_value_t = 600)]
    request_timeout_seconds: u64,
    #[arg(long, env = "RUST_TTS_MAX_CONCURRENT", default_value_t = 1)]
    max_concurrent: usize,
}

struct AppState {
    backend: Arc<dyn TtsBackend>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    let args = Args::parse();
    let backend = ReferenceBackend::new(
        args.upstream,
        std::time::Duration::from_secs(args.connect_timeout_seconds),
        std::time::Duration::from_secs(args.request_timeout_seconds),
        args.max_concurrent,
    )?;
    let state = Arc::new(AppState {
        backend: Arc::new(backend),
    });
    let app = Router::new()
        .route("/v1/health", get(health))
        .route("/v1/speakers", get(speakers))
        .route("/v1/capabilities", get(capabilities))
        .route("/v1/tts", post(tts))
        .route("/v1/audio/speech", post(tts))
        .route("/v1/tts/stream", post(tts_stream))
        .route("/api/v1/engine/speech", post(tts))
        .route("/api/v1/engine/speech/stream", post(tts_stream))
        .route("/api/v1/engine/speakers", get(speakers))
        .route("/api/v1/engine/capabilities", get(capabilities))
        .route("/api/v1/engine/markup/inspect", post(inspect_markup))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(args.listen).await?;
    tracing::info!(%args.listen, max_concurrent = args.max_concurrent, "Rust TTS gateway listening");
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

async fn health(State(s): State<Arc<AppState>>) -> (StatusCode, Json<Value>) {
    let health = s.backend.health().await;
    let status = if health.ready {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    (
        status,
        Json(
            json!({"status": if health.ready {"ok"} else {"unavailable"}, "engine":"rust", "backend":health.name, "detail":health.detail}),
        ),
    )
}
async fn capabilities() -> Json<Value> {
    Json(json!({
        "engine":"qwen3-tts-rs",
        "sample_rate":SAMPLE_RATE,
        "formats":["wav","pcm","base64"],
        "languages":["German","English","Chinese","Japanese","Korean","French","Russian","Portuguese","Spanish","Italian"],
        "features":["story","debate","inline-emotions","pauses","laugh","sigh","rate","volume","seed","pcm-streaming","openai-audio-alias"],
        "native_rust":["api","validation","markup","routing","streaming"],
        "reference_backend":["talker","code-predictor","speech-decoder","cuda"]
    }))
}

async fn inspect_markup(
    Json(req): Json<SpeechRequest>,
) -> Result<Json<Value>, (StatusCode, String)> {
    req.validate()
        .map_err(|e| (StatusCode::BAD_REQUEST, e.into()))?;
    Ok(Json(json!({"spans": markup::parse(&req.text)})))
}
async fn speakers(State(s): State<Arc<AppState>>) -> Result<Json<Value>, (StatusCode, String)> {
    s.backend
        .speakers()
        .await
        .map(Json)
        .map_err(internal_bad_gateway)
}
async fn tts(
    State(s): State<Arc<AppState>>,
    Json(req): Json<SpeechRequest>,
) -> Result<Response, (StatusCode, String)> {
    req.validate()
        .map_err(|e| (StatusCode::BAD_REQUEST, e.into()))?;
    let bytes = s
        .backend
        .synthesize(&req)
        .await
        .map_err(internal_bad_gateway)?;
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "audio/wav")
        .body(Body::from(bytes))
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))
}
async fn tts_stream(
    State(s): State<Arc<AppState>>,
    Json(req): Json<SpeechRequest>,
) -> Result<Response, (StatusCode, String)> {
    req.validate()
        .map_err(|e| (StatusCode::BAD_REQUEST, e.into()))?;
    let stream = s
        .backend
        .stream(&req)
        .await
        .map_err(internal_bad_gateway)?
        .map(|r| r.map_err(std::io::Error::other));
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "audio/L16;rate=24000;channels=1")
        .header("X-Audio-Format", "s16le")
        .header("X-Sample-Rate", SAMPLE_RATE.to_string())
        .body(Body::from_stream(stream))
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))
}
fn internal_bad_gateway(error: anyhow::Error) -> (StatusCode, String) {
    tracing::error!(%error, "TTS backend request failed");
    (StatusCode::BAD_GATEWAY, error.to_string())
}

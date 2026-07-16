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
use qwen3_tts_core::{SAMPLE_RATE, markup, request::SpeechRequest};
use serde_json::{Value, json};
use std::{net::SocketAddr, sync::Arc};
use tower_http::{cors::CorsLayer, trace::TraceLayer};

#[derive(Parser)]
struct Args {
    #[arg(long, env = "RUST_TTS_LISTEN", default_value = "127.0.0.1:8030")]
    listen: SocketAddr,
    #[arg(long, env = "C_TTS_UPSTREAM", default_value = "http://127.0.0.1:8020")]
    upstream: String,
}

struct AppState {
    client: reqwest::Client,
    upstream: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    let args = Args::parse();
    let state = Arc::new(AppState {
        client: reqwest::Client::new(),
        upstream: args.upstream,
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
    tracing::info!(%args.listen, "Rust TTS gateway listening");
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

async fn health(State(s): State<Arc<AppState>>) -> (StatusCode, Json<Value>) {
    match s
        .client
        .get(format!("{}/v1/health", s.upstream))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => (
            StatusCode::OK,
            Json(json!({"status":"ok","engine":"rust-gateway","inference":"c-reference"})),
        ),
        _ => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"status":"unavailable","engine":"rust-gateway"})),
        ),
    }
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
async fn speakers(State(s): State<Arc<AppState>>) -> Result<Response, StatusCode> {
    proxy_json(&s, "/v1/speakers", None).await
}
async fn tts(
    State(s): State<Arc<AppState>>,
    Json(req): Json<SpeechRequest>,
) -> Result<Response, (StatusCode, String)> {
    req.validate()
        .map_err(|e| (StatusCode::BAD_REQUEST, e.into()))?;
    proxy_json(&s, "/v1/tts", Some(req))
        .await
        .map_err(|e| (e, "upstream failed".into()))
}
async fn tts_stream(
    State(s): State<Arc<AppState>>,
    Json(req): Json<SpeechRequest>,
) -> Result<Response, (StatusCode, String)> {
    req.validate()
        .map_err(|e| (StatusCode::BAD_REQUEST, e.into()))?;
    let upstream = s
        .client
        .post(format!("{}/v1/tts/stream", s.upstream))
        .json(&req)
        .send()
        .await
        .map_err(|e| (StatusCode::BAD_GATEWAY, e.to_string()))?;
    let status = upstream.status();
    let stream = upstream
        .bytes_stream()
        .map(|result| result.map_err(std::io::Error::other));
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "audio/L16;rate=24000;channels=1")
        .body(Body::from_stream(stream))
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))
}
async fn proxy_json(
    s: &AppState,
    path: &str,
    req: Option<SpeechRequest>,
) -> Result<Response, StatusCode> {
    let builder = if let Some(req) = req {
        s.client.post(format!("{}{}", s.upstream, path)).json(&req)
    } else {
        s.client.get(format!("{}{}", s.upstream, path))
    };
    let upstream = builder.send().await.map_err(|_| StatusCode::BAD_GATEWAY)?;
    let status = upstream.status();
    let content_type = upstream.headers().get(header::CONTENT_TYPE).cloned();
    let bytes = upstream
        .bytes()
        .await
        .map_err(|_| StatusCode::BAD_GATEWAY)?;
    let mut response = Response::builder().status(status);
    if let Some(value) = content_type {
        response = response.header(header::CONTENT_TYPE, value);
    }
    response
        .body(Body::from(bytes))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

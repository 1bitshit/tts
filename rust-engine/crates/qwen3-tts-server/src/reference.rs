use anyhow::{Context, bail, ensure};
use async_trait::async_trait;
use bytes::Bytes;
use futures_util::StreamExt;
use qwen3_tts_core::{
    backend::{AudioStream, BackendHealth, TtsBackend},
    request::SpeechRequest,
};
use std::sync::Arc;
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

pub struct ReferenceBackend {
    client: reqwest::Client,
    base_url: String,
    permits: Arc<Semaphore>,
}

impl ReferenceBackend {
    pub fn new(
        base_url: String,
        connect_timeout: std::time::Duration,
        request_timeout: std::time::Duration,
        max_concurrent: usize,
    ) -> anyhow::Result<Self> {
        ensure!(
            max_concurrent > 0,
            "max_concurrent must be greater than zero"
        );
        let client = reqwest::Client::builder()
            .connect_timeout(connect_timeout)
            .timeout(request_timeout)
            .pool_max_idle_per_host(max_concurrent)
            .build()?;
        Ok(Self {
            client,
            base_url: base_url.trim_end_matches('/').to_owned(),
            permits: Arc::new(Semaphore::new(max_concurrent)),
        })
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base_url, path)
    }

    async fn permit(&self) -> anyhow::Result<OwnedSemaphorePermit> {
        self.permits
            .clone()
            .acquire_owned()
            .await
            .context("backend concurrency limiter closed")
    }
}

#[async_trait]
impl TtsBackend for ReferenceBackend {
    async fn health(&self) -> BackendHealth {
        match self.client.get(self.url("/v1/health")).send().await {
            Ok(r) if r.status().is_success() => BackendHealth {
                ready: true,
                name: "c-reference",
                detail: None,
            },
            Ok(r) => BackendHealth {
                ready: false,
                name: "c-reference",
                detail: Some(format!("HTTP {}", r.status())),
            },
            Err(e) => BackendHealth {
                ready: false,
                name: "c-reference",
                detail: Some(e.to_string()),
            },
        }
    }

    async fn speakers(&self) -> anyhow::Result<serde_json::Value> {
        let r = self
            .client
            .get(self.url("/v1/speakers"))
            .send()
            .await?
            .error_for_status()?;
        Ok(r.json().await?)
    }

    async fn synthesize(&self, request: &SpeechRequest) -> anyhow::Result<Bytes> {
        let _permit = self.permit().await?;
        let r = self
            .client
            .post(self.url("/v1/tts"))
            .json(request)
            .send()
            .await?
            .error_for_status()?;
        let bytes = r.bytes().await?;
        ensure!(
            bytes.len() >= 44 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WAVE",
            "upstream returned invalid WAV data"
        );
        Ok(bytes)
    }

    async fn stream(&self, request: &SpeechRequest) -> anyhow::Result<AudioStream> {
        let permit = self.permit().await?;
        let r = self
            .client
            .post(self.url("/v1/tts/stream"))
            .json(request)
            .send()
            .await?
            .error_for_status()?;
        if !r.status().is_success() {
            bail!("upstream returned {}", r.status());
        }
        let stream = r
            .bytes_stream()
            .map(|item| item.context("upstream stream failed"));
        let stream = PermitStream {
            inner: Box::pin(stream),
            _permit: permit,
        };
        Ok(Box::pin(stream))
    }
}

struct PermitStream {
    inner: AudioStream,
    _permit: OwnedSemaphorePermit,
}

impl futures_util::Stream for PermitStream {
    type Item = anyhow::Result<Bytes>;

    fn poll_next(
        mut self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Self::Item>> {
        self.inner.as_mut().poll_next(cx)
    }
}

use anyhow::{Context, bail};
use async_trait::async_trait;
use bytes::Bytes;
use futures_util::StreamExt;
use qwen3_tts_core::{
    backend::{AudioStream, BackendHealth, TtsBackend},
    request::SpeechRequest,
};

pub struct ReferenceBackend {
    client: reqwest::Client,
    base_url: String,
}

impl ReferenceBackend {
    pub fn new(base_url: String, timeout: std::time::Duration) -> anyhow::Result<Self> {
        let client = reqwest::Client::builder().timeout(timeout).build()?;
        Ok(Self {
            client,
            base_url: base_url.trim_end_matches('/').to_owned(),
        })
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base_url, path)
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
        let r = self
            .client
            .post(self.url("/v1/tts"))
            .json(request)
            .send()
            .await?
            .error_for_status()?;
        Ok(r.bytes().await?)
    }

    async fn stream(&self, request: &SpeechRequest) -> anyhow::Result<AudioStream> {
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
        Ok(Box::pin(stream))
    }
}
